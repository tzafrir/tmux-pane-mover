"""tmux-pane-mover: drag-and-drop tmux pane rearranger.

Drop zones while dragging:
  - Center of a pane  -> swap contents  (tmux swap-pane)
  - Edge of a pane    -> split there    (tmux join-pane)
  - Screen edge strip -> outermost col/row split (tmux join-pane)
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

from textual.app import App, ComposeResult
from textual.events import Leave, MouseDown, MouseMove, MouseUp, Resize
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Footer
from rich.segment import Segment
from rich.style import Style


# -- constants -----------------------------------------------------------------

SCREEN_EDGE_W  = 4     # columns from screen edge  ->  full-column split zone
SCREEN_EDGE_H  = 2     # rows    from screen edge  ->  full-row    split zone
PANE_EDGE_FRAC = 0.28  # fraction of pane dimension that counts as "pane edge"
FOOTER_H       = 1     # height of the footer widget in rows

BOX = {"tl": "\u256d", "tr": "\u256e", "bl": "\u2570", "br": "\u256f", "h": "\u2500", "v": "\u2502"}

# (human label, icon)
ACTION_INFO: dict[str, tuple[str, str]] = {
    "swap":         ("swap",            "\u21c4"),
    "screen_left":  ("new left col",    "\u25c0"),
    "screen_right": ("new right col",   "\u25b6"),
    "screen_top":   ("new top row",     "\u25b2"),
    "screen_bot":   ("new bottom row",  "\u25bc"),
    "pane_left":    ("split left",      "\u255e"),
    "pane_right":   ("split right",     "\u2561"),
    "pane_top":     ("split above",     "\u2565"),
    "pane_bot":     ("split below",     "\u2568"),
}

# -- data ----------------------------------------------------------------------

@dataclass
class Pane:
    id: str
    left: int
    top: int
    width: int
    height: int
    title: str
    active: bool


def get_panes() -> tuple[list[Pane], int, int]:
    try:
        win_w, win_h = map(
            int,
            subprocess.run(
                ["tmux", "display-message", "-p", "#{window_width} #{window_height}"],
                capture_output=True, text=True, check=True,
            ).stdout.split(),
        )
        lines = subprocess.run(
            [
                "tmux", "list-panes", "-F",
                "#{pane_id}\t#{pane_left}\t#{pane_top}\t"
                "#{pane_width}\t#{pane_height}\t#{pane_title}\t#{pane_active}",
            ],
            capture_output=True, text=True, check=True,
        ).stdout.strip().splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: tmux not found or not running inside a tmux session.", file=sys.stderr)
        sys.exit(1)

    panes = []
    for line in lines:
        p = line.split("\t")
        if len(p) < 7:
            continue
        panes.append(Pane(
            id=p[0],
            left=int(p[1]), top=int(p[2]),
            width=int(p[3]), height=int(p[4]),
            title=(p[5] or p[0])[:24],
            active=p[6] == "1",
        ))
    return panes, win_w, win_h


# -- types ---------------------------------------------------------------------

Canvas = list[list[tuple[str, Style]]]

# -- styles --------------------------------------------------------------------

S_NORMAL  = Style(color="#4a9eff")
S_ACTIVE  = Style(color="#00ff88", bold=True)
S_HOVER   = Style(color="#ffcc00", bold=True)
S_DRAG    = Style(color="#ff66ff", bold=True)
S_EDGE_HL = Style(color="#00ffcc", bold=True)   # pane-edge split indicator

F_NORMAL  = Style(bgcolor="#0d1b2a", color="#8ab4d4")
F_ACTIVE  = Style(bgcolor="#0d2b1a", color="#aaffcc")
F_HOVER   = Style(bgcolor="#2b2500", color="#ffe066")
F_DRAG    = Style(bgcolor="#2a0d2a", color="#ffaaff")

ZONE_DIM  = Style(bgcolor="#000d22", color="#1a3a66")
ZONE_LIT  = Style(bgcolor="#002266", color="#66aaff", bold=True)
LABEL_S   = Style(bgcolor="#440066", color="#ff88ff", bold=True)


# -- widget --------------------------------------------------------------------

class PaneMap(Widget):
    DEFAULT_CSS = "PaneMap { width: 1fr; height: 1fr; }"

    def __init__(self, panes: list[Pane], win_w: int, win_h: int) -> None:
        super().__init__()
        self.panes = panes
        self.win_w = win_w
        self.win_h = win_h
        self._drag: Pane | None = None
        self._drag_x = 0
        self._drag_y = 0
        self._hover: Pane | None = None
        self._drop: tuple[str, Pane | None] | None = None
        self._canvas: Canvas | None = None

    # -- coordinate helpers ----------------------------------------------------

    def _sx(self, tx: int) -> int:
        return round(tx * (self.size.width - 2) / max(1, self.win_w)) + 1

    def _sy(self, ty: int) -> int:
        return round(ty * (self.size.height - 2) / max(1, self.win_h)) + 1

    def _sw(self, tw: int) -> int:
        return max(5, round(tw * (self.size.width - 2) / max(1, self.win_w)))

    def _sh(self, th: int) -> int:
        return max(3, round(th * (self.size.height - 2) / max(1, self.win_h)))

    def _pane_at(self, x: int, y: int, skip_drag: bool = True) -> Pane | None:
        for pane in self.panes:
            if skip_drag and pane is self._drag:
                continue
            px, py = self._sx(pane.left), self._sy(pane.top)
            pw, ph = self._sw(pane.width), self._sh(pane.height)
            if px <= x < px + pw and py <= y < py + ph:
                return pane
        return None

    # -- drop-zone logic -------------------------------------------------------

    def _get_drop(self, x: int, y: int) -> tuple[str, Pane | None] | None:
        """Return (action_kind, target_pane_or_None) for cursor position."""
        if not self._drag:
            return None
        cw, ch = self.size.width, self.size.height

        # Screen edges take priority
        if x < SCREEN_EDGE_W:
            return ("screen_left", None)
        if x >= cw - SCREEN_EDGE_W:
            return ("screen_right", None)
        if y < SCREEN_EDGE_H:
            return ("screen_top", None)
        if y >= ch - FOOTER_H - SCREEN_EDGE_H:
            return ("screen_bot", None)

        # Pane edge vs. center
        pane = self._pane_at(x, y)
        if pane:
            px, py = self._sx(pane.left), self._sy(pane.top)
            pw, ph = self._sw(pane.width), self._sh(pane.height)
            rx = (x - px) / max(1, pw - 1)
            ry = (y - py) / max(1, ph - 1)
            if   rx < PANE_EDGE_FRAC:         return ("pane_left",  pane)
            elif rx > 1 - PANE_EDGE_FRAC:     return ("pane_right", pane)
            elif ry < PANE_EDGE_FRAC:         return ("pane_top",   pane)
            elif ry > 1 - PANE_EDGE_FRAC:     return ("pane_bot",   pane)
            else:                             return ("swap",        pane)

        return None

    # -- tmux operations -------------------------------------------------------

    def _apply_drop(self, kind: str, target: Pane | None) -> None:
        if self._drag is None:
            return
        src = self._drag.id
        # Any non-drag pane -- needed as a window anchor for -f operations
        anchor = next((p.id for p in self.panes if p is not self._drag), None)

        def run_tmux(*args: str) -> None:
            result = subprocess.run(
                ["tmux", *args],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                msg = result.stderr.strip() or f"tmux command failed: {' '.join(args)}"
                self.app.notify(msg, severity="error", timeout=4)

        if kind == "swap" and target:
            run_tmux("swap-pane", "-s", src, "-t", target.id)

        # Screen edges: -f wraps the entire existing layout, producing a true
        # full-height column or full-width row regardless of which pane is targeted.
        elif kind == "screen_left"  and anchor: run_tmux("join-pane", "-d", "-h", "-f", "-b", "-s", src, "-t", anchor)
        elif kind == "screen_right" and anchor: run_tmux("join-pane", "-d", "-h", "-f",       "-s", src, "-t", anchor)
        elif kind == "screen_top"   and anchor: run_tmux("join-pane", "-d", "-v", "-f", "-b", "-s", src, "-t", anchor)
        elif kind == "screen_bot"   and anchor: run_tmux("join-pane", "-d", "-v", "-f",       "-s", src, "-t", anchor)

        # Pane edges: split that specific pane (no -f)
        elif kind == "pane_left"  and target:  run_tmux("join-pane", "-d", "-h", "-b", "-s", src, "-t", target.id)
        elif kind == "pane_right" and target:  run_tmux("join-pane", "-d", "-h",       "-s", src, "-t", target.id)
        elif kind == "pane_top"   and target:  run_tmux("join-pane", "-d", "-v", "-b", "-s", src, "-t", target.id)
        elif kind == "pane_bot"   and target:  run_tmux("join-pane", "-d", "-v",       "-s", src, "-t", target.id)

    # -- canvas building -------------------------------------------------------

    def _build_canvas(self) -> Canvas:
        cw, ch = self.size.width, self.size.height
        canvas: Canvas = [
            [(" ", Style.null())] * cw for _ in range(ch)
        ]

        drop_kind, drop_pane = self._drop if self._drop else (None, None)

        # 1. Pane boxes
        for pane in self.panes:
            if pane is self._drag:
                continue

            active_edge: str | None = None
            if self._drag and pane is drop_pane:
                active_edge = {
                    "pane_left": "left", "pane_right": "right",
                    "pane_top":  "top",  "pane_bot":   "bot",
                    "swap":      "center",
                }.get(drop_kind)  # type: ignore[arg-type]

            if self._drag:
                bs = (S_EDGE_HL if active_edge and active_edge != "center"
                      else S_HOVER  if active_edge == "center"
                      else S_ACTIVE if pane.active else S_NORMAL)
                fs = (F_HOVER  if active_edge
                      else F_ACTIVE if pane.active else F_NORMAL)
            else:
                bs = S_HOVER if pane is self._hover else (S_ACTIVE if pane.active else S_NORMAL)
                fs = F_HOVER if pane is self._hover else (F_ACTIVE if pane.active else F_NORMAL)

            self._draw_box(
                canvas,
                self._sx(pane.left), self._sy(pane.top),
                self._sw(pane.width), self._sh(pane.height),
                f"{pane.id} {pane.title}", bs, fs,
                active_edge=active_edge,
            )

        # 2. Screen-edge drop zones (only while dragging)
        if self._drag:
            self._draw_screen_zones(canvas, drop_kind)

        # 3. Drag ghost
        if self._drag:
            pw = self._sw(self._drag.width)
            ph = self._sh(self._drag.height)
            gx = self._drag_x - pw // 2
            gy = self._drag_y - ph // 2
            self._draw_box(
                canvas, gx, gy, pw, ph,
                f"\u2827 {self._drag.id} {self._drag.title}", S_DRAG, F_DRAG,
            )

            # 4. Action label just inside the ghost's top border
            if self._drop:
                lbl, icon = ACTION_INFO.get(drop_kind, (drop_kind, ""))  # type: ignore[arg-type]
                text = f" {icon} {lbl} "
                for i, char in enumerate(text):
                    cx, cy = gx + 1 + i, gy + 1
                    if 0 <= cx < cw and 0 <= cy < ch:
                        canvas[cy][cx] = (char, LABEL_S)

        return canvas

    def _draw_screen_zones(
        self, canvas: Canvas, active_kind: str | None,
    ) -> None:
        cw, ch = self.size.width, self.size.height

        def fill(x: int, y: int, w: int, h: int, char: str, s: Style) -> None:
            for dy in range(h):
                for dx in range(w):
                    cx, cy = x + dx, y + dy
                    if 0 <= cx < cw and 0 <= cy < ch - FOOTER_H:
                        canvas[cy][cx] = (char, s)

        zones = [
            ("screen_left",  0,                          0,                         SCREEN_EDGE_W, ch - FOOTER_H,            "\u25c0"),
            ("screen_right", cw - SCREEN_EDGE_W,         0,                         SCREEN_EDGE_W, ch - FOOTER_H,            "\u25b6"),
            ("screen_top",   0,                          0,                         cw,            SCREEN_EDGE_H,            "\u25b2"),
            ("screen_bot",   0,                          ch - FOOTER_H - SCREEN_EDGE_H, cw,       SCREEN_EDGE_H,            "\u25bc"),
        ]
        for kind, zx, zy, zw, zh, char in zones:
            fill(zx, zy, zw, zh, char, ZONE_LIT if kind == active_kind else ZONE_DIM)

    def _draw_box(
        self,
        canvas: Canvas,
        x: int, y: int, w: int, h: int,
        title: str,
        bs: Style, fs: Style,
        active_edge: str | None = None,
    ) -> None:
        cw, ch = self.size.width, self.size.height
        for dy in range(h):
            for dx in range(w):
                cx, cy = x + dx, y + dy
                if not (0 <= cx < cw and 0 <= cy < ch):
                    continue
                top    = dy == 0
                bottom = dy == h - 1
                left   = dx == 0
                right  = dx == w - 1
                hi = (
                    (active_edge == "left"   and left)   or
                    (active_edge == "right"  and right)  or
                    (active_edge == "top"    and top)    or
                    (active_edge == "bot"    and bottom)
                )
                cur_bs = S_EDGE_HL if hi else bs
                if   top    and left:  c, s = BOX["tl"], cur_bs
                elif top    and right: c, s = BOX["tr"], cur_bs
                elif bottom and left:  c, s = BOX["bl"], cur_bs
                elif bottom and right: c, s = BOX["br"], cur_bs
                elif top    or bottom: c, s = BOX["h"],  cur_bs
                elif left   or right:  c, s = BOX["v"],  cur_bs
                else:                  c, s = " ",       fs
                canvas[cy][cx] = (c, s)

        if w > 5 and 0 <= y < ch:
            label = f" {title[:w - 5]} "
            for i, char in enumerate(label):
                cx = x + 2 + i
                if 0 <= cx < cw:
                    canvas[y][cx] = (char, bs)

    # -- rendering -------------------------------------------------------------

    def _invalidate(self) -> None:
        self._canvas = None
        self.refresh()

    def render_line(self, y: int) -> Strip:
        if self._canvas is None:
            if self.size.width == 0 or self.size.height == 0:
                return Strip.blank(0)
            self._canvas = self._build_canvas()
        if y >= len(self._canvas):
            return Strip.blank(self.size.width)
        return Strip([Segment(ch, st) for ch, st in self._canvas[y]])

    # -- events ----------------------------------------------------------------

    def on_resize(self, _: Resize) -> None:
        self._invalidate()

    def on_mouse_down(self, event: MouseDown) -> None:
        pane = self._pane_at(event.x, event.y, skip_drag=False)
        if pane:
            self._drag = pane
            self._drag_x = event.x
            self._drag_y = event.y
            self._hover = None
            self._drop = None
            self.capture_mouse()
            self._invalidate()

    def on_mouse_move(self, event: MouseMove) -> None:
        if self._drag:
            self._drag_x = event.x
            self._drag_y = event.y
            self._drop = self._get_drop(event.x, event.y)
            self._invalidate()
        else:
            new_hover = self._pane_at(event.x, event.y)
            if new_hover is not self._hover:
                self._hover = new_hover
                self._invalidate()

    def on_mouse_up(self, event: MouseUp) -> None:
        if not self._drag:
            return
        drop = self._get_drop(event.x, event.y)
        did_drop = False
        if drop:
            kind, target = drop
            self._apply_drop(kind, target)
            lbl, icon = ACTION_INFO.get(kind, (kind, ""))
            self.app.notify(f"{icon} {lbl}", timeout=2)
            did_drop = True
        self._drag = None
        self._drop = None
        self.release_mouse()
        if did_drop:
            self._reload()
        self._invalidate()

    def on_leave(self, _: Leave) -> None:
        if self._hover:
            self._hover = None
            self._invalidate()

    def _reload(self) -> None:
        self.panes, self.win_w, self.win_h = get_panes()


# -- app -----------------------------------------------------------------------

class TmuxPanes(App):
    CSS = """
    Screen { background: #0a0f1e; }
    Footer { background: #0d1b2a; color: #4a9eff; }
    """
    BINDINGS = [
        ("q", "quit",   "Quit"),
        ("r", "reload", "Reload"),
    ]

    def compose(self) -> ComposeResult:
        panes, win_w, win_h = get_panes()
        yield PaneMap(panes, win_w, win_h)
        yield Footer()

    def action_reload(self) -> None:
        widget = self.query_one(PaneMap)
        widget._reload()
        widget._invalidate()
