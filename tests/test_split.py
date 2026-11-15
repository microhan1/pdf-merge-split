"""Splitting: the four ways, what each part keeps, names that never overwrite, nothing
left behind on cancel or failure, the size cap measured on the saved files, and the CLI."""
from __future__ import annotations

import os
import stat
import sys
import threading

import pymupdf
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures as fx  # noqa: E402
import pdfops  # noqa: E402
from pdfops import SplitOptions  # noqa: E402
from test_cli_smoke import cli  # noqa: E402


def texts(path: str) -> list[str]:
    doc = pymupdf.open(path)
    try:
        return [p.get_text() for p in doc]
    finally:
        doc.close()


def toc(path: str) -> list:
    doc = pymupdf.open(path)
    try:
        return doc.get_toc(simple=True)
    finally:
        doc.close()


def leftovers(folder) -> list[str]:
    return [n for n in os.listdir(folder) if n.endswith(".part")]


def numbered_text_pdf(path: str, pages: int, toc_entries=None) -> str:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((50, 60), f"page {i + 1:03d} body", fontsize=12, fontname="helv")
    if toc_entries:
        doc.set_toc(toc_entries)
    doc.set_metadata({"title": "Numbered", "author": "tests"})
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


# ------------------------------------------------------------------ options and parsing
def test_options_are_validated():
    assert SplitOptions("nonsense", every=0, max_mb=-1).validated() == SplitOptions("every", "", 10, 10.0, True)
    assert SplitOptions("every", every="25").validated().every == 25
    assert SplitOptions("every", every=True).validated().every == pdfops.DEFAULT_EVERY
    assert SplitOptions("every", every=10**9).validated().every == pdfops.EVERY_RANGE[1]
    assert SplitOptions("size", max_mb="2.5").validated().max_mb == 2.5
    assert SplitOptions("size", max_mb=0.001).validated().max_mb == pdfops.MAX_MB_RANGE[0]
    assert SplitOptions("size", max_mb=float("nan")).validated().max_mb == pdfops.DEFAULT_MAX_MB
    assert SplitOptions("size", max_mb=1).validated().max_bytes == 1024 * 1024
    assert SplitOptions(ranges=5).validated().ranges == ""


@pytest.mark.parametrize("spec, count, expect", [
    ("1-10, 11-25", 25, [(0, 9), (10, 24)]),
    ("3", 5, [(2, 2)]),
    ("2-", 5, [(1, 4)]),
    ("-2", 5, [(0, 1)]),
    ("4-5; 1-2", 5, [(3, 4), (0, 1)]),       # order is the user's
    ("1-3, 2-4", 5, [(0, 2), (1, 3)]),       # overlap allowed: each piece is its own file
    (" 1 - 2 ,, 5 ", 5, [(0, 1), (4, 4)]),
])
def test_parse_ranges(spec, count, expect):
    assert [(p.first, p.last) for p in pdfops.parse_ranges(spec, count)] == expect


@pytest.mark.parametrize("spec", ["", "all", "0", "6", "4-3", "1-9", "a", "-", ",", "1-2-3"])
def test_parse_ranges_rejects(spec):
    with pytest.raises(pdfops.InvalidPageRange):
        pdfops.parse_ranges(spec, 5)


def test_counts_text():
    assert pdfops.counts_text([10, 10, 5]) == "10, 10, 5"
    assert pdfops.counts_text([3] * 8) == ", ".join(["3"] * 8)
    assert pdfops.counts_text([10] * 20 + [7]) == "10, 10, 10, 10, 10, 10, ..., 10, 7"


# ------------------------------------------------------------------ the four ways
@pytest.mark.parametrize("pages, every, expect", [(25, 10, [10, 10, 5]), (20, 10, [10, 10]), (7, 1, [1] * 7),
                                                  (11, 10, [10, 1])])
def test_every_n_pages_covers_the_document_in_order(tmp_path, pages, every, expect):
    src = numbered_text_pdf(str(tmp_path / "book.pdf"), pages)
    h = fx.sha1(src)
    r = pdfops.split_pdf(src, SplitOptions("every", every=every))
    assert r.part_pages == expect and sum(r.part_pages) == pages == r.pages
    got = [t for out in r.outputs for t in texts(out)]
    assert got == texts(src)                                   # every page once, in order
    assert [os.path.basename(o) for o in r.outputs] == [f"book_part{i:02d}.pdf" for i in range(1, len(expect) + 1)]
    assert fx.sha1(src) == h and not leftovers(tmp_path)


def test_page_ranges_one_file_each(tmp_path):
    src = numbered_text_pdf(str(tmp_path / "b.pdf"), 30)
    r = pdfops.split_pdf(src, SplitOptions("ranges", ranges="1-10, 11-25, 28"))
    assert r.part_pages == [10, 15, 1]
    assert "page 011" in texts(r.outputs[1])[0] and "page 028" in texts(r.outputs[2])[0]


def test_bookmarks_split_at_top_level_and_keep_inner_entries(tmp_path):
    entries = [[1, "Cover note", 3], [1, "Chapter A", 4], [2, "A.1", 5], [2, "A.2", 6], [1, "Chapter B", 8],
               [2, "B.1", 9], [3, "deep", 9], [1, "Chapter C", 10], [1, "same page", 10]]
    src = numbered_text_pdf(str(tmp_path / "b.pdf"), 12, entries)
    r = pdfops.split_pdf(src, SplitOptions("bookmarks"))
    # pages 1-2 before the first bookmark form their own part
    assert r.part_pages == [2, 1, 4, 2, 3] and sum(r.part_pages) == 12
    assert toc(r.outputs[0]) == []
    assert toc(r.outputs[2]) == [[1, "Chapter A", 1], [2, "A.1", 2], [2, "A.2", 3]]
    assert toc(r.outputs[3]) == [[1, "Chapter B", 1], [2, "B.1", 2], [3, "deep", 2]]
    assert toc(r.outputs[4]) == [[1, "Chapter C", 1], [1, "same page", 1]]
    titles = [pymupdf.open(o).metadata["title"] for o in r.outputs]
    assert titles == ["Numbered", "Cover note", "Chapter A", "Chapter B", "Chapter C"]
    # a part that starts inside a chapter: its first entry is lifted to level 1 and levels never jump
    r = pdfops.split_pdf(src, SplitOptions("ranges", ranges="5-6, 9"))
    assert toc(r.outputs[0]) == [[1, "A.1", 1], [1, "A.2", 2]]
    assert toc(r.outputs[1]) == [[1, "B.1", 1], [2, "deep", 1]]


def test_bookmarks_without_any_top_level_entry(tmp_path):
    src = numbered_text_pdf(str(tmp_path / "plain.pdf"), 5)
    with pytest.raises(pdfops.NoBookmarks):
        pdfops.split_pdf(src, SplitOptions("bookmarks"))
    assert sorted(os.listdir(tmp_path)) == ["plain.pdf"]


def test_nothing_to_split_writes_nothing(tmp_path):
    src = numbered_text_pdf(str(tmp_path / "s.pdf"), 5, [[1, "Only", 1]])
    for opts in (SplitOptions("every", every=5), SplitOptions("every", every=50), SplitOptions("bookmarks"),
                 SplitOptions("ranges", ranges="1-5"), SplitOptions("size", max_mb=100)):
        with pytest.raises(pdfops.NothingToSplit):
            pdfops.split_pdf(src, opts)
    assert sorted(os.listdir(tmp_path)) == ["s.pdf"]
    # one range that is not the whole file is a real extraction, not "nothing"
    assert pdfops.split_pdf(src, SplitOptions("ranges", ranges="2-3")).part_pages == [2]


def test_bookmarks_off(tmp_path):
    src = numbered_text_pdf(str(tmp_path / "b.pdf"), 6, [[1, "A", 1], [1, "B", 4]])
    r = pdfops.split_pdf(src, SplitOptions("every", every=3, keep_bookmarks=False))
    assert [toc(o) for o in r.outputs] == [[], []]


# ------------------------------------------------------------------ size cap
def test_size_cap_holds_on_the_saved_files(tmp_path):
    src = fx.image_pdf(str(tmp_path / "scan.pdf"), pages=8, dpi=150)
    size = os.path.getsize(src)
    cap_mb = round(size / (1024 * 1024) / 3, 2)               # about three parts
    seen = []
    r = pdfops.split_pdf(src, SplitOptions("size", max_mb=cap_mb), progress=lambda d, t: seen.append((d, t)))
    cap = int(cap_mb * 1024 * 1024)
    assert len(r.outputs) >= 3 and sum(r.part_pages) == 8 and not r.oversized
    assert all(os.path.getsize(o) <= cap for o in r.outputs)
    assert seen[-1] == (16, 16) and [d for d, _ in seen] == sorted(d for d, _ in seen)
    assert [t for o in r.outputs for t in texts(o)] == texts(src)


def test_size_cap_halves_a_part_whose_estimate_was_too_low(tmp_path, monkeypatch):
    src = fx.image_pdf(str(tmp_path / "scan.pdf"), pages=6, dpi=120)
    one_page = os.path.getsize(src) / 6
    # pretend every object is tiny: the plan puts all pages in one part, the measuring must fix it
    real_load = pdfops._ObjectSizes._load

    def tiny(self, xref):
        real_load(self, xref)
        self.size[xref] = 1

    monkeypatch.setattr(pdfops._ObjectSizes, "_load", tiny)
    cap_mb = round(one_page * 2.5 / (1024 * 1024), 3)
    r = pdfops.split_pdf(src, SplitOptions("size", max_mb=cap_mb))
    cap = int(SplitOptions("size", max_mb=cap_mb).validated().max_bytes)
    assert sum(r.part_pages) == 6 and len(r.outputs) >= 3
    assert all(os.path.getsize(o) <= cap for o in r.outputs)
    assert not leftovers(tmp_path)


def test_size_mode_judges_nothing_to_split_from_real_sizes(tmp_path, monkeypatch):
    """A file over the cap whose estimate says "one part" is still measured and split;
    a file under the cap is left alone before any work."""
    src = fx.image_pdf(str(tmp_path / "scan.pdf"), pages=4, dpi=120)
    size = os.path.getsize(src)
    with pytest.raises(pdfops.NothingToSplit):
        pdfops.split_pdf(src, SplitOptions("size", max_mb=size / (1024 * 1024) + 0.01))
    with pytest.raises(pdfops.NothingToSplit):
        pdfops.preview_split(src, SplitOptions("size", max_mb=size / (1024 * 1024) + 0.01))
    monkeypatch.setattr(pdfops, "_size_parts", lambda doc, limit, cancel=None, progress=None:
                        [pdfops.SplitPart(0, len(doc) - 1)])
    r = pdfops.split_pdf(src, SplitOptions("size", max_mb=round(size * 0.6 / (1024 * 1024), 3)))
    assert len(r.outputs) >= 2 and sum(r.part_pages) == 4
    assert sorted(os.listdir(tmp_path))[:1] == ["scan.pdf"] and not leftovers(tmp_path)


def test_a_single_page_over_the_cap_is_kept_and_reported(tmp_path):
    src = fx.image_pdf(str(tmp_path / "big.pdf"), pages=3, dpi=150)
    r = pdfops.split_pdf(src, SplitOptions("size", max_mb=0.1))
    assert r.part_pages == [1, 1, 1] and r.oversized == [1, 2, 3]


def test_shared_images_are_counted_once_per_part(tmp_path):
    src = fx.image_pdf(str(tmp_path / "shared.pdf"), pages=10, dpi=150, shared=True)
    size = os.path.getsize(src)
    # the one image fits under the cap, so all ten pages fit in one file: nothing to split
    with pytest.raises(pdfops.NothingToSplit):
        pdfops.split_pdf(src, SplitOptions("size", max_mb=round(size * 1.5 / (1024 * 1024), 2)))


# ------------------------------------------------------------------ what a part keeps
def test_parts_keep_links_annotations_rotation_and_sizes(tmp_path):
    doc = pymupdf.open()
    for i, (w, h) in enumerate([(595, 842), (842, 595), (300, 400), (595, 842)]):
        page = doc.new_page(width=w, height=h)
        page.insert_text((30, 40), f"p{i + 1}", fontname="helv")
        page.set_rotation((i * 90) % 360)
        page.add_text_annot(pymupdf.Point(60, 60), f"note {i + 1}")
    doc[0].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(30, 100, 200, 120), "uri": "https://example.test/"})
    doc[1].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(30, 100, 200, 120), "page": 0})
    doc[3].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(30, 100, 200, 120), "page": 2})
    doc.save(str(tmp_path / "mix.pdf"))
    doc.close()
    src = str(tmp_path / "mix.pdf")
    r = pdfops.split_pdf(src, SplitOptions("every", every=2))
    a, b = pymupdf.open(r.outputs[0]), pymupdf.open(r.outputs[1])
    assert [p.rotation for p in a] + [p.rotation for p in b] == [0, 90, 180, 270]
    assert [(int(p.mediabox.width), int(p.mediabox.height)) for p in list(a) + list(b)] == \
        [(595, 842), (842, 595), (300, 400), (595, 842)]
    assert all(len(list(p.annots())) == 1 for p in list(a) + list(b))
    assert a[0].get_links()[0]["uri"] == "https://example.test/"
    assert a[1].get_links()[0]["page"] == 0                   # a link inside the part still works
    assert b[1].get_links()[0]["page"] == 0                   # page 3 of the book is page 1 of part 2
    a.close()
    b.close()


def test_owner_password_only_and_locked_files(tmp_path):
    owner = fx.encrypted_pdf(str(tmp_path / "owner.pdf"), user=False)
    r = pdfops.split_pdf(owner, SplitOptions("every", every=1))
    assert r.part_pages == [1, 1] and not pymupdf.open(r.outputs[0]).needs_pass
    with pytest.raises(pdfops.PasswordRequired):
        pdfops.split_pdf(fx.encrypted_pdf(str(tmp_path / "locked.pdf")), SplitOptions("every", every=1))
    with pytest.raises(pdfops.EmptyDocument):
        pdfops.split_pdf(fx.zero_page_pdf(str(tmp_path / "zero.pdf")), SplitOptions("every", every=1))


# ------------------------------------------------------------------ names and safety
def test_names_never_overwrite_and_one_run_shares_one_suffix(tmp_path):
    src = numbered_text_pdf(str(tmp_path / "b.pdf"), 4)
    first = pdfops.split_pdf(src, SplitOptions("every", every=2)).outputs
    os.mkdir(str(tmp_path / "b_part02(2).pdf"))               # a folder takes one name of the next set
    second = pdfops.split_pdf(src, SplitOptions("every", every=2)).outputs
    assert [os.path.basename(p) for p in first] == ["b_part01.pdf", "b_part02.pdf"]
    assert [os.path.basename(p) for p in second] == ["b_part01(3).pdf", "b_part02(3).pdf"]


def test_part_numbers_get_wider_past_99(tmp_path):
    src = numbered_text_pdf(str(tmp_path / "long.pdf"), 120)
    r = pdfops.split_pdf(src, SplitOptions("every", every=1))
    names = [os.path.basename(p) for p in r.outputs]
    assert names[0] == "long_part001.pdf" and names[-1] == "long_part120.pdf" and len(names) == 120


def test_cancel_at_any_step_leaves_nothing(tmp_path):
    src = numbered_text_pdf(str(tmp_path / "b.pdf"), 6)
    steps = []
    done = pdfops.split_pdf(src, SplitOptions("every", every=2), progress=lambda d, t: steps.append(d))
    for p in done.outputs:
        os.remove(p)
    for stop_at in range(len(steps)):
        cancel = threading.Event()
        calls = []

        def progress(d, t, _n=stop_at):
            calls.append(d)
            if len(calls) - 1 == _n:
                cancel.set()

        if stop_at == len(steps) - 1:
            # the last part is saved; the cancel check before renaming still stops the run
            pass
        with pytest.raises(pdfops.Cancelled):
            pdfops.split_pdf(src, SplitOptions("every", every=2), progress=progress, cancel=cancel)
        assert sorted(os.listdir(tmp_path)) == ["b.pdf"], f"stop at step {stop_at}"


def test_a_failing_part_removes_every_temporary_file(tmp_path, monkeypatch):
    src = numbered_text_pdf(str(tmp_path / "b.pdf"), 6)
    real = pdfops._write_part
    calls = []

    def fail_third(*a, **k):
        calls.append(1)
        if len(calls) == 3:
            raise OSError("disk full")
        return real(*a, **k)

    monkeypatch.setattr(pdfops, "_write_part", fail_third)
    with pytest.raises(OSError):
        pdfops.split_pdf(src, SplitOptions("every", every=2))
    assert sorted(os.listdir(tmp_path)) == ["b.pdf"]


def test_a_file_appearing_under_a_chosen_name_is_not_overwritten(tmp_path, monkeypatch):
    src = numbered_text_pdf(str(tmp_path / "b.pdf"), 4)
    real = pdfops.split_output_paths

    def then_someone_writes(input_path, count):
        paths = real(input_path, count)
        with open(paths[1], "wb") as f:                       # appears between naming and renaming
            f.write(b"someone else's file")
        return paths

    monkeypatch.setattr(pdfops, "split_output_paths", then_someone_writes)
    with pytest.raises(OSError):
        pdfops.split_pdf(src, SplitOptions("every", every=2))
    assert open(str(tmp_path / "b_part02.pdf"), "rb").read() == b"someone else's file"
    assert not leftovers(tmp_path)


def test_read_only_original_and_unicode_names(tmp_path):
    folder = tmp_path / "資料 폴더"
    folder.mkdir()
    src = numbered_text_pdf(str(folder / "한글 #1 & 50%.pdf"), 4)
    os.chmod(src, stat.S_IREAD)
    try:
        h = fx.sha1(src)
        r = pdfops.split_pdf(src, SplitOptions("every", every=2))
        assert [os.path.basename(o) for o in r.outputs] == ["한글 #1 & 50%_part01.pdf", "한글 #1 & 50%_part02.pdf"]
        assert fx.sha1(src) == h and not os.access(src, os.W_OK)
    finally:
        os.chmod(src, stat.S_IWRITE | stat.S_IREAD)


def test_preview_matches_the_real_split(tmp_path):
    src = numbered_text_pdf(str(tmp_path / "b.pdf"), 23, [[1, "A", 1], [1, "B", 9], [1, "C", 20]])
    for opts in (SplitOptions("every", every=7), SplitOptions("bookmarks"), SplitOptions("ranges", ranges="1-3, 4-23")):
        assert pdfops.preview_split(src, opts) == pdfops.split_pdf(src, opts).part_pages


def test_sample_scan_split_by_size():
    import shutil
    import tempfile

    work = tempfile.mkdtemp(prefix="pms_split_sample_")
    try:
        src = shutil.copy(os.path.join(ROOT, "samples", "sample_scan.pdf"), work)
        r = pdfops.split_pdf(src, SplitOptions("size", max_mb=1))
        assert sum(r.part_pages) == 3 and all(os.path.getsize(o) <= 1024 * 1024 for o in r.outputs)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ command line
def test_cli_every_bookmarks_and_size(tmp_path):
    src = numbered_text_pdf(str(tmp_path / "book.pdf"), 9, [[1, "A", 1], [1, "B", 5]])
    code, out = cli(src, "--split", "--every", "4", "--lang", "en")
    assert code == 0 and "9 pages → 3 files (4, 4, 1 pages)" in out and out.count("Saved:") == 3
    code, out = cli(src, "--by-bookmarks", "--lang", "ko")              # a way alone means --split
    assert code == 0 and "9쪽 → 2개 파일 (쪽 수 4, 5)" in out
    scan = fx.image_pdf(str(tmp_path / "scan.pdf"), pages=3, dpi=150)
    code, out = cli(scan, "--split", "--max-mb", "0.1", "--lang", "en")
    assert code == 0 and "3 files are over 0.1 MB with a single page" in out


def test_cli_ranges_and_errors(tmp_path):
    src = numbered_text_pdf(str(tmp_path / "b.pdf"), 6)
    code, out = cli(src, "--split", "--ranges", "1-2, 3-6", "--lang", "en")
    assert code == 0 and (tmp_path / "b_part02.pdf").exists()
    code, out = cli(src, "--split", "--ranges", "1-9", "--lang", "en")
    assert code == 1 and "Page range does not fit: 1-9 (b.pdf, pages 1-6)" in out
    code, out = cli(src, "--split", "--lang", "en")
    assert code == 2 and "--split needs one of" in out
    code, out = cli(src, "--merge", "--every", "2", "--lang", "en")
    assert code == 2 and "go with --split only" in out
    code, out = cli(src, "--split", "--every", "2", "--ranges", "1-2", "--lang", "en")
    assert code == 2                                                     # argparse: not allowed with
    code, out = cli(src, "--split", "--by-bookmarks", "--lang", "ja")
    assert code == 1 and "しおりで分割できません" in out
    code, out = cli(src, "--split", "--every", "6", "--lang", "zh-CN")
    assert code == 0 and "无需拆分" in out


def test_cli_split_batch_continues_past_a_bad_file(tmp_path):
    a = numbered_text_pdf(str(tmp_path / "a.pdf"), 4)
    locked = fx.encrypted_pdf(str(tmp_path / "locked.pdf"))
    b = numbered_text_pdf(str(tmp_path / "b.pdf"), 4)
    code, out = cli(a, locked, b, "--split", "--every", "2", "--lang", "en")
    assert code == 1 and "Password-protected, skipping: locked.pdf" in out
    assert "Done: 2 files split into 4 files" in out
