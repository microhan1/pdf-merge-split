"""[Send feedback]: the address it builds, the link in the window, what happens when no
browser opens, and that none of it reaches the network. The browser is always faked:
webbrowser.open is replaced, and urlopen and sockets are recorded and must stay unused."""
from __future__ import annotations

import functools
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import i18n  # noqa: E402
import update_check as uc  # noqa: E402
from version import APP_VERSION  # noqa: E402

KO = "https://chaekgalpi.co.kr/tools/pdfmerge?utm_source=app&utm_medium=feedback&v=0.1.1#feedback"
EN = "https://chaekgalpi.co.kr/en/tools/pdfmerge?utm_source=app&utm_medium=feedback&v=0.1.1#feedback"
LABELS = {"ko": "의견 보내기", "en": "Send feedback", "ja": "ご意見を送る", "zh-CN": "发送反馈"}
NEW_KEYS = ("lnk_feedback", "msg_feedback_failed", "log_feedback_url")


def query(url: str) -> dict:
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query, keep_blank_values=True)


# ------------------------------------------------------------------ a. the address

@pytest.mark.parametrize("lang, expected", [("ko", KO), ("en", EN), ("ja", EN), ("zh-CN", EN)])
def test_exact_address_per_language(lang, expected):
    assert uc.feedback_url("pdfmerge", "0.1.1", lang) == expected


# 2026-10-27 series rule: only Korean gets the Korean page; any other code, listed or
# not, gets the English one, and no language at all means the default screen language
# (English). More cases in test_feedback_followup.py.
@pytest.mark.parametrize("lang", [None, "", "fr", "de-DE", "xx", "  ", 42, ["en"]])
def test_unknown_language_gets_the_english_page(lang):
    assert uc.feedback_url("pdfmerge", "0.1.1", lang) == EN


@pytest.mark.parametrize("lang, expected", [("EN", EN), ("En", EN), ("zh-cn", EN), ("ZH-CN", EN), ("zh_CN", EN),
                                            ("JA", EN), ("en-US", EN), (" ja ", EN), ("KO", KO), ("ko-KR", KO)])
def test_language_code_case_and_form(lang, expected):
    assert uc.feedback_url("pdfmerge", "0.1.1", lang) == expected


@pytest.mark.parametrize("lang", list(i18n.LANGS) + [None, "fr"])
@pytest.mark.parametrize("version", ["0.1.1", "", None, "1 & #2"])
def test_address_shape(lang, version):
    url = uc.feedback_url(uc.SLUG, version, lang)
    assert url.startswith("https://chaekgalpi.co.kr/") and url.startswith(uc.SITE)
    assert url.endswith("#feedback") and url.count("#") == 1
    q = query(url)
    assert q["utm_source"] == ["app"] and q["utm_medium"] == ["feedback"]
    parts = urllib.parse.urlsplit(url)
    assert parts.scheme == "https" and parts.netloc == "chaekgalpi.co.kr" and parts.fragment == "feedback"
    assert parts.path in ("/tools/pdfmerge", "/en/tools/pdfmerge")


def test_the_windows_slug_not_the_web_one():
    assert uc.SLUG == "pdfmerge"
    assert "/tools/pdfmerge?" in uc.feedback_url(uc.SLUG, APP_VERSION, "ko")
    assert "pdfmergeweb" not in uc.feedback_url(uc.SLUG, APP_VERSION, "en")


# ------------------------------------------------------------------ b. the version

@pytest.mark.parametrize("version, encoded", [("0.2.1", "0.2.1"), ("v0.2.1", "v0.2.1"),
                                              ("1.0.0-beta+3", "1.0.0-beta%2B3")])
def test_version_values(version, encoded):
    url = uc.feedback_url("pdfmerge", version, "ko")
    assert url == f"https://chaekgalpi.co.kr/tools/pdfmerge?utm_source=app&utm_medium=feedback&v={encoded}#feedback"
    assert query(url)["v"] == [version]


@pytest.mark.parametrize("version", ["", None, "   ", 0, 1.0])
def test_empty_version_leaves_v_out(version):
    url = uc.feedback_url("pdfmerge", version, "ko")
    assert url == "https://chaekgalpi.co.kr/tools/pdfmerge?utm_source=app&utm_medium=feedback#feedback"
    assert "v" not in query(url)


@pytest.mark.parametrize("version", ["1.0 beta", "1&utm_source=evil", "1#top", "1?x=y", "버전 0.1",
                                     "a/b\\c", "100%", "x=y&z", "\t1.0 é中", "+-+"])
def test_strange_versions_are_encoded_and_read_back(version):
    for lang in ("ko", "en"):
        url = uc.feedback_url("pdfmerge", version, lang)
        parts = urllib.parse.urlsplit(url)
        assert parts.fragment == "feedback" and parts.path.endswith("/tools/pdfmerge")
        q = query(url)
        assert sorted(q) == ["utm_medium", "utm_source", "v"]
        assert q["utm_source"] == ["app"] and q["utm_medium"] == ["feedback"]
        assert q["v"] == [version.strip()]
        raw_v = parts.query.split("&v=", 1)[1]
        assert not set(raw_v) & set(" &#?/+=\\\t") and all(ord(c) < 128 for c in url)


# ------------------------------------------------------------------ e. language files

@pytest.mark.parametrize("lang", i18n.LANGS)
def test_new_strings_in_every_language(lang):
    with open(os.path.join(ROOT, "lang", f"{lang}.json"), encoding="utf-8") as f:
        data = json.load(f)
    for key in NEW_KEYS:
        assert isinstance(data.get(key), str) and data[key].strip(), f"{lang}: {key}"
    assert data["lnk_feedback"] == LABELS[lang]
    assert "{url}" in data["msg_feedback_failed"] and "{url}" in data["log_feedback_url"]


# ------------------------------------------------------------------ the window

class SocketRecorder:
    """Every attempt to open a connection or look up a name, from anywhere in the process."""

    def __init__(self):
        self.calls: list[str] = []

    def install(self, monkeypatch):
        def record(name):
            def fake(*args, **kwargs):
                self.calls.append(f"{name}{args[1:2] if name == 'connect' else args[:1]}")
                raise OSError("network blocked in test_feedback")
            return fake

        monkeypatch.setattr(socket.socket, "connect", record("connect"))
        monkeypatch.setattr(socket.socket, "connect_ex", record("connect"))
        monkeypatch.setattr(socket, "create_connection", record("create_connection"))
        monkeypatch.setattr(socket, "getaddrinfo", record("getaddrinfo"))


@pytest.fixture
def env(monkeypatch, isolated_settings, no_update_server):
    i18n.init("ko")
    import gui

    sockets = SocketRecorder()
    sockets.install(monkeypatch)
    state = {"answer": None, "opened": [], "result": True, "warnings": [], "settings": isolated_settings,
             "gui": gui, "apps": []}

    def fake_open(url, *args, **kwargs):
        state["opened"].append(url)
        result = state["result"]
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(webbrowser, "open", fake_open)
    monkeypatch.setattr(webbrowser, "open_new", lambda *a, **k: pytest.fail("open_new used"))
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda *a, **k: pytest.fail("open_new_tab used"))
    monkeypatch.setattr(gui.messagebox, "showwarning", lambda *a, **k: state["warnings"].append((a, k)))
    monkeypatch.setattr(gui.update_check, "check_async",
                        functools.partial(uc.check_async, fetcher=lambda s, v: state["answer"]))
    yield state
    for app in state["apps"]:
        close(app)
    import gc

    gc.collect()
    # g. not one request or socket, on any path the link can take
    assert no_update_server.requests == []
    assert sockets.calls == []


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
    app = env["gui"].App()
    env["apps"].append(app)
    return app


def close(app):
    def idle():
        return not any(t.is_alive() for t in threading.enumerate() if t is not threading.main_thread())

    if app._closing:
        return
    pump(app, idle, timeout=10)
    app._on_close()
    try:
        app.root.update()
    except Exception:
        pass


def address(lang):
    return uc.feedback_url("pdfmerge", APP_VERSION, lang)


def log_text(app):
    return app.txt_log.get("1.0", "end")


def snapshot(app):
    return {"files": list(app.files), "mode": app.mode(), "status": app.lbl_status.cget("text"),
            "running": app._running, "update": bool(app.var_update_check.get()),
            "notice": bool(app.frm_update.winfo_manager()), "lang": i18n.current_lang(),
            "run_state": str(app.btn_run.cget("state")), "title": app.root.title()}


# ------------------------------------------------------------------ c. the click

def test_link_sits_in_the_header_next_to_the_update_box(env):
    app = start(env)
    app.root.update()
    assert app.lnk_feedback.master is app.chk_update.master
    assert app.lnk_feedback.grid_info()["row"] == app.chk_update.grid_info()["row"]
    assert app.lnk_feedback.grid_info()["column"] > app.chk_update.grid_info()["column"]
    assert app.lnk_feedback.winfo_ismapped()
    assert str(app.lnk_feedback.cget("cursor")) == "hand2"
    assert env["opened"] == []                          # nothing opens by itself


def test_one_click_opens_the_page_once(env):
    app = start(env)
    app._open_feedback()
    assert env["opened"] == [address("ko")]
    assert env["opened"][0] == f"https://chaekgalpi.co.kr/tools/pdfmerge?utm_source=app&utm_medium=feedback&v={APP_VERSION}#feedback"
    assert env["warnings"] == [] and "chaekgalpi" not in log_text(app)


@pytest.mark.parametrize("seq", ["<Button-1>", "<Return>", "<space>"])
def test_mouse_and_keyboard_reach_the_link(env, seq):
    app = start(env)
    app.root.update()
    app.lnk_feedback.focus_force()
    app.root.update()
    app.lnk_feedback.event_generate(seq, x=3, y=3) if seq == "<Button-1>" else app.lnk_feedback.event_generate(seq)
    app.root.update()
    assert env["opened"] == [address("ko")]


@pytest.mark.parametrize("lang", i18n.LANGS)
def test_each_language_opens_its_page(env, lang):
    i18n.init(lang)
    app = start(env)
    app._open_feedback()
    assert env["opened"] == [address(lang)]
    assert ("/en/tools/" in env["opened"][0]) == (lang != "ko")


def test_changing_language_while_running_changes_the_next_address(env):
    app = start(env)
    app._open_feedback()
    for lang in ("en", "ja", "zh-CN", "ko"):
        app.cmb_lang.current(i18n.LANGS.index(lang))
        app._on_lang()
        app._open_feedback()
    assert env["opened"] == [address(x) for x in ("ko", "en", "ja", "zh-CN", "ko")]


def test_address_comes_from_constants_not_settings(env):
    app = start(env, {"lang": "ko", "feedback_url": "https://evil.example/", "url": "https://evil.example/",
                      "slug": "other", "site": "https://evil.example/"})
    app._open_feedback()
    assert env["opened"] == [address("ko")]


def test_link_works_while_a_job_runs_and_changes_nothing(env):
    app = start(env)
    app._set_controls(running=True)
    before = snapshot(app)
    app._open_feedback()
    assert env["opened"] == [address("ko")] and snapshot(app) == before
    app._set_controls(running=False)


# ------------------------------------------------------------------ d. no browser

@pytest.mark.parametrize("result", [False, None, webbrowser.Error("no runnable browser"),
                                    OSError(2, "The system cannot find the file specified"),
                                    RuntimeError("odd handler")],
                         ids=["false", "none", "webbrowser.Error", "OSError", "RuntimeError"])
def test_when_no_browser_opens_the_address_is_shown(env, result):
    env["result"] = result
    app = start(env)
    before = snapshot(app)
    app._open_feedback()                                 # must not raise
    url = address("ko")
    assert env["opened"] == [url]
    assert len(env["warnings"]) == 1
    (title, message), kwargs = env["warnings"][0]
    assert title == "의견 보내기" and url in message and kwargs.get("parent") is app.root
    assert url in log_text(app)                          # selectable in the log as well
    assert snapshot(app) == before
    assert app.root.winfo_exists()
    env["result"] = True                                 # the next click works normally again
    app._open_feedback()
    assert len(env["opened"]) == 2 and len(env["warnings"]) == 1


@pytest.mark.parametrize("lang", i18n.LANGS)
def test_failure_message_in_every_language(env, lang):
    i18n.init(lang)
    env["result"] = False
    app = start(env)
    app._open_feedback()
    (title, message), _ = env["warnings"][0]
    assert title == LABELS[lang]
    assert message == i18n.t("msg_feedback_failed", url=address(lang)) and "{" not in message


def test_success_writes_nothing_to_settings(env):
    app = start(env, {"lang": "ko"})
    app._open_feedback()
    with open(env["settings"], encoding="utf-8") as f:
        saved = json.load(f)
    assert not any("feedback" in k for k in saved)


# ------------------------------------------------------------------ e. the label follows the language

def test_label_follows_the_language(env):
    app = start(env)
    assert app.lnk_feedback.cget("text") == LABELS["ko"]
    for lang in ("en", "ja", "zh-CN", "ko"):
        app.cmb_lang.current(i18n.LANGS.index(lang))
        app._on_lang()
        assert app.lnk_feedback.cget("text") == LABELS[lang]


# ------------------------------------------------------------------ f. the update check is untouched

def test_update_notice_and_feedback_do_not_mix(env):
    env["answer"] = {"latest": "9.9.9", "updateAvailable": True,
                     "url": "https://chaekgalpi.co.kr/tools/pdfmerge?utm_source=app&utm_medium=update"}
    app = start(env)
    assert pump(app, lambda: bool(app.frm_update.winfo_manager()))
    app._open_feedback()                                 # the notice stays where it is
    assert app.frm_update.winfo_manager() and app._update_info is not None
    app._update_open()
    assert env["opened"] == [address("ko"), env["answer"]["url"]]
    assert not app.frm_update.winfo_manager()


def test_update_toggle_still_saves(env):
    app = start(env)
    app.var_update_check.set(False)
    app._on_update_toggle()
    app._open_feedback()
    with open(env["settings"], encoding="utf-8") as f:
        assert json.load(f)["update_check"] is False
    assert env["opened"] == [address("ko")]
