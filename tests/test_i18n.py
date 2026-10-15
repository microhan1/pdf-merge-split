"""The four language files agree with each other and with the code."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import i18n  # noqa: E402


def test_check_i18n_passes():
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "check_i18n.py"), ROOT],
                       capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    out = p.stdout.decode("utf-8", "replace")
    assert p.returncode == 0, out


@pytest.mark.parametrize("lang", i18n.LANGS)
def test_every_language_loads_and_formats(lang):
    i18n.init(lang)
    assert i18n.current_lang() == lang
    assert "{" not in i18n.t("update_available", latest="0.2.0", current="0.1.0")
    assert i18n.t("no_such_key") == "no_such_key"


def test_init_prefers_explicit_then_settings_then_os(isolated_settings):
    assert i18n.init("ja") == "ja"
    i18n.save_settings({"lang": "zh-CN"})
    assert i18n.init() == "zh-CN"
    i18n.save_settings({"lang": "xx"})
    assert i18n.init() in i18n.LANGS
    assert i18n.init("nonsense") in i18n.LANGS


def test_set_lang_persists_only_when_asked(isolated_settings):
    i18n.set_lang("ko", persist=False)
    assert not os.path.exists(isolated_settings)
    i18n.set_lang("ko")
    assert i18n.load_settings()["lang"] == "ko"
