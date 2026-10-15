"""The update notice in the real window: a Tk App with a faked server answer and a
settings.json in a temporary folder. Never reaches the real server or the user's settings."""
from __future__ import annotations

import functools
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import i18n  # noqa: E402
import update_check as uc  # noqa: E402
from version import APP_VERSION  # noqa: E402

OK = {"latest": "0.2.0", "updateAvailable": True,
      "url": "https://chaekgalpi.co.kr/tools/pdfmerge?utm_source=app&utm_medium=update"}


@pytest.fixture
def env(monkeypatch, isolated_settings):
    i18n.init("en")
    import gui

    state = {"answer": OK, "calls": 0, "opened": [], "settings": isolated_settings, "gui": gui}

    def fetcher(slug, version):
        state["calls"] += 1
        return state["answer"]

    monkeypatch.setattr(gui.update_check, "check_async", functools.partial(uc.check_async, fetcher=fetcher))
    monkeypatch.setattr(gui.webbrowser, "open", lambda url: state["opened"].append(url))
    yield state
    import gc

    gc.collect()


def pump(app, cond, timeout=8.0):
    end = time.time() + timeout
    box = {"ok": False}

    def poll():
        if cond():
            box["ok"] = True
            app.root.quit()
        elif time.time() > end:
            app.root.quit()
        else:
            app.root.after(20, poll)

    app.root.after(20, poll)
    app.root.mainloop()
    return box["ok"] or cond()


def start(env, settings=None):
    if settings is not None:
        os.makedirs(os.path.dirname(env["settings"]), exist_ok=True)
        with open(env["settings"], "w", encoding="utf-8") as f:
            json.dump(settings, f)
    return env["gui"].App()


def close(app):
    import threading

    def idle():
        return not any(t.is_alive() for t in threading.enumerate() if t is not threading.main_thread())

    pump(app, idle, timeout=10)
    app._on_close()
    try:
        app.root.update()
    except Exception:
        pass


def saved(env):
    p = env["settings"]
    return json.loads(open(p, encoding="utf-8").read()) if os.path.exists(p) else {}


def shown(app):
    return bool(app.frm_update.winfo_manager())


def test_newer_version_shows_one_line_and_records_the_day(env):
    app = start(env, {"lang": "en", "shrink": {"quality": "small", "dpi": 150}})
    assert pump(app, lambda: shown(app))
    assert app.lbl_update.cget("text") == f"Version 0.2.0 is available (current {APP_VERSION})"
    s = saved(env)
    assert s["update_check_last"] == uc.today() and env["calls"] == 1
    assert s["lang"] == "en" and s["shrink"] == {"quality": "small", "dpi": 150}   # other settings untouched
    close(app)


def test_later_hides_and_remembers_that_version(env):
    app = start(env)
    assert pump(app, lambda: shown(app))
    app._update_later()
    app.root.update()
    assert not shown(app) and saved(env)["update_check_skipped"] == "0.2.0" and env["opened"] == []
    close(app)


def test_update_opens_the_tools_page_only(env):
    app = start(env)
    assert pump(app, lambda: shown(app))
    app._update_open()
    app.root.update()
    assert env["opened"] == [OK["url"]] and not shown(app)
    close(app)


@pytest.mark.parametrize("lang, expect_en", [("ko", False), ("en", True), ("ja", True), ("zh-CN", True)])
def test_update_opens_the_page_in_the_screen_language(env, lang, expect_en):
    url_en = "https://chaekgalpi.co.kr/en/tools/pdfmerge?utm_source=app&utm_medium=update"
    env["answer"] = dict(OK, urlEn=url_en)
    i18n.init(lang)
    app = start(env)
    assert pump(app, lambda: shown(app))
    app._update_open()
    app.root.update()
    assert env["opened"] == [url_en if expect_en else OK["url"]]
    close(app)


def test_offline_shows_nothing_and_keeps_the_day_open(env):
    env["answer"] = None
    app = start(env)
    pump(app, lambda: env["calls"] == 1)
    pump(app, lambda: False, timeout=0.3)
    assert not shown(app) and saved(env).get("update_check_last", "") == ""
    close(app)


def test_switched_off_never_asks(env):
    app = start(env, {"update_check": False})
    pump(app, lambda: False, timeout=2.2)
    assert env["calls"] == 0 and not shown(app) and not app.var_update_check.get()
    close(app)


def test_already_checked_today_does_not_ask_again(env):
    app = start(env, {"update_check_last": uc.today()})
    pump(app, lambda: False, timeout=2.2)
    assert env["calls"] == 0
    close(app)


def test_unticking_the_box_hides_the_notice_and_saves(env):
    app = start(env)
    assert pump(app, lambda: shown(app))
    app.var_update_check.set(False)
    app._on_update_toggle()
    app.root.update()
    assert not shown(app) and saved(env)["update_check"] is False
    close(app)


def test_language_switch_rewords_the_notice(env):
    app = start(env)
    assert pump(app, lambda: shown(app))
    app.cmb_lang.current(i18n.LANGS.index("ko"))
    app._on_lang()
    assert app.lbl_update.cget("text") == f"새 버전이 나왔습니다: 0.2.0 (현재 {APP_VERSION})"
    assert app.btn_update_open.cget("text") == "업데이트" and app.btn_update_later.cget("text") == "다음에"
    close(app)


def test_notice_leaves_the_rest_of_the_window_in_place(env):
    """The notice row takes its height from the stretching middle row only; header,
    files, options, log and the bottom buttons keep their size and stay inside."""
    app = start(env)
    app.root.geometry("1060x760")
    pump(app, lambda: False, timeout=0.5)

    def layout():
        app.root.update_idletasks()
        ws = {"title_row": app.cmb_lang, "files": app.lst_files, "options": app.nb, "log": app.txt_log,
              "run": app.btn_run, "status": app.lbl_status}
        # the options column sits in the stretching middle row, so only its width counts
        return {k: (w.winfo_width(), w.winfo_height() if k != "options" else 0, w.winfo_rootx()) for k, w in ws.items()}

    before = layout()
    assert pump(app, lambda: shown(app))
    pump(app, lambda: False, timeout=0.3)
    after = layout()
    assert before == after
    r = app.root
    for w in (app.btn_run, app.btn_update_open, app.btn_update_later):
        assert w.winfo_rooty() + w.winfo_height() <= r.winfo_rooty() + r.winfo_height()
    close(app)


def test_window_title_carries_the_version(env):
    app = start(env)
    assert app.root.title() == f"PDF Merge & Compress v{APP_VERSION}"
    close(app)


CLOSE_WHILE_ASKING = r"""
import functools, os, sys, tempfile, time
sys.path.insert(0, {root!r})
os.environ["PDF_MERGE_SPLIT_SETTINGS"] = os.path.join(tempfile.mkdtemp(), "settings.json")
import i18n
i18n.SETTINGS_PATH = os.environ["PDF_MERGE_SPLIT_SETTINGS"]
i18n.init("en")
import gui, update_check as uc
import urllib.error, urllib.request
def _blocked(*a, **k):    # this child process must not reach the server either
    raise urllib.error.URLError("network blocked in tests")
urllib.request.urlopen = _blocked
uc.fetch.__kwdefaults__["opener"] = _blocked
def slow(slug, version):
    time.sleep(1.5)          # the request is still out when the window closes
    return {{"latest": "0.2.0", "updateAvailable": True, "url": "https://chaekgalpi.co.kr/tools/pdfmerge"}}
gui.update_check.check_async = functools.partial(uc.check_async, fetcher=slow)
for _ in range(3):
    app = gui.App()
    app.root.after(1700, app._on_close)      # 0.2 s into the request
    app.root.mainloop()
    del app
    time.sleep(2.0)          # the thread finishes with no window left
print("survived")
"""


def test_closing_while_the_request_is_out_does_not_abort_tcl(tmp_path):
    """Before the box-and-poll structure this died with "Tcl_AsyncDelete: async handler
    deleted by the wrong thread"."""
    import subprocess

    script = tmp_path / "close_while_asking.py"
    script.write_text(CLOSE_WHILE_ASKING.format(root=ROOT), encoding="utf-8")
    p = subprocess.run([sys.executable, "-W", "ignore", str(script)], capture_output=True, timeout=120)
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    assert p.returncode == 0 and "survived" in out and "Tcl_AsyncDelete" not in out, out[-800:]


def test_window_is_released_after_closing_during_a_request(env):
    """The decisive check: with the request still out, close the window, collect on the
    main thread, and the App must be gone (a thread holding it would keep it alive)."""
    import gc
    import weakref

    state = {"go": False}

    def slow(slug, version):
        while not state["go"]:
            time.sleep(0.05)
        return OK

    gui = env["gui"]
    gui.update_check.check_async = functools.partial(uc.check_async, fetcher=slow)
    app = start(env)
    ref = weakref.ref(app)
    pump(app, lambda: False, timeout=1.8)                  # the request is out now
    app._on_close()
    try:
        app.root.update()
    except Exception:
        pass
    del app
    gc.collect()
    assert ref() is None
    state["go"] = True
    time.sleep(0.3)
