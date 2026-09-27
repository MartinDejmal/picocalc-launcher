# PicoCalc graphical launcher

A native, 16-colour graphical application launcher for the ClockworkPi
PicoCalc running MicroPython on the RP2350. It uses the `picocalc.display` and
`picocalc.terminal` objects created by `boot.py`; it does not allocate a second
framebuffer or replace the VT terminal.

## Controls

| Key | Action |
| --- | --- |
| Up / Down | Select an application |
| Home / End | Jump to the first / last application |
| Enter | Run the selected application |
| R | Rescan applications and refresh metadata |
| F | Flush modules loaded since launcher startup |
| M | Show memory and system information |
| T | Show file-system tools/status |
| Esc | Exit to the MicroPython REPL |

Applications are discovered in `/sd/apps`, `/apps`, `/sd`, and `/`. Optional
metadata can be added near the beginning of a script:

```python
# picocalc-app: name=Snake, description=Classic snake game, category=Games
```

Supported categories are Music, Games, Network, Graphics, Tools, Apps, and
Other. Each category has a small icon drawn directly with framebuffer
primitives.
