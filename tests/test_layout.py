"""Nothing in the window is cut off: every language, both tabs, the default and the
smallest size, with and without the new-version notice. Settings go to a temporary file
and the update check is faked, so neither the user's settings nor the real server are touched."""
from __future__ import annotations

import functools
import os
import shutil
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import i18n  # noqa: E402
import update_check as uc  # noqa: E402

SAMPLE = os.path.join(ROOT, "samples", "sample_report_a.pdf")
ANSWER = {"latest": "0.2.0", "updateAvailable": True, "url": "https://chaekgalpi.co.kr/tools/pdfmerge"}


@pytest.fixture
def make_app(tmp_path, monkeypatch, isolated_settings):
    import gui

    apps = []
    sample = str(shutil.copy(SAMPLE, tmp_path / "a.pdf"))

    def make(lang: str, notice: bool):
        i18n.init(lang)
        answer = ANSWER if notice else {"updateAvailable": False}
        monkeypatch.setattr(gui.update_check, "check_async", functools.partial(uc.check_async, fetcher=lambda s, v: answer))
        app = gui.App([sample])
        apps.append(app)
        return app

    yield make
    import gc
    import threading

    for app in apps:
        pump(app, lambda: not any(t.is_alive() for t in threading.enumerate() if t is not threading.main_thread()), 15)
        app._on_close()
        try:
            app.root.update()
        except Exception:
            pass
    gc.collect()


def pump(app, cond, timeout=10.0):
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


def problems(app) -> list[str]:
    root = app.root
    root.update_idletasks()
    win = (root.winfo_rootx(), root.winfo_rooty(), root.winfo_rootx() + root.winfo_width(), root.winfo_rooty() + root.winfo_height())

    def inside(w, box):
        x, y = w.winfo_rootx(), w.winfo_rooty()
        return x >= box[0] - 1 and y >= box[1] - 1 and x + w.winfo_width() <= box[2] + 1 and y + w.winfo_height() <= box[3] + 1

    out = []
    widgets = [(w, key) for w, key, attr in app._texts if attr == "text" and w.winfo_class() != "TLabelframe"]
    widgets += [(app.lbl_update, "lbl_update"), (app.lbl_merge_summary, "lbl_merge_summary"),
                (app.lbl_estimate, "lbl_estimate"), (app.lbl_pages_hint, "lbl_pages_hint"),
                (app.lbl_file_info, "lbl_file_info"), (app.lbl_status, "lbl_status")]
    for w, key in widgets:
        if not w.winfo_ismapped() or not w.cget("text"):
            continue
        if w.winfo_reqwidth() > w.winfo_width() + 1:
            out.append(f"{key}: text needs {w.winfo_reqwidth()}px, has {w.winfo_width()}")
        if w.winfo_reqheight() > w.winfo_height() + 1:
            out.append(f"{key}: squeezed to {w.winfo_height()}px of {w.winfo_reqheight()}")
        if not inside(w, win):
            out.append(f"{key}: outside the window")
    for i in range(2):
        if not app.nb.tab(i, "text"):
            out.append(f"tab {i}: no text")
    return out


@pytest.mark.parametrize("lang", i18n.LANGS)
@pytest.mark.parametrize("notice", [False, True], ids=["no-notice", "notice"])
def test_nothing_is_cut_off(make_app, lang, notice):
    app = make_app(lang, notice)
    assert pump(app, lambda: app._preview is not None)
    if notice:
        assert pump(app, lambda: bool(app.frm_update.winfo_manager()))
    for tab in (0, 1):
        app.nb.select(tab)
        for size in ("1060x760", "940x700"):
            app.root.geometry(size)
            pump(app, lambda: False, timeout=0.4)
            # on a busy machine the resize can still be settling here; a real cut-off stays
            pump(app, lambda: not problems(app), timeout=3)
            found = problems(app)
            assert not found, f"{lang} tab{tab} {size}: " + "; ".join(found)
