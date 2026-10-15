# -*- coding: utf-8 -*-
"""Screenshots of the real window for the READMEs and the tools page.

    python tests/shots.py <out_dir> [ko en zh-CN ja]

The App is driven in-process (callbacks, not fake mouse clicks) and only the
window is captured with PrintWindow, so nothing else on the screen is touched.
Settings go to a temporary file (the user's settings.json is never written),
the update server is never asked (the notice scene uses a faked answer), and
the sample PDFs are copied to a temporary folder first so no result lands in
the repository.

Files written per language:  gui_<lang>.png (merge tab), shrink_<lang>.png.
For Korean also: done_ko.png (after a shrink run), update_notice.png, and the
layout check that the notice moves nothing above it.
"""
from __future__ import annotations

import ctypes
import functools
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from ctypes import wintypes

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# under the repository's ignored build/ folder: a %TEMP% path would put the user name into the screenshots
WORK = tempfile.mkdtemp(prefix="shots_", dir=os.path.join(ROOT, "build")) if os.path.isdir(os.path.join(ROOT, "build")) or not os.makedirs(os.path.join(ROOT, "build"), exist_ok=True) else None
os.environ["PDF_MERGE_SPLIT_SETTINGS"] = os.path.join(WORK, "settings", "settings.json")

import i18n  # noqa: E402

i18n.SETTINGS_PATH = os.environ["PDF_MERGE_SPLIT_SETTINGS"]

import update_check as uc  # noqa: E402
from PIL import Image  # noqa: E402

ATTEMPTS: list[str] = []


def _blocked(req, *a, **k):
    ATTEMPTS.append(getattr(req, "full_url", str(req)))
    raise urllib.error.URLError("network blocked in shots.py")


urllib.request.urlopen = _blocked
uc.fetch.__kwdefaults__["opener"] = _blocked
uc.check_async.__kwdefaults__["fetcher"] = lambda slug, version: None

import gui  # noqa: E402

user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
NOTICE = {"latest": "0.2.0", "updateAvailable": True, "url": "https://chaekgalpi.co.kr/tools/pdfmerge"}


class BI(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def grab(root) -> Image.Image:
    """The window's client area as an RGB image (PrintWindow, works when covered)."""
    root.update()
    hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
    wr = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wr))
    w, h = wr.right - wr.left, wr.bottom - wr.top
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 2)
    bi = BI(ctypes.sizeof(BI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1).convert("RGB")
    cr = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(cr))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    x0, y0 = pt.x - wr.left, pt.y - wr.top
    img = img.crop((x0, y0, x0 + cr.right, y0 + cr.bottom))
    if sum(img.resize((8, 8)).convert("L").tobytes()) / 64 < 5:
        raise SystemExit("captured image is black: is the screen locked?")
    return img


def pump(app, cond, timeout):
    end = time.time() + timeout
    box = {"ok": False}

    def poll():
        try:
            if cond():
                box["ok"] = True
                app.root.quit()
                return
        except Exception:
            pass
        if time.time() > end:
            app.root.quit()
        else:
            app.root.after(30, poll)

    app.root.after(30, poll)
    app.root.mainloop()
    return box["ok"]


def samples() -> list[str]:
    folder = os.path.join(WORK, "pdf")
    os.makedirs(folder, exist_ok=True)
    return [shutil.copy(os.path.join(ROOT, "samples", n), folder)
            for n in ("sample_report_a.pdf", "sample_report_b.pdf", "sample_scan.pdf")]


def shoot(lang: str, out: str, notice: bool) -> None:
    i18n.init(lang)
    i18n.set_lang(lang, persist=False)
    gui.update_check.check_async = functools.partial(uc.check_async, fetcher=lambda s, v: NOTICE if notice else None)
    gui.messagebox.showinfo = lambda *a, **k: None
    files = samples()
    app = gui.App()
    app.root.geometry("+40+40")
    app.nb.select(0)
    app.add_paths(files)
    app._select_file(1)
    app.var_pages.set("1-3")
    app._select_file(0)
    pump(app, lambda: app._preview is not None, 20)
    pump(app, lambda: False, 0.6)
    before = grab(app.root)
    before.save(os.path.join(out, f"gui_{lang}.png"))
    if notice:
        # The notice arrives 1.5 s after start. Only the stretching middle row (options |
        # preview) may give up height for it: the header and file list above must be
        # pixel-identical, and the log and bottom bar must merely move up, unchanged.
        ry = app.root.winfo_rooty()
        files_bottom = app.lst_files.master.winfo_rooty() + app.lst_files.master.winfo_height() - ry
        log_top_before = app.txt_log.master.winfo_rooty() - ry
        pump(app, lambda: bool(app.frm_update.winfo_manager()), 10)
        pump(app, lambda: False, 0.4)
        after = grab(app.root)
        after.save(os.path.join(out, "update_notice.png"))
        log_top_after = app.txt_log.master.winfo_rooty() - ry
        notice_top = app.frm_update.winfo_rooty() - ry
        band = notice_top - log_top_after
        top_same = before.crop((0, 0, before.width, files_bottom)).tobytes() == after.crop((0, 0, after.width, files_bottom)).tobytes()
        low_same = (before.crop((0, log_top_before, before.width, log_top_before + band)).tobytes()
                    == after.crop((0, log_top_after, after.width, log_top_after + band)).tobytes())
        print(f"notice: window {after.width}x{after.height}; header+files ({files_bottom}px) "
              f"{'identical' if top_same else 'DIFFERENT'}; log+buttons ({band}px, moved up "
              f"{log_top_before - log_top_after}px) {'identical' if low_same else 'DIFFERENT'}")
        app._update_later()
    if lang == "ko":
        app.root.geometry("1200x800")
        pump(app, lambda: False, 0.6)
        grab(app.root).convert("RGB").save(os.path.join(out, "hero_1200.jpg"), "JPEG", quality=90)
        app.root.geometry("1060x760")
        pump(app, lambda: False, 0.4)
    app.nb.select(1)
    pump(app, lambda: app._estimate_kwargs is not None and not app._estimating, 60)
    pump(app, lambda: False, 0.4)
    grab(app.root).save(os.path.join(out, f"shrink_{lang}.png"))
    if lang == "ko":
        app.clear_files()
        app.add_paths([files[2]])
        pump(app, lambda: app._estimate_kwargs is not None and not app._estimating, 60)
        app.run()
        pump(app, lambda: app.lbl_status.cget("text") == i18n.t("status_done"), 120)
        pump(app, lambda: False, 0.5)
        grab(app.root).save(os.path.join(out, "done_ko.png"))
    pump(app, lambda: not any(t.is_alive() for t in __import__('threading').enumerate()
                              if t is not __import__('threading').main_thread()), 30)
    app._on_close()
    try:
        app.root.update()
    except Exception:
        pass


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    out = os.path.abspath(argv[0])
    os.makedirs(out, exist_ok=True)
    langs = argv[1:] or list(i18n.LANGS)
    for lang in langs:
        shoot(lang, out, notice=(lang == "ko"))
        print("done", lang)
    print(f"requests that tried to leave: {len(ATTEMPTS)}")
    shutil.rmtree(WORK, ignore_errors=True)
    return 1 if ATTEMPTS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
