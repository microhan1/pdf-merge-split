"""The command line: --version, translated --help, a merge, a shrink, the exit codes,
and that a CLI run never asks the update server or leaves settings in the repository."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pymupdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures as fx  # noqa: E402
from version import APP_VERSION  # noqa: E402

MAIN = os.path.join(ROOT, "main.py")
SAMPLES = os.path.join(ROOT, "samples")


def cli(*args, cwd=None):
    p = subprocess.run([sys.executable, "-W", "ignore", MAIN, *args], capture_output=True, cwd=cwd,
                       stdin=subprocess.DEVNULL, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=180)
    return p.returncode, p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")


def test_version():
    code, out = cli("--version")
    assert code == 0 and out.strip() == f"pdf-merge-split {APP_VERSION}"


def test_help_is_translated():
    for lang, word in (("ko", "사용법"), ("en", "usage"), ("zh-CN", "用法"), ("ja", "使い方")):
        code, out = cli("--help", "--lang", lang)
        assert code == 0 and "--merge" in out and "--shrink" in out and word in out


def test_merge_from_the_command_line(tmp_path):
    a = shutil.copy(os.path.join(SAMPLES, "sample_report_a.pdf"), tmp_path / "a.pdf")
    b = shutil.copy(os.path.join(SAMPLES, "sample_report_b.pdf"), tmp_path / "b.pdf")
    before = (fx.sha1(a), fx.sha1(b))
    code, out = cli(a, b, "--merge", "--pages", "1-2", "--lang", "en")
    assert code == 0, out
    assert "Saved:" in out and "2 files merged into 6 pages" in out
    doc = pymupdf.open(tmp_path / "a_merged.pdf")
    assert len(doc) == 6 and doc.get_toc()[0][1] == "a"
    doc.close()
    assert (fx.sha1(a), fx.sha1(b)) == before


def test_shrink_from_the_command_line(tmp_path):
    s = shutil.copy(os.path.join(SAMPLES, "sample_scan.pdf"), tmp_path / "scan.pdf")
    code, out = cli(s, "--shrink", "--quality", "small", "--dpi", "150", "--lang", "ko")
    assert code == 0, out
    out_path = tmp_path / "scan_small.pdf"
    assert out_path.exists() and out_path.stat().st_size < os.path.getsize(s) * 0.5
    assert "저장:" in out and "완료: 1개 파일" in out


def test_shrink_with_explicit_output(tmp_path):
    s = shutil.copy(os.path.join(SAMPLES, "sample_scan.pdf"), tmp_path / "scan.pdf")
    code, out = cli(s, "--shrink", "-o", str(tmp_path / "out.pdf"), "--lang", "en")
    assert code == 0 and (tmp_path / "out.pdf").exists()


def test_exit_codes(tmp_path):
    a = fx.text_pdf(str(tmp_path / "a.pdf"), pages=2)
    locked = fx.encrypted_pdf(str(tmp_path / "locked.pdf"))
    assert cli(a, "--lang", "en")[0] == 2                                   # no mode
    assert cli(str(tmp_path / "missing.pdf"), "--merge", "--lang", "en")[0] == 2
    assert cli(a, "--merge", "--pages", "1-9", "--lang", "en")[0] == 2       # bad range, translated
    code, out = cli(a, locked, "--shrink", "--lang", "en")
    assert code == 1 and "Password-protected, skipping: locked.pdf" in out  # one failure in a batch
    code, out = cli(a, locked, "--merge", "--lang", "en")
    assert code == 1 and (tmp_path / "a_merged.pdf").exists()
    assert cli(a, "--dpi", "123", "--shrink", "--lang", "ko")[0] == 2        # argparse error, translated


def test_folder_inputs_uppercase_extension_and_extra_page_specs(tmp_path):
    sub = tmp_path / "in"
    sub.mkdir()
    a = fx.text_pdf(str(sub / "a.PDF"), pages=3, title="A")
    b = fx.text_pdf(str(sub / "b.pdf"), pages=2, title="B")
    (sub / "notes.txt").write_text("not a pdf")
    code, out = cli(str(sub), "--merge", "--pages", "2-3", "--pages", "all", "--pages", "9-9", "--lang", "en")
    assert code == 0, out                                    # the third spec has no file: ignored
    doc = pymupdf.open(sub / "a_merged.pdf")
    assert len(doc) == 4 and "A page 2" in doc[0].get_text() and "B page 2" in doc[3].get_text()
    doc.close()
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "x.txt").write_text("x")
    assert cli(str(empty), "--merge", "--lang", "en")[0] == 2


def test_output_equal_to_an_input_is_refused(tmp_path):
    a = fx.text_pdf(str(tmp_path / "a.pdf"), pages=1)
    b = fx.text_pdf(str(tmp_path / "b.pdf"), pages=1)
    s = fx.image_pdf(str(tmp_path / "s.pdf"), pages=1, dpi=100)
    before = (fx.sha1(a), fx.sha1(b), fx.sha1(s))
    code, out = cli(a, b, "--merge", "-o", str(tmp_path / "A.PDF"), "--lang", "en")
    assert code == 2 and "not overwritten: A.PDF" in out
    code, out = cli(s, "--shrink", "-o", s, "--lang", "ko")
    assert code == 2 and "원본을 덮어쓰지 않습니다" in out
    assert (fx.sha1(a), fx.sha1(b), fx.sha1(s)) == before
    assert sorted(os.listdir(tmp_path))[:3] == ["a.pdf", "b.pdf", "s.pdf"]


def test_shrink_batch_mixes_results_and_failures(tmp_path):
    scan = fx.image_pdf(str(tmp_path / "scan.pdf"), pages=1, dpi=200)
    text = fx.text_pdf(str(tmp_path / "text.pdf"), pages=1)
    broken = fx.not_a_pdf(str(tmp_path / "broken.pdf"))
    code, out = cli(scan, text, broken, "--shrink", "--lang", "en")
    assert code == 1
    assert "Saved:" in out and "scan_small.pdf" in out
    assert "Already small, could not shrink it further: text.pdf" in out or "text_small.pdf" in out
    assert "Cannot open file: broken.pdf" in out
    assert "Done: " in out


def test_help_and_errors_in_every_language_do_not_crash_on_cp949(tmp_path):
    """A console that cannot print CJK must not kill the program: stdout is reconfigured."""
    a = fx.text_pdf(str(tmp_path / "日本語 파일.pdf"), pages=1)
    for lang in ("ko", "zh-CN", "ja"):
        p = subprocess.run([sys.executable, "-W", "ignore", MAIN, a, "--lang", lang], capture_output=True,
                           stdin=subprocess.DEVNULL, env={k: v for k, v in os.environ.items() if k != "PYTHONIOENCODING"},
                           timeout=120)
        assert p.returncode == 2 and b"Traceback" not in p.stderr, (lang, p.stderr[-300:])


def test_cli_leaves_no_settings_in_the_repository(tmp_path):
    a = fx.text_pdf(str(tmp_path / "a.pdf"), pages=1)
    b = fx.text_pdf(str(tmp_path / "b.pdf"), pages=1)
    assert cli(a, b, "--merge", "--lang", "en", cwd=ROOT)[0] == 0
    assert not os.path.exists(os.path.join(ROOT, "settings.json"))


def test_cli_never_asks_the_update_server(tmp_path, monkeypatch, no_update_server):
    import main

    a = fx.text_pdf(str(tmp_path / "a.pdf"), pages=1)
    b = fx.text_pdf(str(tmp_path / "b.pdf"), pages=1)
    assert main.main([a, b, "--merge", "--lang", "en"]) == 0
    assert no_update_server.requests == []
