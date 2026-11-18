#!/usr/bin/env python3
"""Livro de prints: recorte de uma área da tela no Windows e PDF de cima para baixo."""

from __future__ import annotations

import ctypes
import math
import os
import sys
import tempfile
import zlib
from ctypes import wintypes
from dataclasses import dataclass, field

PAGE_WIDTH = 595.28
MAX_PAGE_HEIGHT = 14400
MIN_CROP = 8
CAPTURE_SETTLE_MS = 200

BG = "#141210"
BG2 = "#221e1a"
INK = "#1c1612"
PAPER = "#f3ecdf"
CREAM = "#f6f0e6"
MUTED = "#b3a79a"
FAINT = "#d9cfc2"
STAMP = "#d94a32"
STAMP_INK = "#fff7f3"
RULE = "#e4b2a4"
NUM = "#9c341f"
FOCUS = "#ffb15a"
LINE = "#3c342c"

DIM_TABLE = bytes((value * 45) // 100 for value in range(256))

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
BI_RGB = 0
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

tk = None
filedialog = None


@dataclass
class Shot:
    id: int
    width: int
    height: int
    rgb: bytes
    preview: object = field(default=None, repr=False, compare=False)
    preview_for: int = field(default=0, repr=False, compare=False)


def prints_label(count: int) -> str:
    if count == 1:
        return "1 print"
    return f"{count} prints"


def fmt(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("número inválido")
    rounded = math.floor(value * 1000 + 0.5) / 1000
    if rounded == 0:
        return "0"
    text = f"{rounded:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def normalize_rect(x0: int, y0: int, x1: int, y1: int, min_size: int = MIN_CROP):
    left, right = sorted((int(x0), int(x1)))
    top, bottom = sorted((int(y0), int(y1)))
    width = right - left + 1
    height = bottom - top + 1
    if width < min_size or height < min_size:
        return None
    return left, top, width, height


def clamp_rect(x: int, y: int, width: int, height: int, bounds, min_size: int = MIN_CROP):
    bx, by, bw, bh = bounds
    x1 = min(x + width, bx + bw)
    y1 = min(y + height, by + bh)
    x0 = max(x, bx)
    y0 = max(y, by)
    cw = x1 - x0
    ch = y1 - y0
    if cw < min_size or ch < min_size:
        return None
    return int(x0), int(y0), int(cw), int(ch)


def crop_rgb(rgb: bytes, width: int, height: int, x: int, y: int, cw: int, ch: int) -> bytes:
    if cw <= 0 or ch <= 0 or width <= 0 or height <= 0:
        raise ValueError("dimensão")
    if x < 0 or y < 0 or x + cw > width or y + ch > height:
        raise ValueError("fora")
    if len(rgb) != width * height * 3:
        raise ValueError("rgb")
    out = bytearray(cw * ch * 3)
    src = memoryview(rgb)
    row_in = width * 3
    row_out = cw * 3
    dest = 0
    for row in range(y, y + ch):
        start = row * row_in + x * 3
        out[dest : dest + row_out] = src[start : start + row_out]
        dest += row_out
    return bytes(out)


def fit_width(src_w: int, src_h: int, max_w: int):
    if src_w <= max_w:
        return src_w, src_h
    new_w = max(1, int(max_w))
    new_h = max(1, int(round(src_h * new_w / src_w)))
    return new_w, new_h


def resize_rgb(rgb: bytes, width: int, height: int, new_w: int, new_h: int) -> bytes:
    if len(rgb) != width * height * 3:
        raise ValueError("rgb")
    if new_w == width and new_h == height:
        return rgb
    src = memoryview(rgb)
    out = bytearray(new_w * new_h * 3)
    dest = 0
    for y in range(new_h):
        sy = min(height - 1, y * height // new_h)
        row = sy * width * 3
        for x in range(new_w):
            sx = min(width - 1, x * width // new_w)
            index = row + sx * 3
            out[dest] = src[index]
            out[dest + 1] = src[index + 1]
            out[dest + 2] = src[index + 2]
            dest += 3
    return bytes(out)


def layout_pages(shots: list[Shot]):
    pages = []
    page = {"height": 0.0, "items": []}
    guard = 0

    def flush():
        nonlocal page
        if page["items"]:
            pages.append(page)
            page = {"height": 0.0, "items": []}

    for shot in shots:
        if shot.width <= 0 or shot.height <= 0:
            raise ValueError("dimensão")
        full_h = PAGE_WIDTH * (shot.height / shot.width)
        consumed = 0.0
        while consumed < 1 - 1e-8:
            guard += 1
            if guard > 100000:
                raise ValueError("layout")
            remain_h = full_h * (1 - consumed)
            if remain_h <= 0.05:
                break
            space = MAX_PAGE_HEIGHT - page["height"]
            if page["items"] and remain_h > space + 0.05 and remain_h <= MAX_PAGE_HEIGHT:
                flush()
                continue
            if space <= 0.05:
                flush()
                continue
            take_h = min(space, remain_h)
            take_frac = take_h / full_h
            page["items"].append(
                {
                    "shot": shot,
                    "src_top": consumed,
                    "src_bottom": consumed + take_frac,
                    "top": page["height"],
                    "full_h": full_h,
                }
            )
            page["height"] += take_h
            consumed += take_frac
            if page["height"] >= MAX_PAGE_HEIGHT - 0.05:
                flush()
    flush()
    return pages


def content_for(page, index_by_id: dict[int, int]) -> str:
    chunks = []
    height = page["height"]
    for item in page["items"]:
        name = f"/Im{index_by_id[item['shot'].id]}"
        slot_h = (item["src_bottom"] - item["src_top"]) * item["full_h"]
        image_top = item["top"] - item["src_top"] * item["full_h"]
        y = height - image_top - item["full_h"]
        slot_bottom = height - item["top"] - slot_h
        chunks.append(
            "q\n"
            f"{fmt(0)} {fmt(slot_bottom)} {fmt(PAGE_WIDTH)} {fmt(slot_h)} re\n"
            "W\nn\n"
            f"{fmt(PAGE_WIDTH)} 0 0 {fmt(item['full_h'])} 0 {fmt(y)} cm\n"
            f"{name} Do\n"
            "Q\n"
        )
    return "".join(chunks)


def _ascii(text: str) -> bytes:
    return text.encode("latin-1")


def build_pdf(shots: list[Shot]) -> bytes:
    pages = layout_pages(shots)
    if not pages:
        raise ValueError("vazio")
    for shot in shots:
        if len(shot.rgb) != shot.width * shot.height * 3:
            raise ValueError("rgb")

    index_by_id = {shot.id: index for index, shot in enumerate(shots)}
    next_id = 1
    catalog_id = next_id
    next_id += 1
    pages_id = next_id
    next_id += 1
    page_ids = []
    for _page in pages:
        page_ids.append(next_id)
        next_id += 1
    content_ids = []
    for _page in pages:
        content_ids.append(next_id)
        next_id += 1
    image_ids = []
    for _shot in shots:
        image_ids.append(next_id)
        next_id += 1
    info_id = next_id
    next_id += 1

    offsets = [None] * next_id
    chunks: list[bytes] = []
    offset = 0

    def push(data: bytes):
        nonlocal offset
        chunks.append(data)
        offset += len(data)

    def write_object(obj_id: int, parts: list[bytes]):
        offsets[obj_id] = offset
        push(_ascii(f"{obj_id} 0 obj\n"))
        for part in parts:
            push(part)
        push(_ascii("endobj\n"))

    push(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    write_object(
        catalog_id,
        [_ascii(f"<< /Type /Catalog /Pages {pages_id} 0 R /PageLayout /OneColumn >>\n")],
    )
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    write_object(
        pages_id,
        [_ascii(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>\n")],
    )

    for page_index, page in enumerate(pages):
        used = []
        seen = set()
        for item in page["items"]:
            image_index = index_by_id[item["shot"].id]
            if image_index in seen:
                continue
            seen.add(image_index)
            used.append(f"/Im{image_index} {image_ids[image_index]} 0 R")
        write_object(
            page_ids[page_index],
            [
                _ascii(
                    f"<< /Type /Page /Parent {pages_id} 0 R "
                    f"/MediaBox [0 0 {fmt(PAGE_WIDTH)} {fmt(page['height'])}] "
                    f"/Contents {content_ids[page_index]} 0 R "
                    f"/Resources << /ProcSet [/PDF /ImageC] /XObject << {' '.join(used)} >> >> >>\n"
                )
            ],
        )

    for page_index, page in enumerate(pages):
        stream = _ascii(content_for(page, index_by_id))
        write_object(
            content_ids[page_index],
            [
                _ascii(f"<< /Length {len(stream)} >>\nstream\n"),
                stream,
                _ascii("\nendstream\n"),
            ],
        )

    for image_index, shot in enumerate(shots):
        compressed = zlib.compress(shot.rgb)
        write_object(
            image_ids[image_index],
            [
                _ascii(
                    f"<< /Type /XObject /Subtype /Image /Width {shot.width} /Height {shot.height} "
                    f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
                    f"/Length {len(compressed)} >>\nstream\n"
                ),
                compressed,
                _ascii("\nendstream\n"),
            ],
        )

    write_object(info_id, [_ascii("<< /Title (Livro de prints) /Producer (Livro de prints) >>\n")])

    for obj_id in range(1, len(offsets)):
        if offsets[obj_id] is None:
            raise ValueError(f"objeto {obj_id}")

    startxref = offset
    xref = [f"xref\n0 {len(offsets)}\n", "0000000000 65535 f \n"]
    for obj_id in range(1, len(offsets)):
        xref.append(f"{offsets[obj_id]:010d} 00000 n \n")
    push(_ascii("".join(xref)))
    push(
        _ascii(
            f"trailer\n<< /Size {len(offsets)} /Root {catalog_id} 0 R /Info {info_id} 0 R >>\n"
            f"startxref\n{startxref}\n%%EOF\n"
        )
    )
    return b"".join(chunks)


class _BitmapInfo(ctypes.Structure):
    """BITMAPINFO with a 40-byte BITMAPINFOHEADER. Widths are explicit so the layout matches Windows."""

    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
        ("bmiColors", ctypes.c_uint32 * 3),
    ]


class _Win32:
    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        user32, gdi32 = self.user32, self.gdi32
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.ReleaseDC.restype = ctypes.c_int
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.BitBlt.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.DWORD,
        ]
        gdi32.BitBlt.restype = wintypes.BOOL
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteObject.restype = wintypes.BOOL
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        gdi32.DeleteDC.restype = wintypes.BOOL
        gdi32.GetDIBits.argtypes = [
            wintypes.HDC,
            wintypes.HBITMAP,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.UINT,
        ]
        gdi32.GetDIBits.restype = ctypes.c_int


_WIN32 = None


def _win32() -> _Win32:
    global _WIN32
    if os.name != "nt":
        raise OSError("O recorte da tela só funciona no Windows.")
    if _WIN32 is None:
        _WIN32 = _Win32()
    return _WIN32


def virtual_screen():
    api = _win32()
    user32 = api.user32
    x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    width = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    height = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    if width <= 0 or height <= 0:
        return 0, 0, user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)
    return x, y, width, height


def _flip_bgra(raw: bytes, width: int, height: int) -> bytes:
    stride = width * 4
    src = memoryview(raw)
    out = bytearray(len(raw))
    for row in range(height):
        start = (height - 1 - row) * stride
        dest = row * stride
        out[dest : dest + stride] = src[start : start + stride]
    return bytes(out)


def _bgra_to_rgb(raw: bytes) -> bytes:
    src = memoryview(raw)
    pixels = len(raw) // 4
    out = bytearray(pixels * 3)
    dest = 0
    for index in range(0, pixels * 4, 4):
        out[dest] = src[index + 2]
        out[dest + 1] = src[index + 1]
        out[dest + 2] = src[index]
        dest += 3
    return bytes(out)


def _read_bitmap(gdi32, memory_dc, bitmap, width: int, height: int) -> bytes:
    info = _BitmapInfo()
    info.biSize = 40
    info.biWidth = width
    info.biHeight = height
    info.biPlanes = 1
    info.biBitCount = 32
    info.biCompression = BI_RGB
    info.biSizeImage = width * height * 4
    buffer = (ctypes.c_char * (width * height * 4))()
    lines = gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(info), DIB_RGB_COLORS)
    if lines != height:
        raise OSError("GetDIBits")
    return _bgra_to_rgb(_flip_bgra(bytes(buffer), width, height))


def capture_rect(x: int, y: int, width: int, height: int):
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        raise ValueError("dimensão")
    if width > 20000 or height > 20000 or width * height > 60_000_000:
        raise ValueError("área grande demais")
    api = _win32()
    user32, gdi32 = api.user32, api.gdi32
    screen = user32.GetDC(0)
    if not screen:
        raise OSError("GetDC")
    memory = None
    bitmap = None
    try:
        memory = gdi32.CreateCompatibleDC(screen)
        if not memory:
            raise OSError("CreateCompatibleDC")
        bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
        if not bitmap:
            raise OSError("CreateCompatibleBitmap")
        previous = gdi32.SelectObject(memory, bitmap)
        try:
            copied = gdi32.BitBlt(
                memory, 0, 0, width, height, screen, int(x), int(y), SRCCOPY | CAPTUREBLT
            )
            if not copied:
                copied = gdi32.BitBlt(memory, 0, 0, width, height, screen, int(x), int(y), SRCCOPY)
            if not copied:
                raise OSError("BitBlt")
        finally:
            if previous:
                gdi32.SelectObject(memory, previous)
        rgb = _read_bitmap(gdi32, memory, bitmap, width, height)
        return rgb, width, height
    finally:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory:
            gdi32.DeleteDC(memory)
        user32.ReleaseDC(0, screen)


def capture_screen():
    x, y, width, height = virtual_screen()
    rgb, width, height = capture_rect(x, y, width, height)
    return x, y, rgb, width, height


def enable_dpi_awareness():
    if os.name != "nt":
        return
    user32 = ctypes.windll.user32
    try:
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        shcore = ctypes.windll.shcore
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.HRESULT
        if shcore.SetProcessDpiAwareness(2) == 0:
            return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def _widget_hwnd(widget):
    api = _win32()
    widget.update_idletasks()
    hwnd = wintypes.HWND(int(widget.winfo_id()))
    root = api.user32.GetAncestor(hwnd, 2)
    return root or hwnd


def place_window(widget, x: int, y: int, width: int, height: int):
    if os.name != "nt":
        widget.geometry(f"{int(width)}x{int(height)}+{int(x)}+{int(y)}")
        return
    api = _win32()
    hwnd = _widget_hwnd(widget)
    placed = api.user32.SetWindowPos(
        hwnd,
        ctypes.c_void_p(-1),
        int(x),
        int(y),
        int(width),
        int(height),
        0x0040,
    )
    if placed:
        return
    widget.geometry(f"{int(width)}x{int(height)}+0+0")
    try:
        widget.attributes("-fullscreen", True)
    except tk.TclError:
        pass


def ensure_tk():
    global tk, filedialog
    if tk is not None:
        return
    import tkinter as tk_mod
    from tkinter import filedialog as filedialog_mod

    tk = tk_mod
    filedialog = filedialog_mod


def _pick_font(families: set[str], options: tuple[str, ...], size: int, weight: str | None = None):
    name = next((item for item in options if item in families), options[-1])
    if weight:
        return (name, size, weight)
    return (name, size)


def choose_fonts(root):
    import tkinter.font as tkfont

    families = set(tkfont.families(root))
    serif = ("Palatino Linotype", "Palatino", "Georgia", "Times New Roman")
    sans = ("Segoe UI", "Calibri", "Tahoma", "Arial")
    return {
        "serif": _pick_font(families, serif, 22),
        "serif_small": _pick_font(families, serif, 16),
        "serif_num": _pick_font(families, serif, 18),
        "sans": _pick_font(families, sans, 11),
        "sans_small": _pick_font(families, sans, 9),
        "eyebrow": _pick_font(families, sans, 10),
    }


def apply_scaling(root):
    if os.name != "nt":
        return
    dpi = 96
    try:
        dpi = int(ctypes.windll.user32.GetDpiForSystem())
    except (AttributeError, OSError, ValueError):
        dpi = 96
    if dpi > 0:
        root.tk.call("tk", "scaling", dpi / 72.0)


def photo_from_rgb(rgb: bytes, width: int, height: int):
    header = f"P6\n{int(width)} {int(height)}\n255\n".encode("ascii")
    descriptor, path = tempfile.mkstemp(prefix="livro-", suffix=".ppm")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(header)
            handle.write(rgb)
        try:
            return tk.PhotoImage(file=path, format="ppm")
        except tk.TclError:
            return tk.PhotoImage(file=path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _style_button(button, bg: str, fg: str, font):
    button.configure(
        bg=bg,
        fg=fg,
        activebackground=bg,
        activeforeground=fg,
        relief="flat",
        bd=0,
        highlightthickness=2,
        highlightbackground=bg,
        highlightcolor=FOCUS,
        padx=16,
        pady=8,
        cursor="hand2",
        font=font,
    )


def _style_delete(button, font):
    button.configure(
        bg="#14100c",
        fg="#ffffff",
        activebackground=INK,
        activeforeground="#ffffff",
        relief="flat",
        bd=0,
        highlightthickness=0,
        padx=8,
        pady=2,
        cursor="hand2",
        font=font,
    )


def _downloads_dir() -> str:
    path = os.path.join(os.path.expanduser("~"), "Downloads")
    if os.path.isdir(path):
        return path
    return os.path.expanduser("~")


def place_book(root):
    root.update_idletasks()
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    width = max(440, min(820, screen_w // 2 - 32))
    height = max(520, screen_h - 96)
    x = max(0, screen_w - width - 16)
    root.geometry(f"{width}x{height}+{x}+24")
    root.minsize(420, 480)


class SnipOverlay:
    def __init__(self, root, rgb: bytes, width: int, height: int, origin, on_done, on_cancel):
        self.rgb = rgb
        self.img_w = width
        self.img_h = height
        self.origin = origin
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.finished = False
        self.start = None
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="black", cursor="crosshair")
        dim = rgb.translate(DIM_TABLE)
        self.photo = photo_from_rgb(dim, width, height)
        self.canvas = tk.Canvas(
            self.win,
            width=width,
            height=height,
            highlightthickness=0,
            bd=0,
            bg="black",
            cursor="crosshair",
        )
        self.canvas.pack()
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.canvas.create_rectangle(0, height - 44, width, height, fill=BG, outline="", tags="hint")
        self.canvas.create_text(
            width / 2,
            height - 22,
            text="Arraste um retângulo · Esc cancela",
            fill=CREAM,
            font=("Segoe UI", 14),
            tags="hint",
        )
        place_window(self.win, origin[0], origin[1], width, height)
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.motion)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        self.win.bind("<Escape>", self.cancel)
        self.canvas.bind("<ButtonPress-3>", self.cancel)
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self.win.after(30, self._focus)

    def _focus(self):
        if self.finished:
            return
        self.win.lift()
        self.win.focus_force()
        try:
            self.win.grab_set()
        except tk.TclError:
            pass

    def _clamp(self, x: int, y: int):
        x = max(0, min(self.img_w - 1, int(x)))
        y = max(0, min(self.img_h - 1, int(y)))
        return x, y

    def _point(self, event):
        if getattr(event, "widget", None) is self.canvas:
            return self._clamp(event.x, event.y)
        x = self.win.winfo_pointerx() - self.canvas.winfo_rootx()
        y = self.win.winfo_pointery() - self.canvas.winfo_rooty()
        return self._clamp(x, y)

    def press(self, event):
        if self.finished:
            return
        self.canvas.delete("hint")
        self.start = self._point(event)
        self._draw(self.start, self.start)

    def motion(self, event):
        if self.finished or self.start is None:
            return
        self._draw(self.start, self._point(event))

    def _draw(self, start, current):
        x0, y0 = start
        x1, y1 = current
        self.canvas.delete("sel")
        self.canvas.create_rectangle(x0, y0, x1, y1, outline=FOCUS, width=3, tags="sel")
        label_x = min(x0, x1) + 8
        label_y = min(y0, y1) - 8
        anchor = "sw"
        if label_y < 52:
            label_y = max(y0, y1) + 8
            anchor = "nw"
        self.canvas.create_text(
            label_x,
            label_y,
            anchor=anchor,
            text=f"{abs(x1 - x0) + 1} × {abs(y1 - y0) + 1}",
            fill="#fff7f3",
            font=("Segoe UI", 12, "bold"),
            tags="sel",
        )

    def release(self, event):
        if self.finished or self.start is None:
            return
        end = self._point(event)
        rect = normalize_rect(self.start[0], self.start[1], end[0], end[1])
        self.start = None
        if rect is not None:
            rect = clamp_rect(*rect, (0, 0, self.img_w, self.img_h))
        if rect is None:
            self.canvas.delete("sel")
            return
        self._finish_crop(rect)

    def _finish_crop(self, rect):
        if self.finished:
            return
        self.finished = True
        try:
            x, y, width, height = rect
            cropped = crop_rgb(self.rgb, self.img_w, self.img_h, x, y, width, height)
        except Exception:
            self._close()
            self.on_cancel()
            return
        self._close()
        self.on_done(cropped, width, height)

    def cancel(self, _event=None):
        if self.finished:
            return
        self.finished = True
        self._close()
        self.on_cancel()

    def _close(self):
        self.rgb = b""
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()


class BookApp:
    def __init__(self, root):
        self.root = root
        self.shots: list[Shot] = []
        self.seq = 0
        self.snipping = False
        self.saving = False
        self.overlay = None
        self.last_width = 0
        self.resize_job = None
        self.delete_buttons = []
        self.fonts = choose_fonts(root)
        root.title("Livro de prints")
        root.configure(bg=BG)
        self._build()
        place_book(root)
        self.set_status("Recortar cobre a tela. Arraste um retângulo; ele entra no fim da pilha.")
        self.sync()
        root.bind("<Configure>", self._on_root_configure)
        root.bind_all("<MouseWheel>", self._on_wheel)
        root.after(80, self.render)

    def _build(self):
        header = tk.Frame(self.root, bg=BG2)
        header.pack(side="top", fill="x")
        self.count = tk.Label(
            header,
            text="CADERNO · 0 PRINTS",
            bg=BG2,
            fg=MUTED,
            font=self.fonts["eyebrow"],
            anchor="w",
        )
        self.count.pack(anchor="w", padx=18, pady=(14, 0))
        tk.Label(
            header,
            text="Livro de prints",
            bg=BG2,
            fg=CREAM,
            font=self.fonts["serif"],
            anchor="w",
        ).pack(anchor="w", padx=18)
        self.lead = tk.Label(
            header,
            text="O primeiro print fica no topo. Cada um seguinte entra logo abaixo.",
            bg=BG2,
            fg=FAINT,
            font=self.fonts["sans"],
            anchor="w",
            justify="left",
            wraplength=480,
        )
        self.lead.pack(anchor="w", padx=18, pady=(4, 0))
        actions = tk.Frame(header, bg=BG2)
        actions.pack(anchor="w", padx=18, pady=(12, 0))
        self.shoot = tk.Button(actions, text="Recortar", command=self.recortar)
        _style_button(self.shoot, CREAM, INK, self.fonts["sans"])
        self.shoot.pack(side="left")
        self.save = tk.Button(actions, text="Baixar PDF", command=self.baixar)
        _style_button(self.save, STAMP, STAMP_INK, self.fonts["sans"])
        self.save.pack(side="left", padx=(8, 0))
        self.status = tk.Label(
            header,
            text=" ",
            bg=BG2,
            fg=FAINT,
            font=self.fonts["sans"],
            anchor="w",
            justify="left",
            wraplength=480,
        )
        self.status.pack(anchor="w", fill="x", padx=18, pady=(8, 12))
        tk.Frame(self.root, bg=LINE, height=1).pack(fill="x")

        body = tk.Frame(self.root, bg=BG)
        body.pack(side="top", fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=BG, highlightthickness=0, bd=0)
        self.scroll = tk.Scrollbar(body, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self.inner_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def set_status(self, text: str):
        self.status.configure(text=text if text else " ")

    def column_width(self) -> int:
        width = self.canvas.winfo_width()
        if width <= 1:
            width = self.root.winfo_width()
        return max(160, width - 130)

    def sync(self):
        count = len(self.shots)
        self.count.configure(text=f"CADERNO · {prints_label(count).upper()}")
        can_save = count > 0 and not self.saving and not self.snipping
        can_snip = not self.saving and not self.snipping
        self.save.configure(state=("normal" if can_save else "disabled"), cursor=("hand2" if can_save else "arrow"))
        self.shoot.configure(state=("normal" if can_snip else "disabled"), cursor=("hand2" if can_snip else "arrow"))

    def preview_for(self, shot: Shot, max_w: int):
        max_w = max(1, int(max_w))
        if shot.preview is not None and shot.preview_for == max_w:
            return shot.preview
        new_w, new_h = fit_width(shot.width, shot.height, max_w)
        rgb = shot.rgb if new_w == shot.width and new_h == shot.height else resize_rgb(
            shot.rgb, shot.width, shot.height, new_w, new_h
        )
        shot.preview = photo_from_rgb(rgb, new_w, new_h)
        shot.preview_for = max_w
        return shot.preview

    def render(self, scroll: str = "keep", focus_index: int | None = None):
        first, _last = self.canvas.yview()
        for child in self.inner.winfo_children():
            child.destroy()
        self.delete_buttons = []
        max_w = self.column_width()
        if not self.shots:
            self._show_empty(max_w)
        else:
            self._show_stack(max_w)
        self.inner.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all") or (0, 0, 0, 0))
        if scroll == "end":
            self.canvas.yview_moveto(1)
        else:
            self.canvas.yview_moveto(first)
        if focus_index is not None and self.delete_buttons:
            self.delete_buttons[min(focus_index, len(self.delete_buttons) - 1)].focus_set()
        self.sync()

    def _show_empty(self, max_w: int):
        card = tk.Frame(self.inner, bg=PAPER)
        card.pack(fill="x", padx=18, pady=28)
        tk.Label(
            card,
            text="Nenhum print ainda. Use Recortar e arraste um retângulo na tela.",
            bg=PAPER,
            fg="#3a3128",
            font=self.fonts["serif_small"],
            wraplength=max(240, max_w),
            justify="left",
            anchor="w",
            padx=18,
            pady=22,
        ).pack(fill="x")

    def _show_stack(self, max_w: int):
        paper = tk.Frame(self.inner, bg=PAPER)
        paper.pack(fill="x", padx=18, pady=18)
        for number, shot in enumerate(self.shots, start=1):
            row = tk.Frame(paper, bg=PAPER)
            row.pack(fill="x")
            tk.Label(
                row,
                text=str(number),
                bg=PAPER,
                fg=NUM,
                font=self.fonts["serif_num"],
                width=4,
                anchor="n",
            ).pack(side="left", anchor="n", pady=10)
            tk.Frame(row, bg=RULE, width=1).pack(side="left", fill="y")
            body = tk.Frame(row, bg="#111111")
            body.pack(side="left", fill="x", expand=True)
            preview = self.preview_for(shot, max_w)
            image = tk.Label(body, image=preview, bg="#111111", bd=0)
            image.pack(fill="x")
            image.image = preview
            button = tk.Button(body, text="Excluir", command=lambda shot_id=shot.id: self.remove(shot_id))
            _style_delete(button, self.fonts["sans_small"])
            button.place(relx=1.0, x=-8, y=8, anchor="ne")
            self.delete_buttons.append(button)

    def remove(self, shot_id: int):
        index = next((i for i, shot in enumerate(self.shots) if shot.id == shot_id), -1)
        if index < 0:
            return
        del self.shots[index]
        self.render(scroll="keep", focus_index=index if self.shots else None)
        if self.shots:
            self.set_status(f"{prints_label(len(self.shots))} na pilha.")
        else:
            self.set_status("Print excluído.")

    def recortar(self):
        if self.snipping or self.saving:
            return
        if os.name != "nt":
            self.set_status("O recorte da tela só funciona no Windows.")
            return
        self.snipping = True
        self.sync()
        self.set_status("A preparar o recorte…")
        self.root.withdraw()
        self.root.update_idletasks()
        self.root.after(CAPTURE_SETTLE_MS, self._open_overlay)

    def _open_overlay(self):
        try:
            origin_x, origin_y, rgb, width, height = capture_screen()
            self.overlay = SnipOverlay(
                self.root,
                rgb,
                width,
                height,
                (origin_x, origin_y),
                self._on_crop,
                self._on_cancel,
            )
        except Exception as exc:
            print(f"captura falhou: {exc}", file=sys.stderr)
            self._end_snip()
            self.set_status("Não foi possível capturar a tela.")

    def _on_crop(self, rgb: bytes, width: int, height: int):
        self.seq += 1
        self.shots.append(Shot(self.seq, width, height, rgb))
        self._end_snip()
        self.render(scroll="end")
        self.set_status(f"Print {len(self.shots)} guardado no fim da pilha.")

    def _on_cancel(self):
        self._end_snip()
        self.set_status("Recorte cancelado.")

    def _end_snip(self):
        self.snipping = False
        self.overlay = None
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except tk.TclError:
            pass
        self.root.update_idletasks()
        self.sync()

    def baixar(self):
        if self.saving or self.snipping or not self.shots:
            return
        self.saving = True
        self.sync()
        self.set_status("A gerar o PDF…")
        self.root.update_idletasks()
        try:
            data = build_pdf(self.shots)
        except Exception as exc:
            print(f"pdf falhou: {exc}", file=sys.stderr)
            self.saving = False
            self.sync()
            self.set_status("Não foi possível gerar o PDF.")
            return
        self.saving = False
        self.sync()
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Baixar PDF",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            initialfile="livro-de-prints.pdf",
            initialdir=_downloads_dir(),
        )
        if not path:
            self.set_status(" ")
            return
        try:
            with open(path, "wb") as handle:
                handle.write(data)
        except OSError as exc:
            print(f"gravar pdf falhou: {exc}", file=sys.stderr)
            self.set_status("Não foi possível gerar o PDF.")
            return
        self.set_status("PDF baixado. A ordem é a mesma da pilha, de cima para baixo.")

    def _on_root_configure(self, event):
        if event.widget is not self.root:
            return
        wrap = max(240, event.width - 40)
        self.lead.configure(wraplength=wrap)
        self.status.configure(wraplength=wrap)

    def _on_inner_configure(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all") or (0, 0, 0, 0))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.inner_id, width=event.width)
        if abs(event.width - self.last_width) < 24:
            return
        self.last_width = event.width
        if self.resize_job is not None:
            self.root.after_cancel(self.resize_job)
        self.resize_job = self.root.after(120, self._resized)

    def _resized(self):
        self.resize_job = None
        if not self.snipping:
            self.render(scroll="keep")

    def _on_wheel(self, event):
        if self.snipping:
            return
        delta = getattr(event, "delta", 0)
        if delta:
            self.canvas.yview_scroll(int(-delta / 120), "units")


def main():
    enable_dpi_awareness()
    try:
        ensure_tk()
    except ImportError:
        sys.stderr.write(
            "O Python não inclui o tkinter. Instale o Python de python.org e rode de novo.\n"
        )
        raise SystemExit(1)
    try:
        root = tk.Tk()
    except tk.TclError:
        sys.stderr.write("Não foi possível abrir a janela.\n")
        raise SystemExit(1)
    apply_scaling(root)
    BookApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
