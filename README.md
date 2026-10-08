# QuickSnip

A Windows-style snipping tool for Ubuntu / GNOME on Wayland.

Press **Super+Shift+S**, drag a box, let go — that's it. No "select area → click capture" steps.

- The screen freezes and dims; the area you drag is shown at full brightness.
- On mouse release the snip is **copied to the clipboard** and **saved** to `~/Pictures/Screenshots/`.
- A notification pops up with a preview — **click it to mark up** the screenshot.
- Works across multiple monitors (drag on any one of them).
- **Esc** or right-click cancels.

## Editor

| Tool | |
|---|---|
| Pen | Freehand drawing |
| Highlighter | Wide translucent marker |
| Box | Rectangle outline |
| Arrow | Arrow from drag start to end |
| Crop | Keep only the dragged area |

Six colours, plus **Undo** (Ctrl+Z), **Copy** (Ctrl+C), **Save** (Ctrl+S, overwrites the snip) and **Save as** (Ctrl+Shift+S).

## Install

Requirements: GNOME on Wayland (tested on Ubuntu 26.04, GNOME 50), Python 3 with GTK 4 bindings
(`python3-gi`, `gir1.2-gtk-4.0`, `python3-gi-cairo` — preinstalled on Ubuntu desktop).

```bash
git clone https://github.com/dhalishiva/quicksnip.git
cd quicksnip
./install.sh
```

The installer:

- copies `quicksnip.py` to `~/.local/bin/quicksnip`
- adds a desktop entry and D-Bus service (needed for clickable notifications)
- binds **Super+Shift+S** as a GNOME custom shortcut (change it in Settings → Keyboard → Custom Shortcuts)

Run `./install.sh` again after editing the source.

## How it works

GNOME on Wayland doesn't let ordinary apps read the screen, so QuickSnip asks the
`org.freedesktop.portal.Screenshot` portal for a silent full-screen capture, shows it frozen in a
fullscreen window on each monitor, and crops the region you drag.

Wayland drops clipboard contents when the copying app exits, so QuickSnip stays running in the
background (no window) until something else is copied.

## Uninstall

```bash
rm ~/.local/bin/quicksnip \
   ~/.local/share/applications/io.github.quicksnip.desktop \
   ~/.local/share/dbus-1/services/io.github.quicksnip.service
```

Then remove the shortcut in Settings → Keyboard → Custom Shortcuts.
