"""Check a Chaekgalpi tool's language files against each other and the code.

    python check_i18n.py <repo>

Checks: the four lang files have the same keys, placeholders match across
languages, keys are snake_case, every key used in the code is defined, every
defined key is used somewhere, and no non-ASCII string literal (a hard-coded
UI string) is left in the code apart from language names. Exit 0 = clean.
"""
from __future__ import annotations

import json
import os
import re
import sys

LANGS = ("ko", "en", "zh-CN", "ja")
LANG_NAME_LITERALS = {"한국어", "English", "简体中文", "日本語"}


def main(repo: str) -> int:
    problems: list[str] = []
    data = {}
    for lang in LANGS:
        path = os.path.join(repo, "lang", f"{lang}.json")
        try:
            data[lang] = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"cannot read {path}: {exc}")
    if len(data) != len(LANGS):
        return report(problems)

    base = set(data["en"])
    for lang in LANGS:
        missing = sorted(base - set(data[lang]))
        extra = sorted(set(data[lang]) - base)
        if missing:
            problems.append(f"{lang}: missing keys {missing}")
        if extra:
            problems.append(f"{lang}: extra keys {extra}")
    for key in sorted(base):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            problems.append(f"key not snake_case: {key}")
        ph = {lang: sorted(re.findall(r"\{(\w+)\}", str(data[lang].get(key, "")))) for lang in LANGS}
        if len({tuple(v) for v in ph.values()}) > 1:
            problems.append(f"placeholders differ for {key}: {ph}")
        empty = [lang for lang in LANGS if not str(data[lang].get(key, "")).strip()]
        if empty:
            problems.append(f"empty text for {key} in {empty}")

    code = ""
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in {".git", "build", "dist", "__pycache__", "venv", ".venv", "samples", "tests"}]
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(root, name)
                src = open(path, encoding="utf-8").read()
                code += src
                for lit in re.findall(r'(["\'])((?:(?!\1).)*[^\x00-\x7f](?:(?!\1).)*)\1', src):
                    text = lit[1]
                    if text not in LANG_NAME_LITERALS and not text.lstrip().startswith("#"):
                        problems.append(f"non-ASCII literal in {os.path.relpath(path, repo)}: {text[:40]!r}")

    quoted = set(re.findall(r'["\']([a-z][a-z0-9_]*)["\']', code))
    used_direct = set(re.findall(r'\bt\(\s*["\']([a-z][a-z0-9_]*)["\']', code))
    # keys built with f-strings, e.g. f"contrast_{lvl}", count as used when the prefix appears
    prefixes = set(re.findall(r'f["\']([a-z][a-z0-9_]*_)\{', code))
    for key in sorted(used_direct - base):
        problems.append(f"used in code but not defined: {key}")
    for key in sorted(base):
        if key not in quoted and not any(key.startswith(p) for p in prefixes):
            problems.append(f"defined but never referenced in code: {key}")

    print(f"{len(base)} keys x {len(LANGS)} languages")
    return report(problems)


def report(problems: list[str]) -> int:
    for p in problems:
        print("PROBLEM:", p)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):  # Korean/CJK text on a cp949 console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
