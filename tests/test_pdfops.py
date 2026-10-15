"""Merging and shrinking on generated PDFs: results, what is kept, what never happens."""
from __future__ import annotations

import os
import sys
import threading
import time

import pymupdf
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures as fx  # noqa: E402
import pdfops  # noqa: E402

SAMPLES = os.path.join(ROOT, "samples")


def pages_of(path: str) -> int:
    doc = pymupdf.open(path)
    try:
        return len(doc)
    finally:
        doc.close()


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


def links(path: str, page: int = 0) -> list:
    doc = pymupdf.open(path)
    try:
        return [(ln["kind"], ln.get("page"), ln.get("uri")) for ln in doc[page].get_links()]
    finally:
        doc.close()


def no_part_files(folder) -> bool:
    return not [n for n in os.listdir(folder) if n.endswith(".part")]


# ------------------------------------------------------------------ page ranges
@pytest.mark.parametrize("spec, count, expect", [
    ("", 5, [0, 1, 2, 3, 4]),
    ("all", 5, [0, 1, 2, 3, 4]),
    ("ALL ", 3, [0, 1, 2]),
    ("1-3", 5, [0, 1, 2]),
    ("3", 5, [2]),
    ("2-", 5, [1, 2, 3, 4]),
    ("-2", 5, [0, 1]),
    ("1, 3-4;5", 5, [0, 2, 3, 4]),
    ("4-5, 1-2", 5, [3, 4, 0, 1]),            # order is the user's
    ("1,1", 5, [0, 0]),                        # a page may be repeated
    ("3 - 4", 5, [2, 3]),
])
def test_parse_pages(spec, count, expect):
    assert pdfops.parse_pages(spec, count) == expect


@pytest.mark.parametrize("spec", ["0", "6", "4-3", "1-9", "a", "1-2-3", "2,,x", "-", ","])
def test_parse_pages_rejects(spec):
    with pytest.raises(pdfops.InvalidPageRange):
        pdfops.parse_pages(spec, 5)


# ------------------------------------------------------------------ merging
def test_merge_order_ranges_bookmarks_links_and_metadata(tmp_path):
    a = fx.text_pdf(str(tmp_path / "a.pdf"), pages=4, title="A")
    b = fx.text_pdf(str(tmp_path / "b.pdf"), pages=3, title="B")
    c = fx.image_pdf(str(tmp_path / "c.pdf"), pages=2, dpi=72)
    before = {p: fx.sha1(p) for p in (a, b, c)}
    seen = []
    r = pdfops.merge_pdfs([(b, "3-"), (a, "1,2"), (c, "")], pdfops.MergeOptions(),
                          progress=lambda d, t: seen.append((d, t)))
    assert r.output_path == str(tmp_path / "b_merged.pdf") and r.files == 3 and r.pages == 5
    assert seen[-1] == (5, 5) and all(t == 5 for _, t in seen)
    got = texts(r.output_path)
    assert "B page 3" in got[0] and "A page 1" in got[1] and "A page 2" in got[2]
    assert "hidden text page 1" in got[3] and "hidden text page 2" in got[4]
    assert toc(r.output_path) == [
        [1, "b", 1], [2, "B entry 3", 1],
        [1, "a", 2], [2, "A entry 1", 2], [3, "A entry 2", 3],
        [1, "c", 4], [2, "Scan 1", 4], [2, "Scan 2", 5],
    ]
    assert r.bookmarks == 8
    assert (pymupdf.LINK_URI, None, "https://example.test/") in links(r.output_path, 1)   # A page 1's web link
    doc = pymupdf.open(r.output_path)
    assert doc.metadata["title"] == "B" and doc.metadata["producer"] == pdfops.PRODUCER
    doc.close()
    assert {p: fx.sha1(p) for p in (a, b, c)} == before
    assert no_part_files(tmp_path)


def test_merge_without_bookmarks_still_names_the_files(tmp_path):
    a = fx.text_pdf(str(tmp_path / "a.pdf"), pages=2, title="A")
    b = fx.text_pdf(str(tmp_path / "b.pdf"), pages=2, title="B")
    r = pdfops.merge_pdfs([(a, ""), (b, "")], pdfops.MergeOptions(keep_bookmarks=False))
    assert toc(r.output_path) == [[1, "a", 1], [1, "b", 3]]


def test_merge_skips_a_locked_or_broken_file_and_reports_it(tmp_path):
    a = fx.text_pdf(str(tmp_path / "a.pdf"), pages=2, title="A")
    locked = fx.encrypted_pdf(str(tmp_path / "locked.pdf"))
    broken = fx.not_a_pdf(str(tmp_path / "broken.pdf"))
    empty = fx.zero_page_pdf(str(tmp_path / "zero.pdf"))
    failed = []
    r = pdfops.merge_pdfs([(a, ""), (locked, ""), (broken, ""), (empty, "")], pdfops.MergeOptions(),
                          file_failed=lambda p, e: failed.append((os.path.basename(p), type(e).__name__)))
    assert r.files == 1 and r.pages == 2
    assert [n for n, _ in failed] == ["locked.pdf", "broken.pdf", "zero.pdf"]
    assert failed[0][1] == "PasswordRequired" and failed[2][1] == "EmptyDocument"
    assert [n for n, _ in r.skipped] == [locked, broken, empty]


def test_merge_with_nothing_usable_writes_nothing(tmp_path):
    locked = fx.encrypted_pdf(str(tmp_path / "locked.pdf"))
    with pytest.raises(pdfops.NothingToMerge):
        pdfops.merge_pdfs([(locked, "")], pdfops.MergeOptions())
    with pytest.raises(pdfops.NothingToMerge):
        pdfops.merge_pdfs([], pdfops.MergeOptions())
    assert sorted(os.listdir(tmp_path)) == ["locked.pdf"]


def test_merge_bad_range_is_rejected_before_anything_is_written(tmp_path):
    a = fx.text_pdf(str(tmp_path / "a.pdf"), pages=2, title="A")
    b = fx.text_pdf(str(tmp_path / "b.pdf"), pages=2, title="B")
    with pytest.raises(pdfops.InvalidPageRange) as info:
        pdfops.merge_pdfs([(a, ""), (b, "1-9")], pdfops.MergeOptions())
    assert info.value.token == "1-9" and info.value.pages == 2
    assert sorted(os.listdir(tmp_path)) == ["a.pdf", "b.pdf"]


def test_merge_owner_password_only_is_fine(tmp_path):
    a = fx.encrypted_pdf(str(tmp_path / "owner.pdf"), user=False)
    b = fx.text_pdf(str(tmp_path / "b.pdf"), pages=1, title="B")
    r = pdfops.merge_pdfs([(a, ""), (b, "")], pdfops.MergeOptions())
    assert r.pages == 3
    doc = pymupdf.open(r.output_path)
    assert not doc.needs_pass and doc.metadata["encryption"] is None
    doc.close()


def test_merge_output_name_is_never_reused(tmp_path):
    a = fx.text_pdf(str(tmp_path / "a.pdf"), pages=1, title="A")
    b = fx.text_pdf(str(tmp_path / "b.pdf"), pages=1, title="B")
    first = pdfops.merge_pdfs([(a, ""), (b, "")], pdfops.MergeOptions()).output_path
    second = pdfops.merge_pdfs([(a, ""), (b, "")], pdfops.MergeOptions()).output_path
    os.mkdir(str(tmp_path / "a_merged(3).pdf"))                     # a folder takes the next name
    third = pdfops.merge_pdfs([(a, ""), (b, "")], pdfops.MergeOptions()).output_path
    assert os.path.basename(first) == "a_merged.pdf"
    assert os.path.basename(second) == "a_merged(2).pdf"
    assert os.path.basename(third) == "a_merged(4).pdf"
    assert pdfops.output_path_for(a, "_merged") == str(tmp_path / "a_merged(5).pdf")


def test_merge_explicit_output_path(tmp_path):
    a = fx.text_pdf(str(tmp_path / "a.pdf"), pages=1, title="A")
    b = fx.text_pdf(str(tmp_path / "b.pdf"), pages=1, title="B")
    out = str(tmp_path / "sub" / "joined.pdf")
    os.mkdir(str(tmp_path / "sub"))
    r = pdfops.merge_pdfs([(a, ""), (b, "")], pdfops.MergeOptions(), output_path=out)
    assert r.output_path == out and pages_of(out) == 2


def test_merge_cancel_writes_nothing(tmp_path):
    a = fx.text_pdf(str(tmp_path / "a.pdf"), pages=3, title="A")
    b = fx.text_pdf(str(tmp_path / "b.pdf"), pages=3, title="B")
    cancel = threading.Event()

    def progress(done, total):
        cancel.set()

    with pytest.raises(pdfops.Cancelled):
        pdfops.merge_pdfs([(a, ""), (b, "")], pdfops.MergeOptions(), progress=progress, cancel=cancel)
    assert sorted(os.listdir(tmp_path)) == ["a.pdf", "b.pdf"]


def test_merge_odd_file_names_and_folders(tmp_path):
    sub = tmp_path / "nested" / "deeper"
    sub.mkdir(parents=True)
    names = ["한글 보고서.pdf", "日本語 #2 & 50%.PDF", "plain.pdf"]
    paths = [fx.text_pdf(str(sub / n), pages=1, title=n[:3]) for n in names]
    found = pdfops.collect_pdfs([str(tmp_path)])
    assert [os.path.basename(p) for p in found] == sorted(names)   # folder walk is sorted
    r = pdfops.merge_pdfs([(p, "") for p in paths], pdfops.MergeOptions())
    assert r.pages == 3 and os.path.basename(r.output_path) == "한글 보고서_merged.pdf"
    assert toc(r.output_path)[0][1] == "한글 보고서"


def test_collect_pdfs_keeps_order_and_drops_duplicates(tmp_path):
    a = fx.text_pdf(str(tmp_path / "a.pdf"), pages=1)
    b = fx.text_pdf(str(tmp_path / "b.pdf"), pages=1)
    (tmp_path / "note.txt").write_text("x")
    assert pdfops.collect_pdfs([b, a, b.upper(), str(tmp_path / "note.txt"), str(tmp_path / "missing.pdf")]) == [b, a]


# ------------------------------------------------------------------ shrinking
def test_shrink_keeps_text_bookmarks_links_sizes_and_the_original(tmp_path):
    src = fx.image_pdf(str(tmp_path / "scan.pdf"), pages=2, dpi=300)
    doc = pymupdf.open(src)
    doc[0].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(10, 10, 100, 30), "uri": "https://example.test/"})
    doc.save(str(tmp_path / "scan2.pdf"), garbage=3, deflate=True)
    doc.close()
    src = str(tmp_path / "scan2.pdf")
    h = fx.sha1(src)
    seen = []
    r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions("normal", 200), progress=lambda d, t: seen.append((d, t)))
    assert r.output_path == str(tmp_path / "scan2_small.pdf")
    assert r.size_after < r.size_before * 0.6 and r.rewritten == 2 and r.images == 2 and r.failed_images == 0
    assert seen[-1] == (4, 4) and all(t == 4 for _, t in seen) and [d for d, _ in seen] == sorted(d for d, _ in seen)
    assert texts(r.output_path) == texts(src)
    assert toc(r.output_path) == toc(src)
    assert links(r.output_path, 0) == links(src, 0)
    a, b = pymupdf.open(src), pymupdf.open(r.output_path)
    assert [p.rect for p in a] == [p.rect for p in b]
    for page in b:
        xrefs = {x[0] for x in page.get_images()}
        assert len(xrefs) == 1
        info = b.extract_image(next(iter(xrefs)))
        assert info["ext"] == "jpeg" and info["width"] <= 595 / 72 * 200 + 1      # 200 dpi ceiling
    assert b.metadata["producer"] == pdfops.PRODUCER and b.metadata["title"] == "Scan"
    a.close()
    b.close()
    assert fx.sha1(src) == h
    assert no_part_files(tmp_path)


@pytest.mark.parametrize("quality, dpi", [("normal", 300), ("small", 200), ("minimum", 150)])
def test_shrink_levels_each_make_it_smaller_than_the_last(tmp_path, quality, dpi):
    src = fx.image_pdf(str(tmp_path / "scan.pdf"), pages=1, dpi=300)
    r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions(quality, dpi))
    assert r.size_after < r.size_before
    r2 = pdfops.shrink_pdf(src, pdfops.ShrinkOptions("minimum", 150))
    assert r2.size_after <= r.size_after


def test_shrink_gray_stays_gray_and_cmyk_becomes_rgb(tmp_path):
    gray = fx.image_pdf(str(tmp_path / "gray.pdf"), pages=1, dpi=200, mode="L")
    cmyk = fx.image_pdf(str(tmp_path / "cmyk.pdf"), pages=1, dpi=200, mode="CMYK")
    for src in (gray, cmyk):
        r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions("small", 150))
        assert r.rewritten == 1 and r.size_after < r.size_before
        doc = pymupdf.open(r.output_path)
        info = doc.extract_image(doc[0].get_images()[0][0])
        assert info["colorspace"] == (1 if src is gray else 3)
        # the page still looks the same: average colour within a few levels
        a = pymupdf.open(src)[0].get_pixmap(dpi=24, colorspace=pymupdf.csRGB)
        b = doc[0].get_pixmap(dpi=24, colorspace=pymupdf.csRGB)
        assert abs(sum(a.samples) / len(a.samples) - sum(b.samples) / len(b.samples)) < 8
        doc.close()


def test_shrink_leaves_bitonal_and_masked_images_alone(tmp_path):
    bit = fx.bitonal_pdf(str(tmp_path / "bit.pdf"))
    masked = fx.image_pdf(str(tmp_path / "masked.pdf"), pages=1, dpi=100, smask=True)
    for src in (bit, masked):
        try:
            r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions("minimum", 150))
        except pdfops.NotSmaller:
            continue                                   # nothing to gain, nothing written
        assert r.rewritten == 0
    assert not os.path.exists(str(tmp_path / "bit_small.pdf"))
    doc = pymupdf.open(masked)
    assert doc.xref_get_key(doc[0].get_images()[0][0], "SMask")[0] == "xref"
    doc.close()


def test_shrink_lossless_images_and_shared_images(tmp_path):
    png = fx.image_pdf(str(tmp_path / "png.pdf"), pages=1, dpi=150, fmt="png")
    shared = fx.image_pdf(str(tmp_path / "shared.pdf"), pages=3, dpi=200, shared=True)
    r = pdfops.shrink_pdf(png, pdfops.ShrinkOptions())
    assert r.rewritten == 1 and r.size_after < r.size_before * 0.5
    r = pdfops.shrink_pdf(shared, pdfops.ShrinkOptions())
    assert r.images == 1 and r.rewritten == 1
    doc = pymupdf.open(r.output_path)
    xrefs = [{x[0] for x in page.get_images()} for page in doc]
    assert xrefs[0] == xrefs[1] == xrefs[2] and len(xrefs[0]) == 1     # still one object, three uses
    doc.close()


def test_shrink_rotated_pages_keep_their_rotation(tmp_path):
    for rot in (90, 180, 270):
        src = fx.image_pdf(str(tmp_path / f"rot{rot}.pdf"), pages=1, dpi=200, rotate=rot)
        r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions())
        a, b = pymupdf.open(src), pymupdf.open(r.output_path)
        assert b[0].rotation == rot and b[0].rect == a[0].rect and r.rewritten == 1
        a.close()
        b.close()


def test_shrink_never_writes_a_bigger_file(tmp_path):
    src = fx.image_pdf(str(tmp_path / "scan.pdf"), pages=1, dpi=200)
    once = pdfops.shrink_pdf(src, pdfops.ShrinkOptions("minimum", 150)).output_path
    with pytest.raises(pdfops.NotSmaller) as info:
        pdfops.shrink_pdf(once, pdfops.ShrinkOptions("minimum", 150))      # nothing left to gain
    assert info.value.before == os.path.getsize(once)
    assert not os.path.exists(str(tmp_path / "scan_small_small.pdf"))


def test_shrink_too_large_image_is_kept(tmp_path, monkeypatch):
    src = fx.image_pdf(str(tmp_path / "scan.pdf"), pages=1, dpi=200)
    monkeypatch.setattr(pdfops, "MAX_PIXELS", 1000)
    with pytest.raises(pdfops.NotSmaller):
        pdfops.shrink_pdf(src, pdfops.ShrinkOptions())


def test_shrink_image_failure_keeps_that_image_and_finishes(tmp_path, monkeypatch):
    src = fx.image_pdf(str(tmp_path / "scan.pdf"), pages=2, dpi=200)
    real = pdfops._rewrite_image
    calls = []

    def flaky(doc, xref, use, opts):
        calls.append(xref)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return real(doc, xref, use, opts)

    monkeypatch.setattr(pdfops, "_rewrite_image", flaky)
    failed = []
    r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions(), image_failed=lambda x, e: failed.append(x))
    assert r.failed_images == 1 and r.rewritten == 1 and failed == [calls[0]]
    assert pages_of(r.output_path) == 2


def test_shrink_cancel_writes_nothing(tmp_path):
    src = fx.image_pdf(str(tmp_path / "scan.pdf"), pages=2, dpi=200)
    cancel = threading.Event()
    with pytest.raises(pdfops.Cancelled):
        pdfops.shrink_pdf(src, pdfops.ShrinkOptions(), progress=lambda d, t: cancel.set(), cancel=cancel)
    assert sorted(os.listdir(tmp_path)) == ["scan.pdf"]


def test_shrink_rejects_locked_empty_and_broken_files(tmp_path):
    with pytest.raises(pdfops.PasswordRequired):
        pdfops.shrink_pdf(fx.encrypted_pdf(str(tmp_path / "locked.pdf")), pdfops.ShrinkOptions())
    with pytest.raises(pdfops.EmptyDocument):
        pdfops.shrink_pdf(fx.zero_page_pdf(str(tmp_path / "zero.pdf")), pdfops.ShrinkOptions())
    with pytest.raises(Exception):
        pdfops.shrink_pdf(fx.not_a_pdf(str(tmp_path / "text.pdf")), pdfops.ShrinkOptions())
    with pytest.raises(Exception):
        pdfops.shrink_pdf(fx.empty_file(str(tmp_path / "nothing.pdf")), pdfops.ShrinkOptions())
    assert not [n for n in os.listdir(tmp_path) if "_small" in n]


def test_shrink_truncated_file_is_repaired_and_processed(tmp_path):
    src = fx.truncated_pdf(str(tmp_path / "cut.pdf"))
    r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions())
    assert os.path.exists(r.output_path) and pages_of(r.output_path) >= 1


def test_shrink_read_only_original_stays_read_only(tmp_path):
    import stat

    src = fx.image_pdf(str(tmp_path / "ro.pdf"), pages=1, dpi=150)
    os.chmod(src, stat.S_IREAD)
    try:
        h = fx.sha1(src)
        r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions())
        assert r.size_after < r.size_before and fx.sha1(src) == h
        assert not os.access(src, os.W_OK)
    finally:
        os.chmod(src, stat.S_IWRITE | stat.S_IREAD)


def test_shrink_tiny_and_huge_pages(tmp_path):
    tiny = fx.image_pdf(str(tmp_path / "tiny.pdf"), pages=1, dpi=300, page_size=(3, 3))
    poster = fx.image_pdf(str(tmp_path / "poster.pdf"), pages=1, dpi=60, page_size=(2384, 3370))   # A0
    try:
        pdfops.shrink_pdf(tiny, pdfops.ShrinkOptions())
    except pdfops.NotSmaller:
        pass
    r = pdfops.shrink_pdf(poster, pdfops.ShrinkOptions("small", 150))
    b = pymupdf.open(r.output_path)
    assert b[0].rect.width == 2384 and r.size_after < r.size_before
    b.close()


def test_estimate_is_in_the_right_ballpark(tmp_path):
    src = fx.image_pdf(str(tmp_path / "scan.pdf"), pages=5, dpi=300)
    before, after = pdfops.estimate_shrink(src, pdfops.ShrinkOptions("normal", 200))
    real = pdfops.shrink_pdf(src, pdfops.ShrinkOptions("normal", 200))
    assert before == real.size_before
    assert 0.5 < after / real.size_after < 2.0
    text = fx.text_pdf(str(tmp_path / "t.pdf"))
    b, a = pdfops.estimate_shrink(text, pdfops.ShrinkOptions())
    assert a == b


def test_options_are_validated():
    assert pdfops.ShrinkOptions("weird", 999).validated() == pdfops.ShrinkOptions("normal", 300)
    assert pdfops.ShrinkOptions("small", "200").validated() == pdfops.ShrinkOptions("small", 200)
    assert pdfops.ShrinkOptions("small", True).validated().dpi == pdfops.DEFAULT_DPI
    assert pdfops.ShrinkOptions("small", -5).validated().dpi == pdfops.DEFAULT_DPI
    assert pdfops.MergeOptions(keep_bookmarks="yes").validated().keep_bookmarks is True


def test_sample_files_round_trip():
    """The shipped samples: merge all three, shrink the scan."""
    import shutil
    import tempfile

    work = tempfile.mkdtemp(prefix="pms_samples_")
    try:
        paths = [shutil.copy(os.path.join(SAMPLES, n), work) for n in
                 ("sample_report_a.pdf", "sample_report_b.pdf", "sample_scan.pdf")]
        r = pdfops.merge_pdfs([(p, "") for p in paths], pdfops.MergeOptions())
        assert r.pages == 13 and r.bookmarks == 16      # 3 file entries + 6 + 4 + 3
        s = pdfops.shrink_pdf(paths[2], pdfops.ShrinkOptions())
        assert s.size_after < s.size_before * 0.5 and texts(s.output_path) == texts(paths[2])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_human_size():
    assert pdfops.human_size(512) == "512 B"
    assert pdfops.human_size(2048) == "2 KB"
    assert pdfops.human_size(2.5 * 1024 * 1024) == "2.5 MB"
    assert pdfops.human_size(3 * 1024 ** 3) == "3.0 GB"
