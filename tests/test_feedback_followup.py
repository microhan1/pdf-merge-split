"""[Send feedback], series follow-up (2026-10-27): keyboard focus that can be seen, the
tooltip, one language rule for every page of the tools site, and the README links.
The browser is always faked and the network recorded: the `env` fixture of
test_feedback fails a test that opens a socket or reaches the update server."""
from __future__ import annotations

import gc
import os
import re
import sys
import urllib.parse
import weakref

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import i18n  # noqa: E402
import update_check as uc  # noqa: E402
from test_feedback import EN, KO, address, close, env, pump, start  # noqa: E402,F401
from version import APP_VERSION  # noqa: E402

TIPS = {"ko": "안 되는 것·불편한 것·바라는 기능을 알려 주세요(브라우저가 열립니다)",
        "en": "Tell us what's broken or missing (opens your browser)",
        "ja": "不具合・ご要望をお知らせください（ブラウザが開きます）",
        "zh-CN": "告诉我们问题或建议（将打开浏览器）"}
FEEDBACK_EN = "https://chaekgalpi.co.kr/en/tools/pdfmerge#feedback"
FEEDBACK_KO = "https://chaekgalpi.co.kr/tools/pdfmerge#feedback"


def switch(app, lang):
    app.cmb_lang.current(i18n.LANGS.index(lang))
    app._on_lang()


def focus_link_by_tab(app):
    """Put the focus on the update box and press Tab, as a keyboard user would. Windows
    may hand a new window the keyboard a moment late, so wait for it first."""
    app.root.update()
    app.chk_update.focus_force()
    pump(app, lambda: app.root.focus_get() is app.chk_update, timeout=3)
    app.chk_update.event_generate("<Tab>")
    app.root.update()


def focused(app):
    """The widget Tk gave the focus to (focus_get() is None while the window is not the
    foreground one, which says nothing about where Tab moved the focus)."""
    return app.root.focus_lastfor()


def hover(app):
    app.root.update()
    app.lnk_feedback.event_generate("<Enter>", x=3, y=3)
    assert pump(app, lambda: app.tip_feedback.tip is not None and app.tip_feedback.tip.winfo_viewable())
    app.root.update()
    return app.tip_feedback


def tip_rect(tip):
    t = tip.tip
    t.update()
    return t.winfo_rootx(), t.winfo_rooty(), t.winfo_rootx() + t.winfo_width(), t.winfo_rooty() + t.winfo_height()


def window_rect(app):
    r = app.root
    r.update()
    return r.winfo_rootx(), r.winfo_rooty(), r.winfo_rootx() + r.winfo_width(), r.winfo_rooty() + r.winfo_height()


# ------------------------------------------------------------------ a. keyboard

def test_tab_order_reaches_the_link(env):
    app = start(env)
    app.root.update()
    assert str(app.lnk_feedback.cget("takefocus")) in ("1", "True", "true")
    assert app.chk_update.tk_focusNext() is app.lnk_feedback
    assert app.lnk_feedback.tk_focusPrev() is app.chk_update


def test_tab_puts_the_focus_on_the_link(env):
    app = start(env)
    focus_link_by_tab(app)
    assert focused(app) is app.lnk_feedback
    assert env["opened"] == []                           # focusing opens nothing


def test_shift_tab_comes_back_to_the_link(env):
    app = start(env)
    app.root.update()
    after = app.lnk_feedback.tk_focusNext()
    after.focus_force()
    pump(app, lambda: app.root.focus_get() is after, timeout=3)
    after.event_generate("<Shift-Tab>")
    app.root.update()
    assert focused(app) is app.lnk_feedback


@pytest.mark.parametrize("seq", ["<Return>", "<space>"])
def test_enter_and_space_open_the_page_exactly_once(env, seq):
    app = start(env)
    focus_link_by_tab(app)
    app.lnk_feedback.event_generate(seq)
    app.root.update()
    assert env["opened"] == [address("ko")]
    app.lnk_feedback.event_generate(seq)                 # and once more per press
    app.root.update()
    assert env["opened"] == [address("ko")] * 2


@pytest.mark.parametrize("lang", i18n.LANGS)
def test_keyboard_opens_the_page_of_the_screen_language(env, lang):
    i18n.init(lang)
    app = start(env)
    focus_link_by_tab(app)
    app.lnk_feedback.event_generate("<Return>")
    app.root.update()
    assert env["opened"] == [address(lang)]


def test_focus_can_be_seen_and_goes_away(env):
    import gui

    app = start(env)
    app.root.update()
    before = str(app.lnk_feedback.cget("background"))
    focus_link_by_tab(app)
    assert str(app.lnk_feedback.cget("background")) == gui.LINK_FOCUS_BG != before
    app.lnk_feedback.event_generate("<Tab>")             # focus moves on
    app.root.update()
    assert focused(app) is not app.lnk_feedback
    assert str(app.lnk_feedback.cget("background")) == before


def test_other_keys_do_nothing(env):
    app = start(env)
    focus_link_by_tab(app)
    for key in ("<KeyPress-a>", "<Escape>", "<Up>", "<Down>"):
        app.lnk_feedback.event_generate(key)
    app.root.update()
    assert env["opened"] == []


def test_focus_highlight_survives_a_language_change(env):
    import gui

    app = start(env)
    focus_link_by_tab(app)
    switch(app, "en")
    app.root.update()
    assert app.lnk_feedback.cget("text") == "Send feedback"
    assert str(app.lnk_feedback.cget("background")) == gui.LINK_FOCUS_BG


# ------------------------------------------------------------------ b. tooltip

def test_strings_are_the_agreed_ones():
    import json

    for lang in i18n.LANGS:
        with open(os.path.join(ROOT, "lang", f"{lang}.json"), encoding="utf-8") as f:
            assert json.load(f)["tip_feedback"] == TIPS[lang]
    assert len(set(TIPS.values())) == 4 and all(v.strip() for v in TIPS.values())


def test_hover_shows_and_leaving_hides(env):
    app = start(env)
    assert app.tip_feedback.tip is None                  # nothing until the mouse comes
    tip = hover(app)
    assert tip.label.cget("text") == TIPS["ko"]
    toplevel = tip.tip
    app.lnk_feedback.event_generate("<Leave>")
    app.root.update()
    assert tip.tip is None and not toplevel.winfo_exists()
    assert env["opened"] == []


def test_leaving_before_the_delay_shows_nothing(env):
    app = start(env)
    app.root.update()
    app.lnk_feedback.event_generate("<Enter>", x=3, y=3)
    app.root.update()
    app.lnk_feedback.event_generate("<Leave>")
    pump(app, lambda: False, timeout=app.tip_feedback.DELAY_MS / 1000 + 0.4)
    assert app.tip_feedback.tip is None and app.tip_feedback._job is None


def test_hovering_again_shows_it_again_without_piling_up(env):
    app = start(env)
    for _ in range(3):
        hover(app)
        app.lnk_feedback.event_generate("<Leave>")
        app.root.update()
    hover(app)
    app.lnk_feedback.event_generate("<Enter>", x=4, y=4)     # a second Enter while it is open
    pump(app, lambda: False, timeout=app.tip_feedback.DELAY_MS / 1000 + 0.3)
    tops = [w for w in app.lnk_feedback.winfo_children() if w.winfo_class() == "Toplevel"]
    assert len(tops) == 1 and tops[0] is app.tip_feedback.tip


def test_click_hides_the_tooltip_and_opens_once(env):
    app = start(env)
    tip = hover(app)
    app.lnk_feedback.event_generate("<Button-1>", x=3, y=3)
    app.root.update()
    assert tip.tip is None
    assert env["opened"] == [address("ko")]


@pytest.mark.parametrize("lang", i18n.LANGS)
def test_tooltip_text_in_every_language(env, lang):
    i18n.init(lang)
    app = start(env)
    tip = hover(app)
    assert tip.label.cget("text") == TIPS[lang] == i18n.t("tip_feedback")


def test_language_change_changes_an_open_tooltip(env):
    app = start(env)
    tip = hover(app)
    seen = [tip.label.cget("text")]
    for lang in ("en", "ja", "zh-CN", "ko"):
        switch(app, lang)
        app.root.update()
        assert tip.tip is not None
        seen.append(tip.label.cget("text"))
    assert seen == [TIPS[x] for x in ("ko", "en", "ja", "zh-CN", "ko")]


def test_language_change_reaches_the_next_tooltip(env):
    app = start(env)
    switch(app, "ja")
    tip = hover(app)
    assert tip.label.cget("text") == TIPS["ja"]


def test_tooltip_does_not_take_the_focus(env):
    app = start(env)
    focus_link_by_tab(app)
    hover(app)
    assert focused(app) is app.lnk_feedback
    assert app.tip_feedback.tip.wm_overrideredirect()


def minimum_window(app, x=None, y=None):
    import gui

    app.root.update()
    w, h = gui.MIN_SIZE
    place = "" if x is None else f"+{x}+{y}"
    app.root.geometry(f"{w}x{h}{place}")
    app.root.update()
    pump(app, lambda: abs(app.root.winfo_width() - w) <= 2, timeout=3)


@pytest.mark.parametrize("lang", i18n.LANGS)
def test_tooltip_fits_the_minimum_window(env, lang):
    i18n.init(lang)
    app = start(env)
    minimum_window(app)
    tip = hover(app)
    x0, y0, x1, y1 = tip_rect(tip)
    wx0, wy0, wx1, wy1 = window_rect(app)
    assert wx0 <= x0 and x1 <= wx1 and wy0 <= y0 and y1 <= wy1, ((x0, y0, x1, y1), (wx0, wy0, wx1, wy1))
    assert 0 <= x0 and x1 <= app.root.winfo_screenwidth() and 0 <= y0 and y1 <= app.root.winfo_screenheight()
    link_bottom = app.lnk_feedback.winfo_rooty() + app.lnk_feedback.winfo_height()
    assert y0 >= link_bottom                             # under the link, not over it
    assert tip.label.cget("text") == TIPS[lang]          # the whole text, nothing cut


def test_tooltip_stays_on_screen_with_the_window_at_the_right_edge(env):
    import gui

    app = start(env)
    sw = app.root.winfo_screenwidth()
    minimum_window(app, sw - gui.MIN_SIZE[0] - gui.FRAME_W, 0)
    tip = hover(app)
    x0, _, x1, _ = tip_rect(tip)
    _, _, wx1, _ = window_rect(app)
    assert x1 <= min(wx1, sw) and x0 >= 0


def test_tooltip_placement_is_clamped_inside_the_window(env):
    """The rule itself, with a window narrower than the note: it starts at the window's
    left edge instead of running off the right side."""
    app = start(env)
    tip = hover(app)
    tip.label.configure(text="x" * 400, wraplength=0)    # wider than the window
    tip._place()
    x0, _, _, _ = tip_rect(tip)
    wx0, _, _, _ = window_rect(app)
    assert x0 == wx0


def test_closing_with_a_tooltip_open_or_pending_is_clean(env):
    errors = []
    app = start(env)
    app.root.report_callback_exception = lambda *exc: errors.append(exc)
    hover(app)
    app.lnk_feedback.event_generate("<Leave>")
    app.lnk_feedback.event_generate("<Enter>", x=3, y=3)  # pending again when the window closes
    app.root.update()
    close(app)
    assert errors == []


def test_window_is_released_with_a_tooltip_open(env):
    app = start(env)
    hover(app)
    ref = weakref.ref(app)
    close(app)
    env["apps"].remove(app)
    del app
    gc.collect()
    assert ref() is None


# ------------------------------------------------------------------ c. one language rule

KOREAN = ["ko", "ko-KR", "ko_KR", "KO", "Ko-kr", " ko "]
ENGLISH = ["en", "ja", "zh", "zh-CN", "zh_CN", "zh-TW", "zh-HK", "fr", "de", "EN", "en-US", "es-419", "pt_BR",
           "xx", "kok", "korean-ish", "k", "o"]


@pytest.mark.parametrize("lang", KOREAN)
def test_korean_codes_get_the_korean_page(lang):
    assert uc.is_korean_page(lang)
    assert uc.feedback_url("pdfmerge", "0.1.2", lang) == KO.replace("0.1.1", "0.1.2")


@pytest.mark.parametrize("lang", ENGLISH)
def test_every_other_code_gets_the_english_page(lang):
    assert not uc.is_korean_page(lang)
    assert uc.feedback_url("pdfmerge", "0.1.2", lang) == EN.replace("0.1.1", "0.1.2")


@pytest.mark.parametrize("lang", [None, "", "   ", 0, 42, ["ko"], {"ko": 1}, b"ko"])
def test_no_language_follows_the_default_screen_language(lang, monkeypatch):
    assert i18n.DEFAULT_LANG == "en"
    assert uc.feedback_url("pdfmerge", "0.1.2", lang) == EN.replace("0.1.1", "0.1.2")
    monkeypatch.setattr(uc, "DEFAULT_LANG", "ko")        # the rule follows the default, it does not hard-code it
    assert uc.feedback_url("pdfmerge", "0.1.2", lang) == KO.replace("0.1.1", "0.1.2")


@pytest.mark.parametrize("lang", KOREAN + ENGLISH + [None, ""])
def test_every_address_has_the_same_shape(lang):
    url = uc.feedback_url(uc.SLUG, APP_VERSION, lang)
    assert url.startswith("https://chaekgalpi.co.kr/") and url.endswith("#feedback") and url.count("#") == 1
    parts = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qs(parts.query)
    assert q["utm_source"] == ["app"] and q["utm_medium"] == ["feedback"] and q["v"] == [APP_VERSION]
    assert parts.path == ("/tools/pdfmerge" if uc.is_korean_page(lang) else "/en/tools/pdfmerge")
    assert "pdfmergeweb" not in url


def test_windows_slug_is_not_the_web_one():
    assert uc.SLUG == "pdfmerge" != "pdfmergeweb"
    for lang in KOREAN + ENGLISH:
        path = urllib.parse.urlsplit(uc.feedback_url(uc.SLUG, APP_VERSION, lang)).path
        assert path.rsplit("/", 1)[1] == "pdfmerge"


UPDATE = {"latest": "9.9.9", "updateAvailable": True,
          "url": "https://chaekgalpi.co.kr/tools/pdfmerge?utm_source=app&utm_medium=update",
          "urlEn": "https://chaekgalpi.co.kr/en/tools/pdfmerge?utm_source=app&utm_medium=update"}


@pytest.mark.parametrize("lang", KOREAN + ENGLISH + [None, "", 5])
def test_update_page_follows_the_same_rule(lang):
    info = uc.interpret(UPDATE, "0.1.2")
    feedback_en = "/en/tools/" in uc.feedback_url(uc.SLUG, "0.1.2", lang)
    assert uc.page_for(info, lang) == (UPDATE["urlEn"] if feedback_en else UPDATE["url"])


@pytest.mark.parametrize("lang", ENGLISH + [None])
def test_update_page_without_an_english_one_stays_korean(lang):
    info = uc.interpret(dict(UPDATE, urlEn=None), "0.1.2")
    assert uc.page_for(info, lang) == UPDATE["url"]


# ------------------------------------------------------------------ d. README

README_LINK = re.compile(r"https://chaekgalpi\.co\.kr/[^\s)>\"']*#feedback")


@pytest.mark.parametrize("name, expected", [("README.ja.md", FEEDBACK_EN), ("README.zh-CN.md", FEEDBACK_EN),
                                            ("README.en.md", FEEDBACK_EN), ("README.md", FEEDBACK_KO)])
def test_readme_feedback_links(name, expected):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        text = f.read()
    links = README_LINK.findall(text)
    assert links and set(links) == {expected}, (name, links)
    assert "pdfmergeweb" not in text


# ------------------------------------------------------------------ f. not one request

def test_a_whole_keyboard_and_tooltip_session_sends_nothing(env):
    """Every new path in one run; the env fixture fails the test on any socket or request."""
    app = start(env)
    focus_link_by_tab(app)
    hover(app)
    for lang in ("en", "ja", "zh-CN", "ko"):
        switch(app, lang)
        app.lnk_feedback.event_generate("<Return>")
        app.lnk_feedback.event_generate("<space>")
        app.root.update()
    app.lnk_feedback.event_generate("<Leave>")
    app.root.update()
    assert env["opened"] == [address(x) for x in ("en", "en", "ja", "ja", "zh-CN", "zh-CN", "ko", "ko")]
