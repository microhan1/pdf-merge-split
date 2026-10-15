"""Random documents, random merges, random shrinks - seeded, so a failure is reproducible.

Each seed builds a few PDFs of mixed kinds (text, scans in several colour modes and
resolutions, rotated pages, bookmarks, links), merges a random subset with random page
specs, and shrinks the image-bearing ones. What must always hold:

* merge: page count = pages selected, every output page carries the text of the input
  page it came from in order, bookmarks point inside the file, originals untouched
* shrink: text identical page by page, bookmarks identical, page sizes and rotations
  identical, never larger, every page still looks the same at low resolution

    PMS_SEEDS=50 python -m pytest tests/test_scenarios.py -q     (default 12 seeds)
    python -m pytest tests/test_scenarios.py -q -k seed7          one seed
"""
from __future__ import annotations

import os
import random
import sys

import pymupdf
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures as fx  # noqa: E402
import pdfops  # noqa: E402
from test_edge_cases import looks_the_same, texts  # noqa: E402

SEEDS = int(os.environ.get("PMS_SEEDS", "12"))


def build_docs(rng: random.Random, folder: str) -> list[tuple[str, int, bool]]:
    """(path, pages, has_images) for 2-5 random documents."""
    docs = []
    for k in range(rng.randint(2, 5)):
        kind = rng.choice(["text", "text", "scan", "scan", "gray", "png", "cmyk", "rot"])
        pages = rng.randint(1, 6)
        name = rng.choice(["doc", "보고서", "資料", "file #%d", "a&b"]).replace("%d", str(k)) + f"_{k}.pdf"
        path = os.path.join(folder, name)
        if kind == "text":
            fx.text_pdf(path, pages=pages, toc=rng.random() < 0.7, links=rng.random() < 0.5, title=f"T{k}")
            docs.append((path, pages, False))
        else:
            mode = {"scan": "RGB", "gray": "L", "png": "RGB", "cmyk": "CMYK", "rot": "RGB"}[kind]
            fx.image_pdf(path, pages=pages, dpi=rng.choice([72, 150, 200, 300]), mode=mode,
                         fmt="png" if kind == "png" else "jpeg", rotate=rng.choice([90, 180, 270]) if kind == "rot" else 0,
                         text_layer=rng.random() < 0.8, shared=rng.random() < 0.3)
            docs.append((path, pages, True))
    return docs


def random_spec(rng: random.Random, pages: int) -> tuple[str, list[int]]:
    """A page spec and the 0-based pages it selects."""
    kind = rng.choice(["all", "range", "list", "open"])
    if kind == "all" or pages == 1:
        return rng.choice(["", "all"]), list(range(pages))
    if kind == "range":
        a = rng.randint(1, pages)
        b = rng.randint(a, pages)
        return f"{a}-{b}", list(range(a - 1, b))
    if kind == "open":
        a = rng.randint(1, pages)
        return f"{a}-", list(range(a - 1, pages))
    picks = sorted(rng.sample(range(1, pages + 1), rng.randint(1, pages)))
    return ", ".join(str(p) for p in picks), [p - 1 for p in picks]


@pytest.mark.parametrize("seed", range(SEEDS), ids=[f"seed{s}" for s in range(SEEDS)])
def test_random_merge_and_shrink(tmp_path, seed):
    rng = random.Random(seed * 7919 + 13)
    folder = str(tmp_path / "in")
    os.mkdir(folder)
    docs = build_docs(rng, folder)
    hashes = {p: fx.sha1(p) for p, _, _ in docs}

    # ---- merge a random subset in random order with random specs
    chosen = rng.sample(docs, rng.randint(1, len(docs)))
    items, expect = [], []
    for path, pages, _ in chosen:
        spec, selected = random_spec(rng, pages)
        items.append((path, spec))
        src_texts = texts(path)
        expect.extend(src_texts[i] for i in selected)
    r = pdfops.merge_pdfs(items, pdfops.MergeOptions(keep_bookmarks=rng.random() < 0.8))
    assert r.pages == len(expect), (items, r)
    got = texts(r.output_path)
    assert got == expect, f"page text order differs for {items}"
    out = pymupdf.open(r.output_path)
    for level, title, page in out.get_toc(simple=True):
        assert 1 <= page <= len(out) and level >= 1
    assert [p.rect for p in out]  # readable
    out.close()

    # ---- shrink every image-bearing document
    for path, pages, has_images in docs:
        if not has_images:
            continue
        opts = pdfops.ShrinkOptions(rng.choice(pdfops.QUALITIES), rng.choice(pdfops.DPIS))
        try:
            s = pdfops.shrink_pdf(path, opts)
        except pdfops.NotSmaller:
            continue
        assert s.size_after < s.size_before
        a, b = pymupdf.open(path), pymupdf.open(s.output_path)
        assert len(a) == len(b)
        assert [p.rect for p in a] == [p.rect for p in b]
        assert [p.rotation for p in a] == [p.rotation for p in b]
        assert a.get_toc(simple=True) == b.get_toc(simple=True)
        assert [p.get_text() for p in a] == [p.get_text() for p in b]
        a.close()
        b.close()
        for i in range(min(pages, 3)):
            looks_the_same(path, s.output_path, i, limit=10.0)

    assert {p: fx.sha1(p) for p, _, _ in docs} == hashes
    assert not [n for n in os.listdir(folder) if n.endswith(".part")]
