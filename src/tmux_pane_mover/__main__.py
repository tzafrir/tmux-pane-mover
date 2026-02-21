from __future__ import annotations

import subprocess
import sys

from tmux_pane_mover import __version__
from tmux_pane_mover.app import TmuxPanes


def main() -> None:
    if "--version" in sys.argv:
        print(f"tmux-pane-mover {__version__}")
        return

    try:
        old_pane_title = subprocess.run(
            ["tmux", "display-message", "-p", "#{pane_title}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: tmux not found or not running inside a tmux session.", file=sys.stderr)
        sys.exit(1)

    subprocess.run(
        ["tmux", "select-pane", "-T", "tmux-pane-mover"],
        capture_output=True, check=True,
    )
    try:
        TmuxPanes().run()
    finally:
        subprocess.run(
            ["tmux", "select-pane", "-T", old_pane_title],
            capture_output=True,
        )


if __name__ == "__main__":
    main()
