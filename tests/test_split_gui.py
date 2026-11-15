"""The split tab in the real window: the summary for the selected file, typing a range,
a run, errors per file, cancel, saved options, the language switch, and that no worker
thread holds the window. Dialogs are faked, settings go to a temporary file, the update
server is never asked."""
from __future__ import annotations

import json
import os
import sys
import threading
import time

import pymupdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures as fx  # noqa: E402
import i18n  # noqa: E402
from test_gui import close, env, idle, log_text, pump  # noqa: E402,F401  (env is a fixture)
from test_split import numbered_text_pdf  # noqa: E402


def saved(env) -> dict:
    p = env["settings"]
    return json.loads(open(p, encoding="utf-8").read()) if os.path.exists(p) else {}


def summary(app) -> str:
    return app.lbl_split_summary.cget("text")


def settled(app) -> bool:
    return not app._split_planning and app.split_job is None and bool(summary(app))


def split_app(env, *paths):
    app = env["gui"].App()
    app.nb.select(2)
    app.root.update()
    if paths:
        app.add_paths(list(paths))
    return app


def test_summary_follows_way_options_and_selected_file(env, tmp_path):
    a = numbered_text_pdf(str(tmp_path / "a.pdf"), 25, [[1, "One", 1], [1, "Two", 11]])
    b = numbered_text_pdf(str(tmp_path / "b.pdf"), 4)
    app = split_app(env, a, b)
    app.var_split_mode.set("every")
    app.var_split_every.set("10")
    app._on_split_option()
    assert pump(app, lambda: settled(app))
    assert summary(app) == "Selected file → 3 files (10, 10, 5 pages)"
    app.var_split_mode.set("bookmarks")
    app._on_split_option()
    assert pump(app, lambda: settled(app) and "2 files" in summary(app))
    assert summary(app) == "Selected file → 2 files (10, 15 pages)"
    app._select_file(1)                                     # b.pdf has no bookmarks
    assert pump(app, lambda: settled(app) and "b.pdf" in summary(app))
    assert summary(app) == "No top-level bookmarks, cannot split by bookmark: b.pdf"
    assert str(app.lbl_split_summary.cget("foreground")) == env["gui"].BAD_FG
    gen = app.split_gen
    app.nb.select(1)
    app.root.update()
    app._select_file(0)
    pump(app, lambda: False, timeout=0.6)
    assert app._split_planning is False and app.split_job is None    # off the split tab nothing is planned
    assert app.split_gen > gen
    close(app)


def test_typing_a_range_selects_that_way_and_checks_it(env, tmp_path):
    a = numbered_text_pdf(str(tmp_path / "a.pdf"), 6)
    app = split_app(env, a)
    app.var_split_mode.set("every")
    app.var_split_ranges.set("1-2, 9")
    app._on_split_typed("ranges")
    assert app.var_split_mode.get() == "ranges"
    assert pump(app, lambda: settled(app))
    assert summary(app) == "Page range does not fit: 9 (pages 1-6)"
    app.var_split_ranges.set("1-2, 3-")
    app._on_split_typed("ranges")
    assert pump(app, lambda: settled(app) and "files" in summary(app))
    assert summary(app) == "Selected file → 2 files (2, 4 pages)"
    close(app)


def test_run_splits_every_file_and_reports(env, tmp_path):
    a = numbered_text_pdf(str(tmp_path / "a.pdf"), 5)
    b = numbered_text_pdf(str(tmp_path / "b.pdf"), 3)
    locked = fx.encrypted_pdf(str(tmp_path / "locked.pdf"))
    before = (fx.sha1(a), fx.sha1(b))
    app = split_app(env, a, locked, b)
    assert env["warn"] and app.files == [a, b]                         # refused when added
    app.var_split_mode.set("every")
    app.var_split_every.set("2")
    app.run()
    assert str(app.btn_run.cget("state")) == "disabled"
    assert pump(app, lambda: bool(env["info"]), timeout=60)
    assert env["info"] == ["Done: 2 files split into 5 files"]
    names = sorted(os.path.basename(o) for o in app.outputs)
    assert names == ["a_part01.pdf", "a_part02.pdf", "a_part03.pdf", "b_part01.pdf", "b_part02.pdf"]
    text = log_text(app)
    assert "5 pages → 3 files (2, 2, 1 pages)" in text and "3 pages → 2 files (2, 1 pages)" in text
    assert app.lbl_status.cget("text") == "Done" and str(app.btn_open.cget("state")) == "normal"
    assert (fx.sha1(a), fx.sha1(b)) == before and os.path.exists(locked)
    close(app)


def test_files_that_cannot_be_split_are_logged_and_counted(env, tmp_path):
    with_marks = numbered_text_pdf(str(tmp_path / "marks.pdf"), 4, [[1, "A", 1], [1, "B", 3]])
    plain = numbered_text_pdf(str(tmp_path / "plain.pdf"), 4)
    one = numbered_text_pdf(str(tmp_path / "one.pdf"), 2, [[1, "Only", 1]])
    app = split_app(env, with_marks, plain, one)
    app.var_split_mode.set("bookmarks")
    app.run()
    assert pump(app, lambda: bool(env["info"]), timeout=60)
    assert env["info"] == ["Done: 1 files split into 2 files\n1 files were skipped. See the log."]
    text = log_text(app)
    assert "No top-level bookmarks, cannot split by bookmark: plain.pdf" in text
    assert "Nothing to split (it would be the original as one file): one.pdf" in text
    close(app)


def test_a_range_one_file_cannot_hold_stops_the_run_before_it_starts(env, tmp_path):
    a = numbered_text_pdf(str(tmp_path / "a.pdf"), 10)
    b = numbered_text_pdf(str(tmp_path / "b.pdf"), 3)
    app = split_app(env, a, b)
    app.var_split_mode.set("ranges")
    app.var_split_ranges.set("1-5, 6-10")
    app.run()
    assert env["error"] == ["Page range does not fit: 1-5 (b.pdf, pages 1-3)"]
    assert app.worker is None and sorted(os.listdir(tmp_path))[:2] == ["a.pdf", "b.pdf"]
    close(app)


def test_cancel_during_a_split_leaves_nothing(env, tmp_path, monkeypatch):
    a = numbered_text_pdf(str(tmp_path / "a.pdf"), 8)
    gui = env["gui"]
    real = gui.pdfops._write_part

    def slow(*args, **kw):
        time.sleep(0.4)
        return real(*args, **kw)

    monkeypatch.setattr(gui.pdfops, "_write_part", slow)
    app = split_app(env, a)
    app.var_split_mode.set("every")
    app.var_split_every.set("2")
    app.run()
    pump(app, lambda: app.progress["value"] > 0, timeout=20)
    app.cancel()
    assert pump(app, lambda: app.lbl_status.cget("text") == "Cancelled", timeout=30)
    assert "Cancelled: a.pdf (no output written)" in log_text(app)
    assert sorted(n for n in os.listdir(tmp_path) if n != "settings") == ["a.pdf"]
    assert env["info"] == []
    close(app)


def test_options_and_tab_are_remembered_but_not_the_ranges(env, tmp_path):
    app = split_app(env)
    app.var_split_mode.set("size")
    app.var_split_mb.set("25")
    app.var_split_ranges.set("1-3")
    app.var_split_keep.set(False)
    app._on_split_option()
    close(app)
    s = saved(env)
    assert s["mode"] == "split"
    assert s["split"] == {"mode": "size", "every": 10, "max_mb": 25.0, "keep_bookmarks": False}
    app = env["gui"].App()
    assert app.mode() == "split" and app.var_split_mode.get() == "size" and app.var_split_mb.get() == "25"
    assert app.var_split_ranges.get() == "" and app.var_split_keep.get() is False
    close(app)


def test_odd_split_settings_and_odd_typing_fall_back_to_defaults(env, tmp_path):
    os.makedirs(os.path.dirname(env["settings"]), exist_ok=True)
    with open(env["settings"], "w", encoding="utf-8") as f:
        json.dump({"mode": "split", "split": {"mode": 3, "every": "lots", "max_mb": True, "keep_bookmarks": "no"}}, f)
    app = env["gui"].App()
    assert app.mode() == "split"
    assert app.split_options() == env["gui"].SplitOptions("every", "", 10, 10.0, True)
    app.var_split_every.set("abc")
    app.var_split_mb.set("-4")
    opts = app.split_options()
    assert opts.every == 10 and opts.max_mb == 10.0
    close(app)


def test_language_switch_rewords_the_tab_and_the_summary(env, tmp_path):
    a = numbered_text_pdf(str(tmp_path / "a.pdf"), 6)
    app = split_app(env, a)
    app.var_split_mode.set("every")
    app.var_split_every.set("3")
    app._on_split_option()
    assert pump(app, lambda: settled(app))
    app.cmb_lang.current(i18n.LANGS.index("ko"))
    app._on_lang()
    assert app.nb.tab(2, "text") == "나누기"
    assert summary(app) == "선택한 파일 → 2개 파일 (쪽 수 3, 3)"
    assert app.rb_split["size"].cget("text") == "파일 크기 상한:"
    close(app)


def test_split_threads_hold_no_tk_object(env, tmp_path, monkeypatch):
    import tkinter as tk

    a = numbered_text_pdf(str(tmp_path / "a.pdf"), 6)
    gui = env["gui"]
    started = []

    class Capture(threading.Thread):
        def __init__(self, *args, target=None, kwargs=None, **k):
            super().__init__(*args, target=target, kwargs=kwargs, **k)
            started.append((target, k.get("args", ()), kwargs or {}))

    monkeypatch.setattr(gui.threading, "Thread", Capture)
    monkeypatch.setattr(gui.update_check, "check_async", lambda *args, **kw: False)
    app = split_app(env, a)                                 # preview + split plan threads
    app.var_split_mode.set("every")
    app.var_split_every.set("2")
    app._on_split_option()
    pump(app, lambda: False, timeout=0.6)
    app.run()                                               # job thread
    assert {t.__name__ for t, _, _ in started} == {"_preview_worker", "_split_plan_worker", "_job_worker"}
    for target, args, kwargs in started:
        assert target.__closure__ is None
        for value in list(args) + list(kwargs.values()):
            assert not isinstance(value, (gui.App, tk.Misc, tk.Variable)), target.__name__
    pump(app, idle, timeout=60)
    close(app)


def test_window_closes_cleanly_while_a_plan_is_out(env, tmp_path, monkeypatch):
    import gc
    import weakref

    a = numbered_text_pdf(str(tmp_path / "a.pdf"), 6)
    gui = env["gui"]
    gate = threading.Event()
    real = gui.pdfops.preview_split

    def slow(*args, **kw):
        gate.wait(5)
        return real(*args, **kw)

    monkeypatch.setattr(gui.pdfops, "preview_split", slow)
    app = split_app(env, a)
    pump(app, lambda: False, timeout=0.6)                   # the plan thread is waiting now
    ref = weakref.ref(app)
    app._on_close()
    try:
        app.root.update()
    except Exception:
        pass
    del app
    gc.collect()
    assert ref() is None
    gate.set()
    time.sleep(0.3)


def test_split_parts_open_and_carry_the_pages(env, tmp_path):
    a = numbered_text_pdf(str(tmp_path / "a.pdf"), 4)
    app = split_app(env, a)
    app.var_split_mode.set("ranges")
    app.var_split_ranges.set("3-4, 1")
    app.run()
    assert pump(app, lambda: bool(env["info"]), timeout=60)
    first = pymupdf.open(app.outputs[0])
    assert len(first) == 2 and "page 003" in first[0].get_text()
    first.close()
    close(app)
