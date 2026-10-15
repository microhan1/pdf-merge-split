"""Update check: once a day, off switch, silent failures, what is sent, when a notice shows.
The network is always faked - these tests never reach the real server."""
from __future__ import annotations

import io
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.parse

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import update_check as uc  # noqa: E402

DAY = "2026-10-15"
OK = {"slug": "pdfmerge", "latest": "0.2.0", "tag": "v0.2.0", "client": "0.1.0", "updateAvailable": True,
      "url": "https://chaekgalpi.co.kr/tools/pdfmerge?utm_source=app&utm_medium=update"}


class FakeRoot:
    """after() runs the callback at once; the background thread is run inline (see fixture)."""

    def __init__(self):
        self.delays = []

    def after(self, ms, fn):
        self.delays.append(ms)
        fn()


@pytest.fixture(autouse=True)
def inline_threads(monkeypatch):
    class InlineThread:
        def __init__(self, target, name=None, daemon=None):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(uc.threading, "Thread", InlineThread)


def run(prefs, fetcher, day=DAY):
    results = []

    def on_result(info, answered):
        results.append(info)
        if answered:
            prefs.update_check_last = day

    root = FakeRoot()
    scheduled = uc.check_async(root, uc.SLUG, "0.1.0", prefs, on_result, fetcher=fetcher, day=day)
    return scheduled, results, root


def counting(answer):
    calls = []

    def fetcher(slug, version):
        calls.append((slug, version))
        return answer

    return fetcher, calls


def test_slug_is_this_tool():
    assert uc.SLUG == "pdfmerge"


def test_same_day_twice_asks_once():
    p = uc.UpdatePrefs()
    fetcher, calls = counting(OK)
    run(p, fetcher)
    run(p, fetcher)
    assert len(calls) == 1
    run(p, fetcher, day="2026-10-16")
    assert len(calls) == 2


def test_switched_off_never_asks():
    p = uc.UpdatePrefs(update_check=False)
    fetcher, calls = counting(OK)
    scheduled, results, root = run(p, fetcher)
    assert not scheduled and calls == [] and results == []


def test_offline_does_not_use_up_the_day():
    p = uc.UpdatePrefs()
    fetcher, calls = counting(None)
    run(p, fetcher)
    run(p, fetcher)
    assert len(calls) == 2 and p.update_check_last == ""


def test_waits_one_and_a_half_seconds_first():
    _, _, root = run(uc.UpdatePrefs(), counting(OK)[0])
    assert root.delays[0] == 1500


def test_asks_with_this_tools_slug_and_version():
    fetcher, calls = counting(OK)
    run(uc.UpdatePrefs(), fetcher)
    assert calls == [("pdfmerge", "0.1.0")]


class FakeResp(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_request_carries_only_slug_version_and_os():
    seen = {}

    def opener(req, timeout):
        seen["url"], seen["timeout"] = req.full_url, timeout
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        return FakeResp(json.dumps(OK).encode())

    assert uc.fetch("pdfmerge", "0.1.0", opener=opener) == OK
    url = urllib.parse.urlsplit(seen["url"])
    assert (url.scheme, url.netloc, url.path) == ("https", "chaekgalpi.co.kr", "/api/tools/pdfmerge/latest")
    assert urllib.parse.parse_qs(url.query) == {"v": ["0.1.0"], "os": ["win"]}
    assert seen["timeout"] == 4.0
    assert seen["headers"]["user-agent"] == "chaekgalpi-tool/pdfmerge/0.1.0"
    assert set(seen["headers"]) <= {"user-agent", "accept"}


@pytest.mark.parametrize("behaviour", ["urlerror", "timeout", "http500", "badjson", "list", "status500", "oserror"])
def test_failures_are_silent(behaviour):
    def opener(req, timeout):
        if behaviour == "urlerror":
            raise urllib.error.URLError("offline")
        if behaviour == "timeout":
            raise socket.timeout("slow")
        if behaviour == "http500":
            raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)
        if behaviour == "oserror":
            raise OSError("reset")
        if behaviour == "badjson":
            return FakeResp(b"<html>not json")
        if behaviour == "list":
            return FakeResp(b"[1, 2]")
        return FakeResp(json.dumps(OK).encode(), status=500)

    assert uc.fetch("pdfmerge", "0.1.0", opener=opener) is None


@pytest.mark.parametrize("flag", [False, None, "true", 1])
def test_no_notice_unless_server_says_true(flag):
    assert uc.interpret(dict(OK, updateAvailable=flag), "0.1.0") is None


def test_notice_once_when_newer():
    p = uc.UpdatePrefs()
    fetcher, _ = counting(OK)
    _, results, _ = run(p, fetcher)
    assert len(results) == 1 and results[0] == uc.UpdateInfo("0.2.0", "0.1.0", OK["url"])


def test_later_hides_that_version_but_not_a_newer_one():
    p = uc.UpdatePrefs(update_check_skipped="0.2.0")
    assert uc.interpret(OK, "0.1.0", p.update_check_skipped) is None
    assert uc.interpret(dict(OK, latest="0.2.1"), "0.1.0", p.update_check_skipped).latest == "0.2.1"


@pytest.mark.parametrize("url", ["https://github.com/microhan1/pdf-merge-split/releases", "http://chaekgalpi.co.kr/tools/x",
                                 "https://chaekgalpi.co.kr.evil.example/", "javascript:alert(1)", None, 5])
def test_only_pages_of_the_tools_site_are_opened(url):
    assert uc.interpret(dict(OK, url=url), "0.1.0") is None


@pytest.mark.parametrize("latest", ["", "   ", None, 3, "x" * 40])
def test_odd_versions_show_nothing(latest):
    assert uc.interpret(dict(OK, latest=latest), "0.1.0") is None


def test_the_thread_holds_no_tk_object(monkeypatch):
    captured = {}

    class CaptureThread:
        def __init__(self, target, name=None, daemon=None):
            captured["target"] = target

        def start(self):
            pass

    monkeypatch.setattr(uc.threading, "Thread", CaptureThread)

    class Root:
        def after(self, ms, fn):
            if ms == uc.DELAY_MS:
                fn()

    root, on_result = Root(), lambda info, answered: None
    uc.check_async(root, uc.SLUG, "0.1.0", uc.UpdatePrefs(), on_result, fetcher=lambda s, v: OK, day=DAY)
    target = captured["target"]
    held = [c.cell_contents for c in (target.__closure__ or ())] + list(target.__defaults__ or ())
    assert not any(x is root or x is on_result for x in held)


URL_EN = "https://chaekgalpi.co.kr/en/tools/pdfmerge?utm_source=app&utm_medium=update"


@pytest.mark.parametrize("lang, url_en, expect", [
    ("ko", URL_EN, "url"), ("en", URL_EN, "url_en"), ("ja", URL_EN, "url_en"), ("zh-CN", URL_EN, "url_en"),
    ("en", None, "url"), ("en", "missing", "url"),
    ("en", "https://evil.example/en/tools/pdfmerge", "url"),
    ("en", "http://chaekgalpi.co.kr/en/tools/pdfmerge", "url"), ("en", 5, "url"),
])
def test_page_follows_the_screen_language(lang, url_en, expect):
    data = dict(OK) if url_en == "missing" else dict(OK, urlEn=url_en)
    info = uc.interpret(data, "0.1.0")
    assert uc.page_for(info, lang) == (OK["url"] if expect == "url" else URL_EN)


def test_settings_are_type_checked():
    p = uc.prefs_from_settings({"update_check": "no", "update_check_last": 20261015, "update_check_skipped": ["0.2.0"]})
    assert p == uc.UpdatePrefs(True, "", "")
    assert uc.prefs_from_settings([1, 2]) == uc.UpdatePrefs()
    assert uc.prefs_from_settings({"update_check": False, "update_check_last": DAY}) == uc.UpdatePrefs(False, DAY, "")


def test_version_is_one_value():
    import version

    assert re.fullmatch(r"\d+\.\d+\.\d+", version.APP_VERSION)


def test_response_body_never_leaks_into_an_error(capsys):
    """Whatever the server sends back (it might echo headers) stays inside fetch()."""
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 403, "SECRET-TOKEN-IN-BODY", {}, io.BytesIO(b"SECRET-TOKEN-IN-BODY"))

    assert uc.fetch("pdfmerge", "0.1.0", opener=opener) is None
    out = capsys.readouterr()
    assert "SECRET-TOKEN" not in out.out + out.err
