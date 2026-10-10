#!/usr/bin/env python3
"""QuickSnip: drag-to-capture screenshot tool for GNOME (Wayland).

Run it (bound to Super+Shift+S), drag a rectangle, release the mouse:
the region is copied to the clipboard, saved to ~/Pictures/Screenshots,
and a notification appears; click it to annotate the screenshot or
extract its text (OCR via Tesseract).
Esc or right-click cancels a capture.
"""
import io
import math
import os
import random
import sys
import warnings
from datetime import datetime
from urllib.parse import unquote, urlparse

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Graphene, Gtk  # noqa: E402

warnings.filterwarnings("ignore", category=DeprecationWarning)

APP_ID = "io.github.quicksnip"
SAVE_DIR = os.path.join(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
                        or os.path.expanduser("~/Pictures"), "Screenshots")


def take_screenshot(callback):
    """Ask xdg-desktop-portal for a silent full-screen screenshot; callback(path or None)."""
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    token = f"quicksnip{random.randint(0, 1 << 30)}"
    sender = bus.get_unique_name()[1:].replace(".", "_")
    handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
    sub = None

    def on_response(_conn, _sender, _path, _iface, _signal, params):
        bus.signal_unsubscribe(sub)
        code, results = params.unpack()
        uri = results.get("uri") if code == 0 else None
        callback(unquote(urlparse(uri).path) if uri else None)

    sub = bus.signal_subscribe("org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request",
                               "Response", handle, None, Gio.DBusSignalFlags.NO_MATCH_RULE, on_response)
    options = {"handle_token": GLib.Variant("s", token), "interactive": GLib.Variant("b", False)}
    bus.call("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
             "org.freedesktop.portal.Screenshot", "Screenshot",
             GLib.Variant("(sa{sv})", ("", options)), None, Gio.DBusCallFlags.NONE, -1, None, None)


def rgba(r, g, b, a):
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = r, g, b, a
    return c


class SnipCanvas(Gtk.Widget):
    """Shows the frozen screen, dimmed, with the selected region at full brightness."""

    def __init__(self, texture, layout, monitor):
        super().__init__()
        self.texture = texture
        self.layout = layout      # logical bounding box of all monitors
        self.monitor = monitor    # logical geometry of the monitor we cover
        self.sel = None           # (x, y, w, h) in widget coords

    def image_rect(self):
        """Where the full screenshot sits in this widget's coordinates."""
        lx, ly, lw, lh = self.layout
        return Graphene.Rect().init(lx - self.monitor.x, ly - self.monitor.y, lw, lh)

    def do_snapshot(self, snap):
        w, h = self.get_width(), self.get_height()
        img = self.image_rect()
        snap.append_texture(self.texture, img)
        snap.append_color(rgba(0, 0, 0, 0.45), Graphene.Rect().init(0, 0, w, h))
        if self.sel and self.sel[2] > 0 and self.sel[3] > 0:
            x, y, sw, sh = self.sel
            r = Graphene.Rect().init(x, y, sw, sh)
            snap.push_clip(r)
            snap.append_texture(self.texture, img)
            snap.pop()
            border = rgba(0.21, 0.52, 0.89, 1)
            for bx, by, bw, bh in ((x, y, sw, 1), (x, y + sh - 1, sw, 1), (x, y, 1, sh), (x + sw - 1, y, 1, sh)):
                snap.append_color(border, Graphene.Rect().init(bx, by, bw, bh))


# ---------------------------------------------------------------- editor

COLORS = {
    "red": (0.88, 0.11, 0.14),
    "yellow": (0.96, 0.83, 0.18),
    "green": (0.18, 0.76, 0.49),
    "blue": (0.21, 0.52, 0.89),
    "black": (0.0, 0.0, 0.0),
    "white": (1.0, 1.0, 1.0),
}
TOOLS = [("pen", "Pen"), ("highlight", "Highlighter"), ("box", "Box"), ("arrow", "Arrow"), ("crop", "Crop")]

EDITOR_CSS = """
.swatch { min-width: 18px; min-height: 18px; padding: 2px; border-radius: 50%; }
.swatch:checked { outline: 2px solid #3584e4; outline-offset: 1px; }
""" + "".join(f".swatch-{n} {{ background: rgb({r*255:.0f},{g*255:.0f},{b*255:.0f}); }}\n"
              for n, (r, g, b) in COLORS.items())


def draw_op(ctx, op):
    """Draw one annotation (in image coordinates) onto a cairo context."""
    t = op["tool"]
    r, g, b = op["color"]
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    if t == "highlight":
        ctx.set_source_rgba(r, g, b, 0.4)
        ctx.set_line_width(18)
        ctx.set_line_cap(cairo.LINE_CAP_SQUARE)
    else:
        ctx.set_source_rgb(r, g, b)
        ctx.set_line_width(4)
    pts = op["pts"]
    if t in ("pen", "highlight"):
        ctx.move_to(*pts[0])
        for p in pts[1:]:
            ctx.line_to(*p)
        if len(pts) == 1:
            ctx.line_to(pts[0][0] + 0.1, pts[0][1])
        ctx.stroke()
    elif t == "box":
        (x1, y1), (x2, y2) = pts[0], pts[-1]
        ctx.rectangle(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
        ctx.stroke()
    elif t == "arrow":
        (x1, y1), (x2, y2) = pts[0], pts[-1]
        ctx.move_to(x1, y1)
        ctx.line_to(x2, y2)
        ctx.stroke()
        angle = math.atan2(y2 - y1, x2 - x1)
        head = 18
        for side in (-1, 1):
            a = angle + math.pi - side * math.pi / 7
            ctx.move_to(x2, y2)
            ctx.line_to(x2 + head * math.cos(a), y2 + head * math.sin(a))
        ctx.stroke()


def norm_rect(p1, p2):
    (x1, y1), (x2, y2) = p1, p2
    return min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)


class EditorWindow(Gtk.ApplicationWindow):
    """Snipping Tool–style markup: pen, highlighter, box, arrow, crop, undo, copy, save."""

    def __init__(self, app, path):
        super().__init__(application=app, title="QuickSnip")
        self.app = app
        self.path = path
        self.base = cairo.ImageSurface.create_from_png(path)
        self.ops = []          # annotations and crops, in order (undo pops)
        self.current = None    # op being drawn right now
        self.tool = "pen"
        self.color = "red"
        self.text_mode = False
        self.words = []        # OCR'd words: {"rect", "text", "line", "block"} in image coords
        self.selected = []     # indices into self.words
        self.text_drag = None
        self.ocr_key = None    # which edit state self.words belongs to

        css = Gtk.CssProvider()
        css.load_from_string(EDITOR_CSS)
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        header = Gtk.HeaderBar()
        self.set_titlebar(header)
        self.status = Gtk.Label(label=os.path.basename(path))
        self.status.add_css_class("dim-label")
        header.set_title_widget(self.status)

        tools = Gtk.Box(css_classes=["linked"])
        first = None
        for name, label in TOOLS:
            btn = Gtk.ToggleButton(label=label, active=(name == self.tool))
            if first:
                btn.set_group(first)
            first = first or btn
            btn.connect("toggled", self.on_tool, name)
            tools.append(btn)
        header.pack_start(tools)
        self.tools_box = tools

        swatches = Gtk.Box(spacing=6, margin_start=6)
        first = None
        for name in COLORS:
            btn = Gtk.ToggleButton(active=(name == self.color), tooltip_text=name.title(),
                                   css_classes=["swatch", f"swatch-{name}"], valign=Gtk.Align.CENTER)
            if first:
                btn.set_group(first)
            first = first or btn
            btn.connect("toggled", self.on_color, name)
            swatches.append(btn)
        header.pack_start(swatches)
        self.swatches_box = swatches

        for icon, tip, cb in (("document-save-as-symbolic", "Save as… (Ctrl+Shift+S)", self.save_as),
                              ("document-save-symbolic", "Save (Ctrl+S)", self.save),
                              ("edit-copy-symbolic", "Copy (Ctrl+C)", self.copy),
                              ("edit-undo-symbolic", "Undo (Ctrl+Z)", self.undo)):
            btn = Gtk.Button(icon_name=icon, tooltip_text=tip)
            btn.connect("clicked", lambda _b, f=cb: f())
            header.pack_end(btn)

        self.text_btn = Gtk.ToggleButton(icon_name="insert-text-symbolic", tooltip_text="Select text (Ctrl+T)")
        self.text_btn.connect("toggled", self.on_text_toggled)
        header.pack_end(self.text_btn)
        self.copy_all_btn = Gtk.Button(label="Copy all text", visible=False)
        self.copy_all_btn.connect("clicked", lambda _b: self.copy_text(all_words=True))
        header.pack_end(self.copy_all_btn)

        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.area.set_draw_func(self.on_draw)
        self.area.set_cursor_from_name("crosshair")
        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.area.add_controller(drag)

        self.set_child(self.area)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

        w, h = self.base.get_width(), self.base.get_height()
        self.set_default_size(max(min(w + 40, 1400), 760), max(min(h + 40, 900), 300))

    # -- geometry

    def region(self):
        """Visible part of the image (after crops), in image coordinates."""
        for op in reversed(self.ops):
            if op["tool"] == "crop":
                return op["rect"]
        return 0, 0, self.base.get_width(), self.base.get_height()

    def view(self):
        """(scale, offset_x, offset_y) mapping the region into the drawing area."""
        rx, ry, rw, rh = self.region()
        aw, ah = self.area.get_width(), self.area.get_height()
        s = min((aw - 20) / rw, (ah - 20) / rh, 1.0)
        return s, (aw - rw * s) / 2, (ah - rh * s) / 2

    def to_image(self, x, y):
        s, ox, oy = self.view()
        rx, ry, _, _ = self.region()
        return (x - ox) / s + rx, (y - oy) / s + ry

    # -- rendering

    def render(self, ctx, ops):
        ctx.set_source_surface(self.base, 0, 0)
        ctx.paint()
        for op in ops:
            if op["tool"] != "crop":
                draw_op(ctx, op)

    def on_draw(self, _area, ctx, _w, _h):
        s, ox, oy = self.view()
        rx, ry, rw, rh = self.region()
        ctx.translate(ox, oy)
        ctx.scale(s, s)
        ctx.translate(-rx, -ry)
        ctx.rectangle(rx, ry, rw, rh)
        ctx.clip()
        self.render(ctx, self.ops)
        cur = self.current
        if cur and cur["tool"] == "crop":
            cx, cy, cw, ch = norm_rect(cur["pts"][0], cur["pts"][-1])
            ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            ctx.rectangle(rx, ry, rw, rh)
            ctx.rectangle(cx, cy, cw, ch)
            ctx.set_source_rgba(0, 0, 0, 0.5)
            ctx.fill()
            ctx.rectangle(cx, cy, cw, ch)
            ctx.set_source_rgb(1, 1, 1)
            ctx.set_line_width(1.5 / s)
            ctx.set_dash([6 / s, 4 / s])
            ctx.stroke()
        elif cur:
            draw_op(ctx, cur)
        if self.text_mode:
            self.draw_text_overlay(ctx, s, (rx, ry, rw, rh))

    def draw_text_overlay(self, ctx, s, region):
        ctx.rectangle(*region)
        ctx.set_source_rgba(0, 0, 0, 0.3)
        ctx.fill()
        selected = set(self.selected)
        pad = 2
        for i, w in enumerate(self.words):
            x, y, ww, wh = w["rect"]
            ctx.rectangle(x - pad, y - pad, ww + 2 * pad, wh + 2 * pad)
            if i in selected:
                ctx.set_source_rgba(0.21, 0.52, 0.89, 0.5)
            else:
                ctx.set_source_rgba(1, 1, 1, 0.22)
            ctx.fill()
        drag = self.text_drag
        if drag and drag.get("marquee"):
            ctx.rectangle(*drag["marquee"])
            ctx.set_source_rgb(1, 1, 1)
            ctx.set_line_width(1.5 / s)
            ctx.set_dash([6 / s, 4 / s])
            ctx.stroke()

    def export_png(self, scale=1, annotate=True):
        rx, ry, rw, rh = (round(v) for v in self.region())
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, rw * scale, rh * scale)
        ctx = cairo.Context(surface)
        ctx.scale(scale, scale)
        ctx.translate(-rx, -ry)
        self.render(ctx, self.ops if annotate else [])
        buf = io.BytesIO()
        surface.write_to_png(buf)
        return buf.getvalue()

    # -- input

    def on_tool(self, btn, name):
        if btn.get_active():
            self.tool = name

    def on_color(self, btn, name):
        if btn.get_active():
            self.color = name

    def on_drag_begin(self, _g, x, y):
        if self.text_mode:
            p = self.to_image(x, y)
            s, _, _ = self.view()
            self.text_drag = {"start": p, "anchor": self.word_at(p, max_dist=12 / s)}
            self.update_text_selection(p)
            return
        self.current = {"tool": self.tool, "color": COLORS[self.color], "pts": [self.to_image(x, y)]}
        self.area.queue_draw()

    def on_drag_update(self, g, dx, dy):
        ok, sx, sy = g.get_start_point()
        if self.text_mode and self.text_drag:
            self.update_text_selection(self.to_image(sx + dx, sy + dy))
            return
        if not self.current:
            return
        p = self.to_image(sx + dx, sy + dy)
        if self.current["tool"] in ("pen", "highlight"):
            self.current["pts"].append(p)
        else:
            self.current["pts"][1:] = [p]
        self.area.queue_draw()

    def on_drag_end(self, g, dx, dy):
        self.on_drag_update(g, dx, dy)
        if self.text_mode:
            self.text_drag = None
            self.area.queue_draw()
            return
        op, self.current = self.current, None
        if not op:
            return
        if op["tool"] == "crop":
            rx, ry, rw, rh = self.region()
            cx, cy, cw, ch = norm_rect(op["pts"][0], op["pts"][-1])
            # Keep the crop inside the current visible region.
            x1, y1 = max(cx, rx), max(cy, ry)
            x2, y2 = min(cx + cw, rx + rw), min(cy + ch, ry + rh)
            if x2 - x1 >= 5 and y2 - y1 >= 5:
                self.ops.append({"tool": "crop", "rect": (x1, y1, x2 - x1, y2 - y1)})
        elif op["tool"] in ("pen", "highlight") or len(op["pts"]) > 1:
            self.ops.append(op)
        self.area.queue_draw()

    def on_key(self, _ctl, keyval, _code, state):
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK
        key = Gdk.keyval_to_lower(keyval)
        if ctrl and key == Gdk.KEY_t:
            self.text_btn.set_active(not self.text_btn.get_active())
            return True
        if self.text_mode:
            if ctrl and key == Gdk.KEY_c:
                self.copy_text(all_words=not self.selected)
                return True
            if ctrl and key == Gdk.KEY_a:
                self.selected = list(range(len(self.words)))
                self.area.queue_draw()
                return True
            if keyval == Gdk.KEY_Escape:
                self.text_btn.set_active(False)
                return True
        if ctrl and key == Gdk.KEY_z:
            self.undo()
        elif ctrl and key == Gdk.KEY_c:
            self.copy()
        elif ctrl and shift and key == Gdk.KEY_s:
            self.save_as()
        elif ctrl and key == Gdk.KEY_s:
            self.save()
        else:
            return False
        return True

    # -- text extraction

    def on_text_toggled(self, btn):
        self.text_mode = btn.get_active()
        self.tools_box.set_sensitive(not self.text_mode)
        self.swatches_box.set_sensitive(not self.text_mode)
        self.copy_all_btn.set_visible(self.text_mode)
        self.area.set_cursor_from_name("text" if self.text_mode else "crosshair")
        self.selected = []
        if self.text_mode and self.ocr_key != self.edit_key():
            self.run_ocr()
        else:
            self.status.set_label(self.idle_status())
        self.area.queue_draw()

    def edit_key(self):
        return self.region()

    def run_ocr(self):
        """Find words and their boxes in the visible (cropped) image with Tesseract."""
        self.words = []
        self.status.set_label("Reading text…")
        rx, ry, rw, _ = (round(v) for v in self.region())
        scale = 2 if rw < 2000 else 1  # small screen text OCRs better enlarged
        png = self.export_png(scale=scale, annotate=False)
        try:
            proc = Gio.Subprocess.new(["tesseract", "stdin", "stdout", "tsv"],
                                      Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE
                                      | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            self.status.set_label("Needs Tesseract: sudo apt install tesseract-ocr")
            return
        key = self.edit_key()
        proc.communicate_async(GLib.Bytes.new(png), None, self.on_ocr_done, (key, rx, ry, scale))

    def on_ocr_done(self, proc, result, info):
        key, rx, ry, scale = info
        try:
            _, out, _ = proc.communicate_finish(result)
        except GLib.Error as e:
            self.status.set_label(f"Text extraction failed: {e.message}")
            return
        words = []
        for row in (out.get_data() or b"").decode("utf-8", "replace").splitlines()[1:]:
            cols = row.split("\t")
            if len(cols) < 12 or cols[0] != "5" or not cols[11].strip():
                continue
            block, par, line = int(cols[2]), int(cols[3]), int(cols[4])
            left, top, width, height = (int(c) / scale for c in cols[6:10])
            words.append({"rect": (rx + left, ry + top, width, height), "text": cols[11],
                          "line": (block, par, line), "block": (block, par)})
        self.words = words
        self.ocr_key = key
        self.status.set_label(self.idle_status())
        self.area.queue_draw()

    def idle_status(self):
        if not self.text_mode:
            return os.path.basename(self.path)
        if not self.words:
            return "No text found"
        return "Drag over text to select · Ctrl+C to copy"

    def word_at(self, p, max_dist=None):
        """Index of the word under (or nearest to) image point p."""
        best, best_d = None, None
        px, py = p
        for i, w in enumerate(self.words):
            x, y, ww, wh = w["rect"]
            dx = max(x - px, 0, px - (x + ww))
            dy = max(y - py, 0, py - (y + wh))
            d = math.hypot(dx, dy * 2)  # favour words on the same line
            if best_d is None or d < best_d:
                best, best_d = i, d
        if best is not None and max_dist is not None and best_d > max_dist:
            return None
        return best

    def update_text_selection(self, p):
        drag = self.text_drag
        if drag["anchor"] is not None:
            # Started on a word: select in reading order, like normal text.
            end = self.word_at(p)
            a, b = sorted((drag["anchor"], end))
            self.selected = list(range(a, b + 1))
        else:
            # Started on empty space: select every word the box touches.
            mx, my, mw, mh = norm_rect(drag["start"], p)
            drag["marquee"] = (mx, my, mw, mh)
            self.selected = [i for i, w in enumerate(self.words)
                             if w["rect"][0] < mx + mw and w["rect"][0] + w["rect"][2] > mx
                             and w["rect"][1] < my + mh and w["rect"][1] + w["rect"][3] > my]
        self.area.queue_draw()

    def copy_text(self, all_words=False):
        indices = range(len(self.words)) if all_words else sorted(self.selected)
        parts, prev = [], None
        for i in indices:
            w = self.words[i]
            if prev is not None:
                parts.append(" " if w["line"] == prev["line"] else "\n" if w["block"] == prev["block"] else "\n\n")
            parts.append(w["text"])
            prev = w
        text = "".join(parts)
        if text:
            self.app.set_clipboard(self.get_display(), Gdk.ContentProvider.new_for_bytes(
                "text/plain;charset=utf-8", GLib.Bytes.new(text.encode())))
            self.flash("Text copied to clipboard")

    # -- actions

    def flash(self, text):
        self.status.set_label(text)
        GLib.timeout_add(1800, lambda: self.status.set_label(self.idle_status()) and False)

    def undo(self):
        if self.ops and not self.text_mode:  # undoing a crop would invalidate the word boxes
            self.ops.pop()
            self.area.queue_draw()

    def copy(self):
        self.app.set_clipboard_png(self.get_display(), self.export_png())
        self.flash("Copied to clipboard")

    def save(self):
        png = self.export_png()
        with open(self.path, "wb") as f:
            f.write(png)
        self.app.set_clipboard_png(self.get_display(), png)
        self.flash("Saved and copied")

    def save_as(self):
        dialog = Gtk.FileDialog(initial_name=os.path.basename(self.path),
                                initial_folder=Gio.File.new_for_path(os.path.dirname(self.path)))
        dialog.save(self, None, self.on_save_as_done)

    def on_save_as_done(self, dialog, result):
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return  # cancelled
        self.path = gfile.get_path()
        self.save()


# ---------------------------------------------------------------- app

class QuickSnip(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.windows = []
        self.busy = False
        self.clipboard_held = False

    def do_startup(self):
        Gtk.Application.do_startup(self)
        # Triggered by clicking the notification (also works after we exited,
        # via D-Bus activation of the installed service file).
        edit = Gio.SimpleAction.new("edit", GLib.VariantType.new("s"))
        edit.connect("activate", lambda _a, p: self.open_editor(p.get_string()))
        self.add_action(edit)

    def open_editor(self, path):
        if os.path.exists(path):
            EditorWindow(self, path).present()

    def do_activate(self):
        # A second Super+Shift+S while one is running is forwarded here.
        if self.busy:
            return
        self.busy = True
        self.hold()
        take_screenshot(self.on_screenshot)

    def on_screenshot(self, path):
        if not path or not os.path.exists(path):
            print("quicksnip: screenshot portal failed or was denied", file=sys.stderr)
            self.finish()
            return
        try:
            self.pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        finally:
            os.remove(path)  # the portal's temporary full-screen copy
        self.show_overlay()

    def show_overlay(self):
        display = Gdk.Display.get_default()
        monitors = [display.get_monitors().get_item(i) for i in range(display.get_monitors().get_n_items())]
        geoms = [m.get_geometry() for m in monitors]
        lx, ly = min(g.x for g in geoms), min(g.y for g in geoms)
        lw = max(g.x + g.width for g in geoms) - lx
        lh = max(g.y + g.height for g in geoms) - ly
        self.layout = (lx, ly, lw, lh)
        self.scale = self.pixbuf.get_width() / lw  # image pixels per logical pixel

        texture = Gdk.Texture.new_for_pixbuf(self.pixbuf)
        self.windows = []
        for mon, geom in zip(monitors, geoms):
            self.windows.append(self.make_overlay(texture, mon, geom))

    def make_overlay(self, texture, monitor, geom):
        win = Gtk.ApplicationWindow(application=self, decorated=False)
        win.set_cursor_from_name("crosshair")
        canvas = SnipCanvas(texture, self.layout, geom)
        win.set_child(canvas)

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self.on_drag_begin, canvas)
        drag.connect("drag-update", self.on_drag_update, canvas)
        drag.connect("drag-end", self.on_drag_end, canvas)
        canvas.add_controller(drag)

        cancel_click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        cancel_click.connect("pressed", lambda *a: self.close_overlay())
        canvas.add_controller(cancel_click)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        win.add_controller(keys)

        win.fullscreen_on_monitor(monitor)
        win.present()
        return win

    def on_key(self, _ctl, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.close_overlay()
            return True
        return False

    def on_drag_begin(self, _g, x, y, canvas):
        self.start = (x, y)
        canvas.sel = (x, y, 0, 0)
        canvas.queue_draw()

    def on_drag_update(self, _g, dx, dy, canvas):
        sx, sy = self.start
        canvas.sel = (min(sx, sx + dx), min(sy, sy + dy), abs(dx), abs(dy))
        canvas.queue_draw()

    def on_drag_end(self, g, dx, dy, canvas):
        self.on_drag_update(g, dx, dy, canvas)
        x, y, w, h = canvas.sel
        if w < 3 or h < 3:  # a click, not a drag: stay in capture mode
            canvas.sel = None
            canvas.queue_draw()
            return
        # Clamp to this monitor in case the drag ran off its edge.
        x2, y2 = min(x + w, canvas.get_width()), min(y + h, canvas.get_height())
        x, y = max(x, 0), max(y, 0)
        try:
            self.capture(canvas, x, y, x2 - x, y2 - y)
        finally:
            self.close_overlay()

    def capture(self, canvas, x, y, w, h):
        lx, ly, _, _ = self.layout
        mon = canvas.monitor
        s = self.scale
        px = round((x + mon.x - lx) * s)
        py = round((y + mon.y - ly) * s)
        pw = min(round(w * s), self.pixbuf.get_width() - px)
        ph = min(round(h * s), self.pixbuf.get_height() - py)
        crop = self.pixbuf.new_subpixbuf(px, py, pw, ph).copy()

        os.makedirs(SAVE_DIR, exist_ok=True)
        path = os.path.join(SAVE_DIR, datetime.now().strftime("Screenshot_%Y-%m-%d_%H-%M-%S.png"))
        crop.savev(path, "png", [], [])
        with open(path, "rb") as f:
            png = f.read()

        # Clipboard must be set while our window still has focus (Wayland rule).
        self.set_clipboard_png(canvas.get_display(), png)
        self.notify_captured(path)

    def set_clipboard_png(self, display, png):
        data = GLib.Bytes.new(png)
        self.set_clipboard(display, Gdk.ContentProvider.new_union([
            Gdk.ContentProvider.new_for_bytes("image/png", data),
            Gdk.ContentProvider.new_for_value(Gdk.Texture.new_from_bytes(data)),
        ]))

    def set_clipboard(self, display, provider):
        clipboard = display.get_clipboard()
        clipboard.set_content(provider)
        if not self.clipboard_held:
            # Keep the process alive (windowless) so the image stays pasteable.
            self.clipboard_held = True
            self.hold()
            clipboard.connect("notify::local", self.on_clipboard_changed)

    def on_clipboard_changed(self, clipboard, _pspec):
        # Another app copied something; we no longer need to serve the image.
        if not clipboard.get_property("local") and self.clipboard_held:
            self.clipboard_held = False
            clipboard.disconnect_by_func(self.on_clipboard_changed)
            self.release()

    def notify_captured(self, path):
        n = Gio.Notification.new("Screenshot copied to clipboard")
        n.set_body("Click to mark up and edit")
        n.set_icon(Gio.FileIcon.new(Gio.File.new_for_path(path)))
        target = GLib.Variant("s", path)
        n.set_default_action_and_target("app.edit", target)
        n.add_button_with_target("Edit", "app.edit", target)
        self.send_notification("captured", n)  # same id: replaces the previous one

    def close_overlay(self):
        if not self.windows:
            return
        for win in self.windows:
            win.destroy()
        self.windows = []
        self.finish()

    def finish(self):
        self.busy = False
        self.pixbuf = None
        self.release()


if __name__ == "__main__":
    sys.exit(QuickSnip().run(sys.argv))
