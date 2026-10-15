# -*- coding: utf-8 -*-
"""Check a built exe: it opens, its window carries the translated title and the
version, it writes settings.json where told, and - unless --online - it never
reaches the update server.

    python tests/exe_check.py dist/pdf-merge-split.exe            offline: a dead proxy blocks HTTPS
    python tests/exe_check.py dist/pdf-merge-split.exe --online   release check: asks the real server ONCE

The exe is another process, so the test guards cannot reach it: in offline
mode HTTPS_PROXY points at a port nobody listens on, and the check passes
only when no update_check_last was recorded. Settings go to a temporary file
through PDF_MERGE_SPLIT_SETTINGS, so the user's settings beside the exe are
never written. Only the window this script opened is closed (found by title
through EnumWindows: a onefile exe runs the window in a child process, so the
launcher's PID would not do).
"""
from __future__ import annotations

import ctypes
import datetime
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from version import APP_VERSION  # noqa: E402

user32 = ctypes.windll.user32
WM_CLOSE = 0x0010


def find_window(title_part: str) -> int:
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)
            if title_part in buf.value:
                found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    exe = os.path.abspath(argv[0])
    online = "--online" in argv
    lang = "ko"
    for a in argv:
        if a.startswith("--lang="):
            lang = a.split("=", 1)[1]
    work = tempfile.mkdtemp(prefix="pms_exe_")
    settings = os.path.join(work, "settings.json")
    with open(settings, "w", encoding="utf-8") as f:
        json.dump({"lang": lang}, f)
    env = dict(os.environ, PDF_MERGE_SPLIT_SETTINGS=settings)
    if online:
        print("ONLINE: the real server is asked once (release check only)")
    else:
        env.update(HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9",
                   https_proxy="http://127.0.0.1:9", http_proxy="http://127.0.0.1:9", NO_PROXY="", no_proxy="")
    titles = {"ko": "PDF 합치기·용량 줄이기", "en": "PDF Merge & Compress", "zh-CN": "PDF 合并·压缩", "ja": "PDF 結合・圧縮"}
    expect = f"{titles[lang]} v{APP_VERSION}"
    t0 = time.time()
    proc = subprocess.Popen([exe], env=env, cwd=work)
    hwnd = 0
    while time.time() - t0 < 60 and not hwnd:
        time.sleep(0.5)
        hwnd = find_window(expect)
    opened = time.time() - t0
    print(f"window {'found' if hwnd else 'NOT found'} ({opened:.1f}s): {expect!r}")
    # give the update check its 1.5 s delay + 4 s timeout + a little
    time.sleep(8 if not online else 12)
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0) if hwnd else None
    t1 = time.time()
    while time.time() - t1 < 40 and find_window(expect):
        time.sleep(0.25)
    gone = not find_window(expect)
    while time.time() - t1 < 40 and proc.poll() is None:      # the onefile launcher cleans up after the window
        time.sleep(0.25)
    print(f"window closed on WM_CLOSE: {gone} ({time.time() - t1:.1f}s); launcher exited: {proc.poll() is not None}")
    if not gone or proc.poll() is None:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        print("killed what was left")
    saved = {}
    if os.path.exists(settings):
        try:
            saved = json.load(open(settings, encoding="utf-8"))
        except ValueError:
            pass
    print("settings after close:", saved)
    today = datetime.date.today().isoformat()
    ok = bool(hwnd) and gone and "mode" in saved
    if online:
        reached = saved.get("update_check_last") == today
        print("server answered:", reached)
        ok = ok and reached
    else:
        leaked = bool(saved.get("update_check_last"))
        print("offline guard:", "FAILED - the server was reached" if leaked else "passed (no request got out)")
        ok = ok and not leaked
    print("RESULT:", "pass" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
