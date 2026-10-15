"""Edge cases beyond the basic suite: what a page looks like before and after shrinking
(colour spaces, decode arrays, masks, nested images), memory while shrinking, cancelling
at every stage, odd documents going into a merge, and paths that must never be written."""
from __future__ import annotations

import ctypes
import io
import os
import sys
import threading
import time

import pymupdf
import pytest
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures as fx  # noqa: E402
import pdfops  # noqa: E402


# ------------------------------------------------------------------ helpers
def render(path: str, page: int = 0, dpi: int = 36) -> Image.Image:
    doc = pymupdf.open(path)
    try:
        pix = doc[page].get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()


def mean_abs_diff(a: Image.Image, b: Image.Image) -> float:
    assert a.size == b.size, (a.size, b.size)
    pa, pb = a.tobytes(), b.tobytes()
    return sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa)


def looks_the_same(src: str, out: str, page: int = 0, limit: float = 8.0) -> float:
    """Mean absolute pixel difference of a low-resolution render; JPEG at any of the
    three qualities stays far below ``limit`` unless colours or orientation broke."""
    d = mean_abs_diff(render(src, page), render(out, page))
    assert d < limit, f"page {page} changed too much: mean diff {d:.1f}"
    return d


def texts(path: str) -> list[str]:
    doc = pymupdf.open(path)
    try:
        return [p.get_text() for p in doc]
    finally:
        doc.close()


def xref_image(doc: pymupdf.Document, page: int = 0) -> int:
    return doc[page].get_images()[0][0]


def custom_image_pdf(path: str, img: Image.Image, fmt: str = "PNG", page_size=(300, 400), **save_kw) -> str:
    buf = io.BytesIO()
    img.save(buf, fmt, **save_kw)
    doc = pymupdf.open()
    page = doc.new_page(width=page_size[0], height=page_size[1])
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


# ------------------------------------------------------------------ what the page looks like
@pytest.mark.parametrize("mode, fmt", [("RGB", "JPEG"), ("L", "JPEG"), ("CMYK", "JPEG"), ("RGB", "PNG"),
                                       ("L", "PNG"), ("P", "PNG"), ("I;16", "PNG"), ("RGBA", "PNG"), ("LA", "PNG")])
def test_shrunk_page_still_looks_the_same(tmp_path, mode, fmt):
    img = fx.picture(1500, 2000, "RGB", seed=3)
    if mode == "P":
        img = img.quantize(64)
    elif mode == "I;16":
        img = img.convert("L").point(lambda v: v * 256, "I").convert("I;16")
    else:
        img = img.convert(mode)
    src = custom_image_pdf(str(tmp_path / f"{mode.replace(';', '')}_{fmt}.pdf"), img, fmt)
    try:
        r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions("small", 150))
    except pdfops.NotSmaller:
        return                                             # nothing to gain for this input: allowed
    assert r.size_after < r.size_before
    looks_the_same(src, r.output_path)
    if mode in ("RGBA", "LA"):                             # alpha => SMask => left alone
        assert r.rewritten == 0


def test_inverted_decode_array_keeps_its_look(tmp_path):
    """A gray image stored with /Decode [1 0] shows inverted; after shrinking it must still."""
    src = custom_image_pdf(str(tmp_path / "decode.pdf"), fx.picture(1200, 1600, "L"), "JPEG", quality=90)
    doc = pymupdf.open(src)
    doc.xref_set_key(xref_image(doc), "Decode", "[1 0]")
    doc.save(str(tmp_path / "decode2.pdf"))
    doc.close()
    src = str(tmp_path / "decode2.pdf")
    before = render(src)
    r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions("normal", 150))
    assert r.rewritten == 1
    assert mean_abs_diff(before, render(r.output_path)) < 8


def test_jpeg2000_image_is_handled(tmp_path):
    from PIL import features

    if not features.check("jpg_2000"):
        pytest.skip("Pillow without OpenJPEG")
    src = custom_image_pdf(str(tmp_path / "jpx.pdf"), fx.picture(1200, 1600, "RGB"), "JPEG2000")
    doc = pymupdf.open(src)
    flt = doc.xref_get_key(xref_image(doc), "Filter")[1]
    doc.close()
    try:
        r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions("small", 150))
        looks_the_same(src, r.output_path)
    except pdfops.NotSmaller:
        pass
    assert flt in ("/JPXDecode", "/DCTDecode", "/FlateDecode")   # whatever MuPDF stored, it did not break


def test_image_inside_a_form_xobject_is_found_and_the_page_keeps_its_look(tmp_path):
    inner = fx.image_pdf(str(tmp_path / "inner.pdf"), pages=1, dpi=300)
    src_doc = pymupdf.open(inner)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((40, 40), "around the embedded page", fontsize=12, fontname="helv")
    page.show_pdf_page(pymupdf.Rect(60, 80, 535, 760), src_doc, 0)
    doc.save(str(tmp_path / "nested.pdf"), garbage=3, deflate=True)
    doc.close()
    src_doc.close()
    src = str(tmp_path / "nested.pdf")
    r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions("normal", 150))
    assert r.images == 1 and r.rewritten == 1 and r.size_after < r.size_before * 0.6
    looks_the_same(src, r.output_path)
    assert "around the embedded page" in texts(r.output_path)[0]


def test_one_image_placed_large_and_small_keeps_the_large_placement_sharp(tmp_path):
    img = fx.picture(2400, 3200, "RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    doc = pymupdf.open()
    p1 = doc.new_page(width=595, height=842)
    xref = p1.insert_image(p1.rect, stream=buf.getvalue())            # ~290 dpi
    p2 = doc.new_page(width=595, height=842)
    p2.insert_image(pymupdf.Rect(50, 50, 150, 183), xref=xref)        # the same image as a thumbnail
    doc.save(str(tmp_path / "two.pdf"), garbage=3, deflate=True)
    doc.close()
    src = str(tmp_path / "two.pdf")
    r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions("normal", 200))
    assert r.images == 1 and r.rewritten == 1
    out = pymupdf.open(r.output_path)
    info = out.extract_image(xref_image(out, 0))
    assert 1600 <= info["width"] <= 1700                               # 200 dpi for the full page, not for the thumbnail
    out.close()
    looks_the_same(src, r.output_path, 0)
    looks_the_same(src, r.output_path, 1)


def test_vector_drawings_and_text_next_to_an_image_survive(tmp_path):
    img = fx.picture(1600, 1000, "RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(40, 300, 555, 620), stream=buf.getvalue())
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(40, 40, 555, 280))
    shape.finish(color=(0, 0, 1), fill=(1, 0.9, 0.2), width=3)
    shape.draw_line(pymupdf.Point(40, 700), pymupdf.Point(555, 800))
    shape.finish(color=(1, 0, 0), width=6)
    shape.commit()
    page.insert_text((60, 160), "Vector box with text", fontsize=24, fontname="helv")
    doc.save(str(tmp_path / "mixed.pdf"), garbage=3, deflate=True)
    doc.close()
    src = str(tmp_path / "mixed.pdf")
    r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions("minimum", 150))
    assert r.rewritten == 1
    looks_the_same(src, r.output_path)
    out = pymupdf.open(r.output_path)
    assert len(out[0].get_drawings()) >= 2 and "Vector box with text" in out[0].get_text()
    out.close()


def test_many_tiny_images_are_left_alone_quickly(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    icon = Image.new("RGB", (24, 24), (200, 30, 30))
    ImageDraw.Draw(icon).ellipse((4, 4, 20, 20), fill=(30, 30, 200))
    buf = io.BytesIO()
    icon.save(buf, "PNG")
    for i in range(200):
        x, y = 20 + (i % 20) * 28, 20 + (i // 20) * 28
        page.insert_image(pymupdf.Rect(x, y, x + 24, y + 24), stream=buf.getvalue())
    doc.save(str(tmp_path / "icons.pdf"), garbage=3, deflate=True)
    doc.close()
    t0 = time.perf_counter()
    try:
        r = pdfops.shrink_pdf(str(tmp_path / "icons.pdf"), pdfops.ShrinkOptions())
        assert r.rewritten == 0
    except pdfops.NotSmaller:
        pass
    assert time.perf_counter() - t0 < 10


def test_already_tight_jpeg_is_not_rewritten(tmp_path):
    src = custom_image_pdf(str(tmp_path / "tight.pdf"), fx.picture(1000, 1400, "RGB"), "JPEG", quality=30)
    with pytest.raises(pdfops.NotSmaller):
        pdfops.shrink_pdf(src, pdfops.ShrinkOptions("normal", 300))


def test_a_page_that_cannot_be_inspected_does_not_stop_the_file(tmp_path, monkeypatch):
    src = fx.image_pdf(str(tmp_path / "scan.pdf"), pages=3, dpi=200)
    real = pymupdf.Page.get_image_info
    calls = []

    def flaky(self, *a, **k):
        calls.append(self.number)
        if self.number == 1:
            raise RuntimeError("damaged page")
        return real(self, *a, **k)

    monkeypatch.setattr(pymupdf.Page, "get_image_info", flaky)
    r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions())
    assert r.images == 2 and r.rewritten == 2 and len(texts(r.output_path)) == 3


# ------------------------------------------------------------------ cancelling at every stage
def test_cancel_at_each_progress_step_never_leaves_a_file(tmp_path):
    src = fx.image_pdf(str(tmp_path / "scan.pdf"), pages=3, dpi=150)
    steps = []
    pdfops.shrink_pdf(src, pdfops.ShrinkOptions(), progress=lambda d, t: steps.append(d))
    os.remove(str(tmp_path / "scan_small.pdf"))
    assert len(steps) >= 5
    for stop_at in range(len(steps)):
        cancel = threading.Event()
        seen = []

        def progress(d, t, _n=stop_at):
            seen.append(d)
            if len(seen) - 1 == _n:
                cancel.set()

        if stop_at == len(steps) - 1:
            # the last progress call comes after the save: the file is complete and stays
            r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions(), progress=progress, cancel=cancel)
            assert os.path.exists(r.output_path)
            os.remove(r.output_path)
            continue
        with pytest.raises(pdfops.Cancelled):
            pdfops.shrink_pdf(src, pdfops.ShrinkOptions(), progress=progress, cancel=cancel)
        assert sorted(os.listdir(tmp_path)) == ["scan.pdf"], f"stop at step {stop_at}"


def test_cancel_before_start_writes_nothing(tmp_path):
    a = fx.text_pdf(str(tmp_path / "a.pdf"), pages=2)
    b = fx.text_pdf(str(tmp_path / "b.pdf"), pages=2)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(pdfops.Cancelled):
        pdfops.merge_pdfs([(a, ""), (b, "")], pdfops.MergeOptions(), cancel=cancel)
    with pytest.raises(pdfops.Cancelled):
        pdfops.shrink_pdf(fx.image_pdf(str(tmp_path / "s.pdf"), pages=1, dpi=100), pdfops.ShrinkOptions(), cancel=cancel)
    assert sorted(os.listdir(tmp_path)) == ["a.pdf", "b.pdf", "s.pdf"]


# ------------------------------------------------------------------ memory
class _PMC(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]


def private_mb() -> float:
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    pmc = _PMC()
    pmc.cb = ctypes.sizeof(_PMC)
    ok = psapi.GetProcessMemoryInfo(ctypes.c_void_p(k32.GetCurrentProcess()), ctypes.byref(pmc), pmc.cb)
    assert ok, "GetProcessMemoryInfo failed"
    return pmc.PrivateUsage / (1024 * 1024)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows memory counters")
def test_shrinking_a_300dpi_book_stays_within_memory(tmp_path):
    src = fx.image_pdf(str(tmp_path / "book.pdf"), pages=12, dpi=300)
    base = private_mb()
    assert base > 10, "memory counter reads nonsense"
    peak = [base]
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            peak[0] = max(peak[0], private_mb())
            time.sleep(0.02)

    th = threading.Thread(target=sample, daemon=True)
    th.start()
    t0 = time.perf_counter()
    r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions("normal", 200))
    elapsed = time.perf_counter() - t0
    stop.set()
    th.join()
    growth = peak[0] - base
    print(f"12 pages 300 dpi: {elapsed:.1f}s, memory +{growth:.0f} MB, {r.size_before >> 20} -> {r.size_after >> 20} MB")
    assert r.rewritten == 12 and growth < 600 and elapsed < 60


# ------------------------------------------------------------------ odd inputs to a merge
def test_merge_mixed_page_sizes_rotations_forms_and_annotations(tmp_path):
    doc = pymupdf.open()
    sizes = [(595, 842), (612, 792), (842, 595), (200, 300), (1500, 1500)]
    for i, (w, h) in enumerate(sizes):
        page = doc.new_page(width=w, height=h)
        page.insert_text((30, 40), f"size page {i + 1}", fontsize=14, fontname="helv")
        page.set_rotation((i * 90) % 360)
        page.add_highlight_annot(pymupdf.Rect(30, 25, 150, 45))
        page.add_text_annot(pymupdf.Point(w - 40, 40), f"note {i + 1}")
    w = doc[0].add_widget  # form field on the first page
    widget = pymupdf.Widget()
    widget.field_name = "name"
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.rect = pymupdf.Rect(30, 100, 300, 130)
    widget.field_value = "typed value"
    w(widget)
    doc.save(str(tmp_path / "odd.pdf"), garbage=3, deflate=True)
    doc.close()
    plain = fx.text_pdf(str(tmp_path / "plain.pdf"), pages=1, title="P")
    r = pdfops.merge_pdfs([(str(tmp_path / "odd.pdf"), "5,1-4"), (plain, "")], pdfops.MergeOptions())
    out = pymupdf.open(r.output_path)
    assert len(out) == 6
    assert [(int(p.rect.width), int(p.rect.height)) for p in out][:5] == [(1500, 1500), (595, 842), (792, 612), (842, 595), (300, 200)]
    assert [p.rotation for p in out][:5] == [0, 0, 90, 180, 270]
    assert "size page 5" in out[0].get_text() and "size page 1" in out[1].get_text()
    assert all(len(list(p.annots())) >= 2 for p in list(out)[:5])
    assert [wd.field_value for wd in out[1].widgets()] == ["typed value"]
    out.close()


def test_merge_keeps_cjk_text_with_embedded_fonts(tmp_path):
    fonts = {"한글 본문 첫째 줄": "C:/Windows/Fonts/malgun.ttf", "日本語の行です": "C:/Windows/Fonts/YuGothM.ttc",
             "中文内容测试": "C:/Windows/Fonts/msyh.ttc"}
    if not all(os.path.exists(f) for f in fonts.values()):
        pytest.skip("CJK fonts missing")
    paths = []
    for i, (line, font) in enumerate(fonts.items()):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_font(fontname="F0", fontfile=font)
        page.insert_text((50, 80), line, fontsize=16, fontname="F0")
        p = str(tmp_path / f"cjk{i}.pdf")
        doc.save(p, garbage=3, deflate=True)
        doc.close()
        paths.append(p)
    r = pdfops.merge_pdfs([(p, "") for p in paths], pdfops.MergeOptions())
    got = texts(r.output_path)
    assert "한글 본문 첫째 줄" in got[0] and "日本語の行です" in got[1] and "中文内容测试" in got[2]


def test_merge_a_file_with_itself_and_repeated_pages(tmp_path):
    a = fx.text_pdf(str(tmp_path / "a.pdf"), pages=3, title="A")
    r = pdfops.merge_pdfs([(a, "3,3"), (a, "1-2")], pdfops.MergeOptions())
    got = texts(r.output_path)
    assert len(got) == 4 and "A page 3" in got[0] and "A page 3" in got[1] and "A page 1" in got[2]
    toc = pymupdf.open(r.output_path).get_toc(simple=True)
    assert [e[2] for e in toc] == [1, 1, 3, 3, 4]       # repeated page: the first copy carries the bookmark


def test_merge_deep_bookmarks_with_parents_left_out(tmp_path):
    doc = pymupdf.open()
    for i in range(6):
        page = doc.new_page()
        page.insert_text((50, 50), f"deep {i + 1}", fontname="helv")
    doc.set_toc([[1, "Part", 1], [2, "Chapter", 2], [3, "Section", 3], [4, "Sub", 4], [1, "Part 2", 5], [2, "Ch", 6]])
    doc.save(str(tmp_path / "deep.pdf"))
    doc.close()
    r = pdfops.merge_pdfs([(str(tmp_path / "deep.pdf"), "3-4,6")], pdfops.MergeOptions())
    toc = pymupdf.open(r.output_path).get_toc(simple=True)
    assert toc == [[1, "deep", 1], [2, "Section", 1], [3, "Sub", 2], [3, "Ch", 3]]   # never jumps by more than one


def test_merge_malformed_bookmarks_are_dropped_not_fatal(tmp_path, monkeypatch):
    """PyMuPDF refuses to write such an outline, so a damaged file's reader is faked."""
    a = fx.text_pdf(str(tmp_path / "a.pdf"), pages=2, title="A")
    b = fx.text_pdf(str(tmp_path / "b.pdf"), pages=1, title="B")
    real = pymupdf.Document.get_toc

    def damaged(self, simple=True):
        if os.path.basename(self.name) == "a.pdf":
            return [[1, "ok", 1], [1, "beyond", 9], [1, "zero", 0], [1, "negative", -1], [1, "text", "x"], [7, "deep", 2]]
        return real(self, simple)

    monkeypatch.setattr(pymupdf.Document, "get_toc", damaged)
    r = pdfops.merge_pdfs([(a, ""), (b, "")], pdfops.MergeOptions())
    toc = real(pymupdf.open(r.output_path), True)
    assert toc == [[1, "a", 1], [2, "ok", 1], [3, "deep", 2], [1, "b", 3], [2, "B entry 1", 3]]


def test_merge_first_file_skipped_names_the_output_after_the_first_usable_one(tmp_path):
    locked = fx.encrypted_pdf(str(tmp_path / "locked.pdf"))
    b = fx.text_pdf(str(tmp_path / "b.pdf"), pages=1, title="B")
    c = fx.text_pdf(str(tmp_path / "c.pdf"), pages=1, title="C")
    r = pdfops.merge_pdfs([(locked, ""), (b, ""), (c, "")], pdfops.MergeOptions())
    assert os.path.basename(r.output_path) == "b_merged.pdf" and r.files == 2


def test_merge_fifty_files_is_quick_and_complete(tmp_path):
    paths = [fx.text_pdf(str(tmp_path / f"f{i:02}.pdf"), pages=2, title=f"T{i:02}") for i in range(50)]
    t0 = time.perf_counter()
    r = pdfops.merge_pdfs([(p, "") for p in paths], pdfops.MergeOptions())
    assert r.pages == 100 and r.files == 50 and r.bookmarks == 150
    assert time.perf_counter() - t0 < 30
    got = texts(r.output_path)
    assert "T00 page 1" in got[0] and "T49 page 2" in got[99]


def test_merge_a_thousand_pages(tmp_path):
    doc = pymupdf.open()
    for i in range(1000):
        doc.new_page().insert_text((50, 50), f"p{i + 1}", fontname="helv")
    doc.save(str(tmp_path / "big.pdf"), garbage=3, deflate=True)
    doc.close()
    small = fx.text_pdf(str(tmp_path / "small.pdf"), pages=1)
    t0 = time.perf_counter()
    r = pdfops.merge_pdfs([(str(tmp_path / "big.pdf"), "500-"), (small, "")], pdfops.MergeOptions())
    assert r.pages == 502 and time.perf_counter() - t0 < 60
    got = texts(r.output_path)
    assert "p500" in got[0] and "p1000" in got[500]


# ------------------------------------------------------------------ paths that must never be written
def test_output_may_not_be_an_input(tmp_path):
    a = fx.text_pdf(str(tmp_path / "a.pdf"), pages=1)
    b = fx.text_pdf(str(tmp_path / "b.pdf"), pages=1)
    s = fx.image_pdf(str(tmp_path / "s.pdf"), pages=1, dpi=100)
    before = {p: fx.sha1(p) for p in (a, b, s)}
    with pytest.raises(pdfops.OutputIsInput):
        pdfops.merge_pdfs([(a, ""), (b, "")], pdfops.MergeOptions(), output_path=b.upper())
    with pytest.raises(pdfops.OutputIsInput):
        pdfops.shrink_pdf(s, pdfops.ShrinkOptions(), output_path=os.path.join(str(tmp_path), ".", "s.pdf"))
    assert {p: fx.sha1(p) for p in (a, b, s)} == before


def test_unwritable_output_folder_fails_cleanly(tmp_path):
    a = fx.text_pdf(str(tmp_path / "a.pdf"), pages=1)
    b = fx.text_pdf(str(tmp_path / "b.pdf"), pages=1)
    missing = str(tmp_path / "no_such_dir" / "out.pdf")
    with pytest.raises(Exception):
        pdfops.merge_pdfs([(a, ""), (b, "")], pdfops.MergeOptions(), output_path=missing)
    assert not [n for n in os.listdir(tmp_path) if n.endswith(".part")]


def test_long_file_names_and_unicode_folders(tmp_path):
    folder = tmp_path / "資料 文書" / "스캔 자료"
    folder.mkdir(parents=True)
    stem = "긴 이름 " * 20
    src = fx.image_pdf(str(folder / f"{stem.strip()}.pdf"), pages=1, dpi=150)
    r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions())
    assert os.path.exists(r.output_path) and r.output_path.endswith("_small.pdf")
    m = pdfops.merge_pdfs([(src, ""), (src, "")], pdfops.MergeOptions())
    assert os.path.exists(m.output_path) and m.pages == 2


def test_estimate_while_the_same_file_is_being_shrunk(tmp_path):
    """The GUI estimates in one thread while a job may run in another: both only read."""
    src = fx.image_pdf(str(tmp_path / "scan.pdf"), pages=4, dpi=200)
    results = {}

    def est():
        results["est"] = pdfops.estimate_shrink(src, pdfops.ShrinkOptions("small", 150))

    th = threading.Thread(target=est)
    th.start()
    r = pdfops.shrink_pdf(src, pdfops.ShrinkOptions("small", 150))
    th.join()
    assert results["est"][0] == r.size_before and r.size_after < r.size_before
    assert texts(r.output_path) == texts(src)
