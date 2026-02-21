# tmux-pane-mover

Drag-and-drop TUI for rearranging tmux panes.

![screenshot](https://raw.githubusercontent.com/tzafrir/tmux-pane-mover/main/images/screenshot.png)

## Features

- **Swap panes** -- drag one pane onto the center of another to swap their contents
- **Split at pane edges** -- drop on the edge of a pane to split it left/right/above/below
- **Screen-edge zones** -- drag to the screen edge to create a full-width row or full-height column
- **Visual feedback** -- highlighted drop zones, drag ghost, and action labels

## Installation

```
pip install tmux-pane-mover
```

## Usage

Run inside a tmux session:

```
tmux-pane-mover
```

### Key bindings

| Key | Action |
|-----|--------|
| Mouse drag | Pick up and drop panes |
| `q` | Quit |
| `r` | Reload pane layout |

### Drop zones

- **Center of a pane** -- swap contents (`tmux swap-pane`)
- **Edge of a pane** -- split there (`tmux join-pane`)
- **Screen edge strip** -- outermost column/row split (`tmux join-pane -f`)

## Requirements

- tmux
- Python 3.10+

## License

MIT
