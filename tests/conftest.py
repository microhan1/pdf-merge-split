"""Two things no test may do: reach the Chaekgalpi server, or touch a real settings.json.

Every App starts the update check 1.5 s after it opens, and settings are isolated per
test, so the once-a-day guard is always open. fetch() and check_async() bind their
network functions as keyword defaults, so patching update_check.fetch would not stop
them: the defaults themselves and urllib.request.urlopen are replaced here. Tests that
need an answer pass their own fetcher=, which still works. Any request that reaches
the guard is recorded and fails the test.

settings.json is redirected to a temporary file for every test, so neither the
repository nor a user's settings are written.
"""
import os
import sys
import urllib.error
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import i18n  # noqa: E402
import update_check  # noqa: E402

BLOCKED_HOST = "chaekgalpi.co.kr"


class NetworkGuard:
    def __init__(self):
        self.requests: list[str] = []

    def __call__(self, req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.requests.append(url)
        raise urllib.error.URLError(f"network blocked in tests: {url}")


@pytest.fixture(autouse=True)
def no_update_server(monkeypatch):
    guard = NetworkGuard()
    monkeypatch.setattr(urllib.request, "urlopen", guard)
    monkeypatch.setitem(update_check.fetch.__kwdefaults__, "opener", guard)
    monkeypatch.setitem(update_check.check_async.__kwdefaults__, "fetcher", lambda slug, version: None)
    yield guard
    if guard.requests:
        pytest.fail(f"{len(guard.requests)} request(s) tried to leave the test: {guard.requests[:3]}")


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    path = str(tmp_path / "settings" / "settings.json")
    monkeypatch.setattr(i18n, "SETTINGS_PATH", path)
    monkeypatch.setenv(i18n.SETTINGS_ENV, path)       # subprocesses (CLI tests) use the same file
    yield path
