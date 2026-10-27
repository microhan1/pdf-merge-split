"""Once a day after start: ask the Chaekgalpi tools site whether a newer version exists.

Only the tool's slug, its version and "win" are sent - no file, path, device or user
information. The request runs on a background thread 1.5 s after the window is shown and
never blocks the UI; any failure (offline, timeout, HTTP error, bad JSON) is silent. The
server decides whether the version is older (`updateAvailable`); null means "cannot
compare" and shows nothing. Standard library only.

feedback_url() only builds the address [Send feedback] hands to the browser; the
program itself sends nothing for it.
"""
from __future__ import annotations

import dataclasses
import datetime
import json
import threading
import urllib.parse
import urllib.request
from typing import Callable

from i18n import DEFAULT_LANG

API = "https://chaekgalpi.co.kr/api/tools/{slug}/latest"
SITE = "https://chaekgalpi.co.kr/"  # the notice only ever opens a page on this site
SLUG = "pdfmerge"                   # this tool's name on the Chaekgalpi tools site
TIMEOUT = 4.0
DELAY_MS = 1500
POLL_MS = 100                       # how often the Tk thread looks for the answer
MAX_BYTES = 64 * 1024


@dataclasses.dataclass
class UpdateInfo:
    latest: str
    current: str
    url: str                        # the tool's page on the Chaekgalpi site (Korean)
    url_en: str | None = None       # the same page in English, when the server offers one


@dataclasses.dataclass
class UpdatePrefs:
    update_check: bool = True
    update_check_last: str = ""      # YYYY-MM-DD of the last day the server answered
    update_check_skipped: str = ""   # the version the user said "Later" to


def prefs_from_settings(settings: dict) -> UpdatePrefs:
    """The three update keys of settings.json, trusting nothing about their types:
    the file sits beside the exe where anyone can edit it."""
    settings = settings if isinstance(settings, dict) else {}
    on, last, skipped = (settings.get(k) for k in ("update_check", "update_check_last", "update_check_skipped"))
    return UpdatePrefs(on if isinstance(on, bool) else True,
                       last if isinstance(last, str) else "",
                       skipped if isinstance(skipped, str) else "")


def today() -> str:
    return datetime.date.today().isoformat()


def should_check(prefs, day: str) -> bool:
    """Checking is on and has not been done today."""
    return bool(getattr(prefs, "update_check", True)) and getattr(prefs, "update_check_last", "") != day


def fetch(slug: str, version: str, *, opener: Callable = urllib.request.urlopen) -> dict | None:
    """The server's answer, or None on any failure. The response body is never logged
    or shown: whatever the server sends back stays inside this function."""
    query = urllib.parse.urlencode({"v": version, "os": "win"})
    req = urllib.request.Request(f"{API.format(slug=slug)}?{query}",
                                 headers={"User-Agent": f"chaekgalpi-tool/{slug}/{version}",
                                          "Accept": "application/json"})
    try:
        with opener(req, timeout=TIMEOUT) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            data = json.loads(resp.read(MAX_BYTES).decode("utf-8"))
    except Exception:  # offline, timeout, HTTP error, bad JSON: say nothing
        return None
    return data if isinstance(data, dict) else None


def interpret(data: dict | None, version: str, skipped: str = "") -> UpdateInfo | None:
    """An UpdateInfo only when the server says a newer version exists, it is not the
    version the user chose to skip, and the link points at the tools site."""
    if not isinstance(data, dict) or data.get("updateAvailable") is not True:
        return None
    latest, url = data.get("latest"), data.get("url")
    if not isinstance(latest, str) or not latest.strip() or len(latest) > 32:
        return None
    if not isinstance(url, str) or not url.startswith(SITE):
        return None
    latest = latest.strip()
    if latest == (skipped or "").strip():
        return None
    url_en = data.get("urlEn")
    url_en = url_en if isinstance(url_en, str) and url_en.startswith(SITE) else None
    return UpdateInfo(latest, version, url, url_en)


def is_korean_page(lang) -> bool:
    """The one rule for which page of the tools site a screen language gets, shared by
    [Update] and [Send feedback]: a code whose primary subtag is "ko" (ko, ko-KR, KO,
    ko_KR) gets the Korean page; every other code - listed or not (en, ja, zh, zh-TW,
    fr...) - gets the English one. None, empty or not a string means this tool's
    default screen language."""
    code = lang.strip().lower().replace("_", "-") if isinstance(lang, str) else ""
    return (code or DEFAULT_LANG.lower()).split("-")[0] == "ko"


def page_for(info: UpdateInfo, lang) -> str:
    """The page [Update] opens: Korean screens get the Korean page; every other
    language gets the English page when the server gave one on the tools site."""
    if not is_korean_page(lang) and info.url_en and info.url_en.startswith(SITE):
        return info.url_en
    return info.url


def feedback_url(slug: str, version, lang) -> str:
    """The feedback form on the tool's page, for [Send feedback] to open in the browser.
    Built from constants only - never from settings or a server answer. Korean screens
    get the Korean page and every other language the English one (is_korean_page; there
    is no Japanese or Chinese page). utm_source=app tells the site it came from the
    program; #feedback scrolls to the form. The version is percent-encoded so nothing in
    it can break the address, and left out when empty."""
    page = "tools" if is_korean_page(lang) else "en/tools"
    query = "utm_source=app&utm_medium=feedback"
    version = version.strip() if isinstance(version, str) else ""
    if version:
        query += "&v=" + urllib.parse.quote(version, safe="")
    return f"{SITE}{page}/{urllib.parse.quote(slug, safe='')}?{query}#feedback"


def check_async(root, slug: str, version: str, prefs, on_result: Callable[[UpdateInfo | None, bool], None], *,
                fetcher: Callable = fetch, day: str | None = None) -> bool:
    """Schedule one check DELAY_MS after now. on_result(info, answered) runs on the Tk
    thread: `answered` is True when the server replied (the caller records the day then),
    `info` is set only when a notice should be shown. Returns False when no check is due."""
    if not should_check(prefs, day or today()):
        return False
    skipped = getattr(prefs, "update_check_skipped", "")
    box: dict = {}

    # The thread only fetches into `box` and holds no Tk object. If it handed the
    # result over with root.after(..., on_result), a window closed while the request
    # was out would leave the thread with the last reference to the App; its Tk
    # variables would be freed off the main thread and Tcl aborts ("Tcl_AsyncDelete:
    # async handler deleted by the wrong thread"). The Tk thread polls instead.
    def work(box=box) -> None:
        box["data"] = fetcher(slug, version)

    def poll() -> None:
        if "data" not in box:
            root.after(POLL_MS, poll)
            return
        data = box["data"]
        on_result(interpret(data, version, skipped), data is not None)

    def start() -> None:
        threading.Thread(target=work, name="update-check", daemon=True).start()
        poll()

    try:
        root.after(DELAY_MS, start)
    except Exception:
        return False
    return True
