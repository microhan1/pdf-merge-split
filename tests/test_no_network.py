"""The guard in conftest.py really stands between the tests and the Chaekgalpi server."""
from __future__ import annotations

import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import i18n  # noqa: E402
import update_check as uc  # noqa: E402


def test_default_network_functions_are_the_guard(no_update_server):
    assert uc.fetch.__kwdefaults__["opener"] is no_update_server
    assert urllib.request.urlopen is no_update_server
    assert uc.check_async.__kwdefaults__["fetcher"](uc.SLUG, "0.0.0") is None


def test_a_plain_fetch_is_recorded_and_answers_nothing(no_update_server):
    assert uc.fetch(uc.SLUG, "0.0.0") is None          # the default opener, as the App uses it
    assert len(no_update_server.requests) == 1
    assert no_update_server.requests[0].startswith("https://chaekgalpi.co.kr/api/tools/pdfmerge/latest?")
    no_update_server.requests.clear()                   # recorded on purpose here; do not fail this test


def test_settings_go_to_a_temporary_file(isolated_settings):
    assert i18n.SETTINGS_PATH == isolated_settings and os.environ[i18n.SETTINGS_ENV] == isolated_settings
    assert not i18n.SETTINGS_PATH.startswith(ROOT)
    i18n.save_settings({"lang": "ko"})
    assert i18n.load_settings() == {"lang": "ko"}
    assert not os.path.exists(os.path.join(ROOT, "settings.json"))
