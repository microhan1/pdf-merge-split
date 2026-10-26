"""The real window: files in, reordering, page ranges, a merge and a shrink run, cancel,
closing while busy, and that no worker thread holds the window. Dialogs are faked,
settings go to a temporary file, the update server is never asked."""
from __future__ import annotations

import gc
import json
import os
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures as fx  # noqa: E402
import i18n  # noqa: E402

SAMPLES = os.path.join(ROOT, "samples")


@pytest.fixture
def env(monkeypatch, isolated_settings):
    i18n.init("en")
    import gui

    state = {"info": [], "warn": [], "error": [], "ask": True, "gui": gui, "settings": isolated_settings}
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda title, msg, **k: state["info"].append(msg))
    monkeypatch.setattr(gui.messagebox, "showwarning", lambda title, msg, **k: state["warn"].append(msg))
    monkeypatch.setattr(gui.messagebox, "showerror", lambda title, msg, **k: state["error"].append(msg))
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda title, msg, **k: state["ask"])
    yield state
    gc.collect()


def pump(app, cond, timeout=30.0):
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
            app.root.after(20, poll)

    app.root.after(20, poll)
    app.root.mainloop()
    return box["ok"]


def idle() -> bool:
    return not any(t.is_alive() for t in threading.enumerate() if t is not threading.main_thread())


def close(app):
    pump(app, idle, timeout=30)
    app._on_close()
    try:
        app.root.update()
    except Exception:
        pass


def log_text(app) -> str:
    return app.txt_log.get("1.0", "end")


def saved(env) -> dict:
    p = env["settings"]
    return json.loads(open(p, encoding="utf-8").read()) if os.path.exists(p) else {}


def copies(tmp_path, *names):
    import shutil

    return [str(shutil.copy(os.path.join(SAMPLES, n), tmp_path / n)) for n in names]


# ------------------------------------------------------------------ files
def test_files_are_listed_with_pages_and_sizes_and_previewed(env, tmp_path):
    a, b = copies(tmp_path, "sample_report_a.pdf", "sample_report_b.pdf")
    app = env["gui"].App()
    app.add_paths([a, b])
    assert app.files == [a, b]
    assert app.lst_files.get(0).startswith("1. sample_report_a.pdf  (6 pages")
    assert app.lst_files.get(1).startswith("2. sample_report_b.pdf  (4 pages")
    assert app.lst_files.curselection() == (0,) and app.preview_file == a
    assert pump(app, lambda: app._preview is not None, timeout=20)
    assert app.lbl_file_info.cget("text").startswith("6 pages")
    assert app.lbl_merge_summary.cget("text") == "2 files → 10 pages"
    assert "Added: sample_report_a.pdf (6 pages" in log_text(app)
    close(app)


def test_locked_empty_and_broken_files_are_refused_with_one_warning(env, tmp_path):
    a = fx.text_pdf(str(tmp_path / "a.pdf"), pages=1)
    locked = fx.encrypted_pdf(str(tmp_path / "locked.pdf"))
    zero = fx.zero_page_pdf(str(tmp_path / "zero.pdf"))
    broken = fx.not_a_pdf(str(tmp_path / "broken.pdf"))
    app = env["gui"].App()
    app.add_paths([locked, a, zero, broken, str(tmp_path / "missing.pdf")])
    assert app.files == [a]
    assert len(env["warn"]) == 1
    assert env["warn"][0] == ("Password-protected, skipping: locked.pdf\n"
                              "This file has no pages: zero.pdf\nCannot open file: broken.pdf")
    assert "Cannot open file: " in log_text(app) and "missing.pdf" in log_text(app)
    close(app)


def test_duplicates_folders_and_clear(env, tmp_path):
    sub = tmp_path / "f"
    sub.mkdir()
    a = fx.text_pdf(str(sub / "a.pdf"), pages=1)
    b = fx.text_pdf(str(sub / "b.PDF"), pages=1)
    app = env["gui"].App()
    app.add_paths([str(sub)])
    app.add_paths([a, b.lower()])
    assert app.files == [a, b]
    app.clear_files()
    assert app.files == [] and app.lst_files.size() == 0 and app.preview_file is None
    assert app.lbl_preview_msg.cget("text") == "Add a file to preview its first page"
    close(app)


def test_reorder_with_buttons_drag_and_remove(env, tmp_path):
    paths = [fx.text_pdf(str(tmp_path / f"{n}.pdf"), pages=1) for n in "abc"]
    app = env["gui"].App()
    app.add_paths(paths)
    app._select_file(2)
    app._move_selected(-1)
    assert [os.path.basename(p) for p in app.files] == ["a.pdf", "c.pdf", "b.pdf"]
    assert app.lst_files.curselection() == (1,)
    app._move_selected(-1)
    app._move_selected(-1)                                  # already at the top: no change
    assert [os.path.basename(p) for p in app.files] == ["c.pdf", "a.pdf", "b.pdf"]

    class Ev:
        def __init__(self, y):
            self.y = y

    app.root.update()
    y0 = app.lst_files.bbox(0)[1] + 2
    y2 = app.lst_files.bbox(2)[1] + 2
    app._on_list_press(Ev(y0))
    app._on_list_drag(Ev(y2))
    app._on_list_release(Ev(y2))
    assert [os.path.basename(p) for p in app.files] == ["a.pdf", "b.pdf", "c.pdf"]
    app._select_file(1)
    app.remove_selected()
    assert [os.path.basename(p) for p in app.files] == ["a.pdf", "c.pdf"] and app.preview_file == app.files[1]
    close(app)


# ------------------------------------------------------------------ page ranges
def test_page_range_is_checked_as_typed_and_shown_in_the_list(env, tmp_path):
    a, b = copies(tmp_path, "sample_report_a.pdf", "sample_report_b.pdf")
    app = env["gui"].App()
    app.add_paths([a, b])
    app._select_file(0)
    app.var_pages.set("1-9")
    assert app.lbl_pages_hint.cget("text") == "Page range does not fit: 1-9 (pages 1-6)"
    assert str(app.lbl_pages_hint.cget("foreground")) == env["gui"].BAD_FG
    assert app.specs.get(a, "") == ""
    app.var_pages.set("1-3, 6")
    assert app.specs[a] == "1-3, 6" and app.lst_files.get(0).endswith("[1-3, 6]")
    assert app.lbl_merge_summary.cget("text") == "2 files → 8 pages"
    assert str(app.lbl_pages_hint.cget("foreground")) == env["gui"].HINT_FG
    app._select_file(1)
    assert app.var_pages.get() == ""                        # each file keeps its own range
    app._select_file(0)
    assert app.var_pages.get() == "1-3, 6"
    close(app)


# ------------------------------------------------------------------ running
def test_merge_run_writes_the_file_and_reports(env, tmp_path):
    a, b = copies(tmp_path, "sample_report_a.pdf", "sample_report_b.pdf")
    before = (fx.sha1(a), fx.sha1(b))
    app = env["gui"].App()
    app.nb.select(0)
    app.add_paths([a, b])
    app._select_file(1)
    app.var_pages.set("2-3")
    app.run()
    assert str(app.btn_run.cget("state")) == "disabled" and str(app.btn_cancel.cget("state")) == "normal"
    assert pump(app, lambda: bool(env["info"]), timeout=60)
    assert env["info"] == ["Done: 2 files merged into 8 pages"]
    out = str(tmp_path / "sample_report_a_merged.pdf")
    assert app.outputs == [out] and os.path.exists(out)
    assert "Saved: " in log_text(app) and "2 files · 8 pages · 10 bookmarks" in log_text(app)
    assert app.lbl_status.cget("text") == "Done" and str(app.btn_open.cget("state")) == "normal"
    assert str(app.btn_run.cget("state")) == "normal" and app.progress["value"] == env["gui"].PROGRESS_STEPS
    assert (fx.sha1(a), fx.sha1(b)) == before
    close(app)


def test_merge_needs_two_files_and_a_valid_range(env, tmp_path):
    (a,) = copies(tmp_path, "sample_report_a.pdf")
    app = env["gui"].App()
    app.run()
    assert env["info"] == ["Add PDF files first"]
    app.add_paths([a])
    app.run()
    assert env["info"][-1] == "Add at least two files to merge"
    b = fx.text_pdf(str(tmp_path / "b.pdf"), pages=2)
    app.add_paths([b])
    app._select_file(1)
    app.var_pages.set("5")                                  # invalid: not stored, run must refuse
    app.specs[b] = "5"                                      # pretend it slipped in anyway
    app.run()
    assert env["error"] == ["Page range does not fit: 5 (b.pdf, pages 1-2)"]
    assert app.worker is None
    close(app)


def test_shrink_run_writes_smaller_files_and_shows_the_estimate(env, tmp_path):
    (s,) = copies(tmp_path, "sample_scan.pdf")
    t = fx.text_pdf(str(tmp_path / "t.pdf"), pages=1)
    app = env["gui"].App()
    app.nb.select(1)
    app.root.update()
    app.add_paths([s, t])
    assert pump(app, lambda: app._estimate_kwargs is not None, timeout=60)
    assert app.lbl_estimate.cget("text").startswith("2 files 2.0 MB → about ")
    app.var_quality.set("small")
    app._on_option()
    assert app.lbl_estimate.cget("text") == "Estimating size..."
    assert pump(app, lambda: app._estimate_kwargs is not None and not app._estimating, timeout=60)
    app.run()
    assert pump(app, lambda: bool(env["info"]), timeout=120)
    out = str(tmp_path / "sample_scan_small.pdf")
    assert os.path.exists(out) and os.path.getsize(out) < os.path.getsize(s) * 0.5
    assert env["info"][0].startswith("Done: 1 files (2.0 MB → ")
    assert "Already small, could not shrink it further: t.pdf" in log_text(app)
    assert "images re-encoded" in log_text(app)
    assert saved(env)["shrink"] == {"quality": "small", "dpi": 200} and saved(env)["mode"] == "shrink"
    close(app)


def test_cancel_during_a_shrink_leaves_no_file(env, tmp_path, monkeypatch):
    (s,) = copies(tmp_path, "sample_scan.pdf")
    gui = env["gui"]
    real = gui.pdfops._rewrite_image

    def slow(*a, **k):
        time.sleep(0.4)
        return real(*a, **k)

    monkeypatch.setattr(gui.pdfops, "_rewrite_image", slow)
    app = gui.App()
    app.nb.select(1)
    app.add_paths([s])
    app.run()
    pump(app, lambda: app.progress["value"] > 0, timeout=20)
    app.cancel()
    assert pump(app, lambda: app.lbl_status.cget("text") == "Cancelled", timeout=30)
    assert "Cancelled: sample_scan.pdf (no output written)" in log_text(app)
    assert sorted(os.listdir(tmp_path)) == ["sample_scan.pdf", "settings"]
    assert env["info"] == [] and str(app.btn_run.cget("state")) == "normal"
    close(app)


def test_closing_while_a_job_runs_waits_for_it(env, tmp_path, monkeypatch):
    (s,) = copies(tmp_path, "sample_scan.pdf")
    gui = env["gui"]
    real = gui.pdfops._save_atomic

    def slow_save(*a, **k):
        time.sleep(0.8)
        return real(*a, **k)

    monkeypatch.setattr(gui.pdfops, "_save_atomic", slow_save)
    app = gui.App()
    app.nb.select(1)
    app.add_paths([s])
    app.run()
    pump(app, lambda: app.progress["value"] > 0, timeout=20)
    app._on_close()                                         # cancel flag set; the save under way may finish
    worker = app.worker
    end = time.time() + 30
    while worker.is_alive() and time.time() < end:
        try:
            app.root.update()
        except Exception:
            break
        time.sleep(0.05)
    assert not worker.is_alive()
    assert not [n for n in os.listdir(tmp_path) if n.endswith(".part")]
    gc.collect()


def test_large_batch_asks_first(env, tmp_path, monkeypatch):
    a, b = copies(tmp_path, "sample_report_a.pdf", "sample_report_b.pdf")
    gui = env["gui"]
    monkeypatch.setattr(gui.pdfops, "LARGE_PAGE_COUNT", 5)
    app = gui.App()
    app.add_paths([a, b])
    env["ask"] = False
    app.run()
    assert app.worker is None
    env["ask"] = True
    app.run()
    assert pump(app, lambda: bool(env["info"]), timeout=60)
    close(app)


def test_nothing_changes_while_a_job_runs(env, tmp_path, monkeypatch):
    (s,) = copies(tmp_path, "sample_scan.pdf")
    t = fx.text_pdf(str(tmp_path / "t.pdf"), pages=1)
    gui = env["gui"]
    real = gui.pdfops._rewrite_image

    def slow(*a, **k):
        time.sleep(0.3)
        return real(*a, **k)

    monkeypatch.setattr(gui.pdfops, "_rewrite_image", slow)
    app = gui.App()
    app.nb.select(1)
    app.add_paths([s])
    app.run()
    worker = app.worker
    pump(app, lambda: app.progress["value"] > 0, timeout=20)
    app.run()                                               # a second run while busy: ignored
    assert app.worker is worker
    app.add_paths([t])                                      # adding, removing, clearing: ignored
    app.remove_selected()
    app.clear_files()
    assert app.files == [s]
    assert str(app.cmb_lang.cget("state")) == "disabled" and str(app.btn_add.cget("state")) == "disabled"
    assert pump(app, lambda: bool(env["info"]), timeout=120)
    assert app.files == [s] and str(app.cmb_lang.cget("state")) == "readonly"
    close(app)


def test_drop_event_with_braced_paths(env, tmp_path):
    a = fx.text_pdf(str(tmp_path / "with space.pdf"), pages=1)
    b = fx.text_pdf(str(tmp_path / "plain.pdf"), pages=1)
    app = env["gui"].App()

    class Ev:  # tkdnd hands over a Tcl list with forward slashes; a path with spaces is braced
        data = "{" + a.replace("\\", "/") + "} " + b.replace("\\", "/")

    app._on_drop(Ev())
    assert app.files == [a, b]
    close(app)


def test_fifty_files_list_and_summary(env, tmp_path):
    paths = [fx.text_pdf(str(tmp_path / f"f{i:02}.pdf"), pages=2) for i in range(50)]
    app = env["gui"].App()
    t0 = time.time()
    app.add_paths(paths)
    assert time.time() - t0 < 20
    assert app.lst_files.size() == 50 and app.lbl_merge_summary.cget("text") == "50 files → 100 pages"
    app._select_file(49)
    app._move_selected(1)                                   # last stays last
    assert app.files[-1] == paths[-1]
    close(app)


def test_open_result_without_outputs_is_a_no_op(env):
    app = env["gui"].App()
    app.open_result()                                       # nothing to open, nothing raised
    assert str(app.btn_open.cget("state")) == "disabled"
    close(app)


def test_estimate_clears_when_files_are_removed(env, tmp_path):
    (s,) = copies(tmp_path, "sample_scan.pdf")
    app = env["gui"].App()
    app.nb.select(1)
    app.root.update()
    app.add_paths([s])
    assert pump(app, lambda: app._estimate_kwargs is not None, timeout=60)
    app._select_file(0)
    app.remove_selected()
    assert app.lbl_estimate.cget("text") == "" and app.preview_file is None
    close(app)


def test_merge_job_error_is_logged_and_the_window_recovers(env, tmp_path, monkeypatch):
    a, b = copies(tmp_path, "sample_report_a.pdf", "sample_report_b.pdf")
    gui = env["gui"]
    monkeypatch.setattr(gui.pdfops, "merge_pdfs", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    app = gui.App()
    app.add_paths([a, b])
    app.run()
    assert pump(app, lambda: str(app.btn_run.cget("state")) == "normal" and app.lbl_status.cget("text") == "Done", timeout=30)
    assert "disk full" in log_text(app) and env["info"] == [] and app.outputs == []
    close(app)


# ------------------------------------------------------------------ threads and state
def test_no_worker_thread_holds_the_window(env, tmp_path, monkeypatch):
    """Every thread the App starts gets plain values: no App, widget or Tk variable."""
    import tkinter as tk

    (s,) = copies(tmp_path, "sample_scan.pdf")
    gui = env["gui"]
    started = []

    class Capture(threading.Thread):
        def __init__(self, *a, target=None, args=(), kwargs=None, **k):
            super().__init__(*a, target=target, args=args, kwargs=kwargs, **k)
            started.append((target, args, kwargs or {}))

    monkeypatch.setattr(gui.threading, "Thread", Capture)
    # The update check's thread starts 1.5 s after the window; on a slow machine it landed
    # in `started` here. It has its own test (test_the_thread_holds_no_tk_object).
    monkeypatch.setattr(gui.update_check, "check_async", lambda *a, **k: False)
    app = gui.App()
    app.nb.select(1)
    app.add_paths([s])                                      # preview + estimate threads
    app.root.update()
    pump(app, lambda: False, timeout=0.6)                   # the estimate's debounce fires
    app.run()                                               # job thread
    assert {t.__name__ for t, _, _ in started} == {"_preview_worker", "_estimate_worker", "_job_worker"}
    for target, args, kwargs in started:
        assert target.__closure__ is None
        for value in list(args) + list(kwargs.values()):
            assert not isinstance(value, (gui.App, tk.Misc, tk.Variable)), target.__name__
            assert value is not app and getattr(value, "root", None) is None
    pump(app, idle, timeout=120)
    close(app)


def test_settings_with_wrong_types_still_open_the_window(env):
    os.makedirs(os.path.dirname(env["settings"]), exist_ok=True)
    with open(env["settings"], "w", encoding="utf-8") as f:
        json.dump({"mode": 3, "merge": {"keep_bookmarks": "yes"}, "shrink": {"quality": 5, "dpi": "high"},
                   "update_check": "no", "lang": 7}, f)
    app = env["gui"].App()
    assert app.mode() == "merge" and app.var_keep_bookmarks.get() is True
    assert app.shrink_options() == env["gui"].pdfops.ShrinkOptions("normal", 200)
    assert app.var_update_check.get() is True
    close(app)
    with open(env["settings"], "w", encoding="utf-8") as f:
        f.write("{not json")
    app = env["gui"].App()
    assert app.mode() == "merge"
    close(app)


def test_mode_and_options_are_remembered(env):
    app = env["gui"].App()
    app.nb.select(1)
    app.root.update()
    app.var_dpi.set(150)
    app._on_option()
    close(app)
    assert saved(env)["mode"] == "shrink" and saved(env)["shrink"]["dpi"] == 150
    app = env["gui"].App()
    assert app.mode() == "shrink" and app.var_dpi.get() == 150
    close(app)


def test_language_switch_rewords_everything(env, tmp_path):
    (a,) = copies(tmp_path, "sample_report_a.pdf")
    app = env["gui"].App()
    app.add_paths([a])
    app.cmb_lang.current(i18n.LANGS.index("ko"))
    app._on_lang()
    assert app.root.title().startswith("PDF 합치기·용량 줄이기 v")
    assert app.nb.tab(0, "text") == "합치기" and app.nb.tab(1, "text") == "용량 줄이기"
    assert app.lst_files.get(0).startswith("1. sample_report_a.pdf  (6쪽")
    assert app.btn_run.cget("text") == "실행" and app.lbl_status.cget("text") == "준비"
    assert saved(env)["lang"] == "ko"
    close(app)
