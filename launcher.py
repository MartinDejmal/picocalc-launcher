"""Native 4-bit graphical application launcher for the PicoCalc.

The display and terminal are created by ``boot.py``.  In particular, this
module must never construct another PicoDisplay: doing so wastes RAM and can
leave the LCD and the VT console out of sync.
"""

import os
import sys
import time

import picocalc


# Logical colours in the 16-entry display LUT.
BG = 0
PANEL = 1
PANEL_2 = 2
TEXT = 7
MUTED = 6
BLUE = 12
CYAN = 13
SELECTED = 5
SELECTED_TEXT = 7
GREEN = 11
YELLOW = 10
RED = 8
WHITE = 7

SCREEN_W = 320
SCREEN_H = 320
HEADER_H = 28
FOOTER_H = 28
ROW_H = 52
VISIBLE_ROWS = 5


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _join(base, name):
    return (base.rstrip("/") + "/" + name) if base != "/" else "/" + name


def run_script(path):
    """Run an application in isolated globals (and tolerate sys.exit())."""
    try:
        with open(path, "r") as source:
            code = source.read()
    except Exception as exc:
        print("Could not load", path, exc)
        return

    namespace = {
        "__name__": "__main__",
        "__file__": path,
        "run_script": run_script,
        "run": run_script,
        "os": os,
        "sys": sys,
    }
    try:
        exec(code, namespace, namespace)
    except SystemExit:
        pass
    except Exception as exc:
        # The VT console is deliberately used for tracebacks and diagnostics.
        print("Application error:", exc)


# Backwards-compatible REPL helper.
run = run_script


def _title_from_filename(filename):
    name = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    # MicroPython's compact ``str`` implementation does not provide title().
    # Build a readable fallback using only the basic string operations that are
    # available on the RP2350 firmware.
    words = name.replace("_", " ").replace("-", " ").split()
    return " ".join((word[:1].upper() + word[1:].lower()) for word in words)


def _category_name(value):
    """Return a supported category with canonical casing.

    Using a lookup also keeps metadata case-insensitive without relying on
    CPython-only convenience methods such as ``str.title``.
    """
    categories = {
        "music": "Music",
        "games": "Games",
        "network": "Network",
        "graphics": "Graphics",
        "tools": "Tools",
        "apps": "Apps",
        "other": "Other",
    }
    return categories.get(str(value).strip().lower(), "Other")


def _parse_metadata(path):
    """Read the small ``# picocalc-app`` header without loading the app."""
    meta = {}
    try:
        with open(path, "r") as source:
            # Metadata belongs at the top.  Capping this makes rescans predictable.
            for _ in range(24):
                line = source.readline()
                if not line:
                    break
                stripped = line.strip()
                if not stripped.startswith("#"):
                    if stripped:
                        break
                    continue
                marker = stripped[1:].strip()
                if not marker.lower().startswith("picocalc-app"):
                    continue
                value = marker[len("picocalc-app"):].lstrip(" :")
                # Accept both one key per header and comma/semicolon lists.
                for field in value.replace(";", ",").split(","):
                    if "=" in field:
                        key, val = field.split("=", 1)
                    elif ":" in field:
                        key, val = field.split(":", 1)
                    else:
                        continue
                    meta[key.strip().lower()] = val.strip().strip('"\'')
    except Exception:
        pass
    return meta


def scan_apps():
    """Build the application cache.  Called at startup and explicitly by R."""
    apps = []
    roots = ["/sd/apps", "/apps", "/sd", "/"]
    seen = set()
    for root in roots:
        if not _exists(root):
            continue
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            if not name.lower().endswith(".py"):
                continue
            if name in ("boot.py", "main.py", "launcher.py"):
                continue
            path = _join(root, name)
            if path in seen:
                continue
            seen.add(path)
            meta = _parse_metadata(path)
            title = meta.get("name", meta.get("title", _title_from_filename(name)))
            description = meta.get("description", meta.get("desc", "Python application"))
            category = _category_name(meta.get("category", "Apps"))
            apps.append({"path": path, "name": title,
                         "description": description, "category": category})
    apps.sort(key=lambda app: app["name"].lower())
    return apps


class LauncherModel:
    """Application data and navigation state; never touches the display."""

    def __init__(self, scanner=scan_apps):
        self._scanner = scanner
        self.apps = []
        self.selection = 0
        self.scroll = 0
        self.refresh()

    def refresh(self):
        old_path = self.selected().get("path") if self.apps else None
        self.apps = self._scanner()
        self.selection = 0
        if old_path:
            for index, app in enumerate(self.apps):
                if app["path"] == old_path:
                    self.selection = index
                    break
        self._ensure_visible()

    def selected(self):
        if not self.apps:
            return {}
        return self.apps[self.selection]

    def move(self, delta):
        if not self.apps:
            return False
        new_selection = max(0, min(len(self.apps) - 1,
                                   self.selection + delta))
        if new_selection == self.selection:
            return False
        self.selection = new_selection
        self._ensure_visible()
        return True

    def home(self):
        if self.apps:
            self.selection = 0
            self._ensure_visible()

    def end(self):
        if self.apps:
            self.selection = len(self.apps) - 1
            self._ensure_visible()

    def _ensure_visible(self):
        if self.selection < self.scroll:
            self.scroll = self.selection
        elif self.selection >= self.scroll + VISIBLE_ROWS:
            self.scroll = self.selection - VISIBLE_ROWS + 1
        maximum = max(0, len(self.apps) - VISIBLE_ROWS)
        self.scroll = max(0, min(self.scroll, maximum))


class LauncherView:
    """Allocation-light renderer for the existing GS4 framebuffer."""

    def __init__(self, display):
        self.d = display
        try:
            display.switchPredefinedLUT("pico8")
        except (AttributeError, ValueError, OSError):
            # Some firmware exposes switchPredefinedLUT(), but does not ship
            # the optional pico8 LUT file.  It then raises ENOENT.  The
            # framebuffer is already usable with its current 16-colour LUT,
            # so retaining that LUT is the correct fallback.
            pass

    def _label(self, text, width):
        text = str(text)
        chars = max(1, width // 8)
        return text if len(text) <= chars else text[:max(1, chars - 1)] + "~"

    def draw_header(self):
        d = self.d
        d.fill_rect(0, 0, SCREEN_W, HEADER_H, PANEL)
        d.text("PicoCalc", 10, 10, WHITE)
        now = time.localtime()
        clock = "%02d:%02d" % (now[3], now[4])
        d.text(clock, SCREEN_W - 10 - len(clock) * 8, 10, MUTED)
        d.hline(0, HEADER_H - 1, SCREEN_W, BLUE)

    def draw_icon(self, x, y, category, selected=False):
        d = self.d
        fg = SELECTED_TEXT if selected else CYAN
        d.rect(x, y, 28, 28, fg)
        if category == "Music":
            d.vline(x + 17, y + 5, 15, fg); d.hline(x + 17, y + 5, 7, fg)
            d.fill_rect(x + 10, y + 18, 7, 6, fg); d.fill_rect(x + 20, y + 15, 6, 6, fg)
        elif category == "Games":
            d.rect(x + 4, y + 9, 20, 13, fg)
            d.hline(x + 7, y + 15, 7, fg); d.vline(x + 10, y + 12, 7, fg)
            d.fill_rect(x + 18, y + 13, 2, 2, fg); d.fill_rect(x + 21, y + 16, 2, 2, fg)
        elif category == "Network":
            d.hline(x + 5, y + 8, 18, fg); d.hline(x + 8, y + 12, 12, fg)
            d.hline(x + 11, y + 16, 6, fg); d.fill_rect(x + 13, y + 20, 2, 2, fg)
        elif category == "Graphics":
            d.rect(x + 5, y + 5, 18, 18, fg); d.line(x + 7, y + 20, x + 14, y + 12, fg)
            d.line(x + 14, y + 12, x + 21, y + 20, fg); d.fill_rect(x + 17, y + 8, 3, 3, fg)
        elif category == "Tools":
            d.line(x + 6, y + 22, x + 21, y + 7, fg); d.line(x + 8, y + 22, x + 23, y + 7, fg)
            d.rect(x + 4, y + 19, 7, 5, fg); d.rect(x + 19, y + 4, 5, 7, fg)
        elif category == "Other":
            d.text("?", x + 10, y + 10, fg)
        else:
            d.rect(x + 6, y + 7, 16, 17, fg); d.hline(x + 9, y + 4, 7, fg)

    def draw_app(self, app, row, selected):
        y = HEADER_H + 2 + row * ROW_H
        if selected:
            self.d.fill_rect(5, y, 304, ROW_H - 3, SELECTED)
            self.d.rect(5, y, 304, ROW_H - 3, CYAN)
        self.draw_icon(13, y + 10, app["category"], selected)
        name_color = SELECTED_TEXT if selected else TEXT
        desc_color = SELECTED_TEXT if selected else MUTED
        self.d.text(self._label(app["name"], 246), 51, y + 10, name_color)
        self.d.text(self._label(app["description"], 246), 51, y + 29, desc_color)

    def draw_footer(self):
        y = SCREEN_H - FOOTER_H
        self.d.fill_rect(0, y, SCREEN_W, FOOTER_H, PANEL_2)
        self.d.hline(0, y, SCREEN_W, BLUE)
        self.d.text("^v Select", 8, y + 10, MUTED)
        self.d.text("ENTER Open", 112, y + 10, TEXT)
        self.d.text("ESC REPL", 232, y + 10, MUTED)

    def draw_scrollbar(self, model):
        count = len(model.apps)
        if count <= VISIBLE_ROWS:
            return
        top, height = HEADER_H + 5, VISIBLE_ROWS * ROW_H - 8
        self.d.fill_rect(313, top, 3, height, PANEL_2)
        thumb = max(12, height * VISIBLE_ROWS // count)
        travel = height - thumb
        maximum = count - VISIBLE_ROWS
        pos = travel * model.scroll // maximum if maximum else 0
        self.d.fill_rect(313, top + pos, 3, thumb, CYAN)

    def draw(self, model):
        self.d.beginDraw()
        self.d.fill(BG)
        self.draw_header()
        if not model.apps:
            self.d.text("No applications found", 80, 143, TEXT)
            self.d.text("Press R to rescan", 88, 163, MUTED)
        else:
            stop = min(len(model.apps), model.scroll + VISIBLE_ROWS)
            for index in range(model.scroll, stop):
                self.draw_app(model.apps[index], index - model.scroll,
                              index == model.selection)
        self.draw_scrollbar(model)
        self.draw_footer()
        self.d.show()

    def draw_message(self, title, lines):
        self.d.beginDraw(); self.d.fill(BG); self.draw_header()
        self.d.fill_rect(16, 52, 288, 210, PANEL)
        self.d.rect(16, 52, 288, 210, CYAN)
        self.d.text(self._label(title, 256), 30, 68, YELLOW)
        y = 94
        for line in lines:
            self.d.text(self._label(line, 256), 30, y, TEXT); y += 18
            if y > 238: break
        self.d.text("Press any key", 104, 278, MUTED)
        self.d.show()


class LauncherController:
    def __init__(self, model, view, terminal):
        self.model = model
        self.view = view
        self.terminal = terminal
        self.buffer = bytearray(16)
        self.running = True
        self._base_modules = set(sys.modules)

    def read_key(self):
        count = self.terminal.readinto(self.buffer)
        if not count:
            return None
        return bytes(self.buffer[:count])

    def _wait_key(self):
        while self.running:
            key = self.read_key()
            if key:
                return key
            time.sleep_ms(30)

    def _restore_vt(self):
        # Applications and the REPL retain the boot-created terminal.
        print("\x1b[0m\x1b[2J\x1b[H\x1b[?25h", end="")

    def launch(self):
        app = self.model.selected()
        if not app:
            return
        self._restore_vt()
        run_script(app["path"])
        self.view.draw(self.model)

    def show_system_info(self):
        lines = ["Python: " + sys.version.split()[0],
                 "Apps: %d" % len(self.model.apps)]
        try:
            import gc
            gc.collect()
            lines.extend(("Free RAM: %d" % gc.mem_free(),
                          "Used RAM: %d" % gc.mem_alloc()))
        except (ImportError, AttributeError):
            lines.append("Memory details unavailable")
        self.view.draw_message("Memory / system", lines)
        self._wait_key(); self.view.draw(self.model)

    def flush_modules(self):
        removed = 0
        for name in tuple(sys.modules):
            if name not in self._base_modules and name not in ("picocalc",):
                try:
                    del sys.modules[name]; removed += 1
                except KeyError:
                    pass
        self.view.draw_message("Modules flushed", ["Removed: %d" % removed])
        self._wait_key(); self.view.draw(self.model)

    def file_tools(self):
        roots = ["Internal: /", "SD card: /sd" if _exists("/sd") else "SD card: not mounted"]
        self.view.draw_message("File tools", roots + ["Use REPL for file operations"])
        self._wait_key(); self.view.draw(self.model)

    def handle_key(self, key):
        if key in (b"\x1b[A", b"k"):
            if self.model.move(-1): self.view.draw(self.model)
        elif key in (b"\x1b[B", b"j"):
            if self.model.move(1): self.view.draw(self.model)
        elif key in (b"\x1b[H", b"\x1b[1~"):
            self.model.home(); self.view.draw(self.model)
        elif key in (b"\x1b[F", b"\x1b[4~"):
            self.model.end(); self.view.draw(self.model)
        elif key in (b"\r", b"\n", b"\r\n"):
            self.launch()
        elif key in (b"r", b"R"):
            self.model.refresh(); self.view.draw(self.model)
        elif key in (b"f", b"F"):
            self.flush_modules()
        elif key in (b"m", b"M"):
            self.show_system_info()
        elif key in (b"t", b"T"):
            self.file_tools()
        elif key in (b"\x1b", b"\x1b\x1b"):
            self.running = False

    def run(self):
        self.view.draw(self.model)
        while self.running:
            key = self.read_key()
            if key:
                self.handle_key(key)
            else:
                time.sleep_ms(30)
        self._restore_vt()


def main():
    display = picocalc.display
    model = LauncherModel()
    view = LauncherView(display)
    controller = LauncherController(model, view, picocalc.terminal)
    controller.run()


if __name__ == "__main__":
    globals()["run"] = run_script
    try:
        main()
    except KeyboardInterrupt:
        print("\nLauncher interrupted.")
