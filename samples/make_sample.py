"""Make the sample PDFs for trying the tool (and for the screenshots).

    python samples/make_sample.py

Everything is drawn here (text written for these samples, Windows fonts),
so the files carry no copyright.

  sample_report_a.pdf   6 text pages, 2-level bookmarks, internal and web links
  sample_report_b.pdf   4 text pages, bookmarks
  sample_scan.pdf       3 scanned-looking pages: full-page 300 dpi JPEG images
                        with a text layer behind them, so shrinking has
                        something to shrink and text search still works
"""
from __future__ import annotations

import io
import os
import random

import pymupdf
from PIL import Image, ImageChops, ImageDraw, ImageFont

random.seed(11)
HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = "C:/Windows/Fonts/"
PAPER = (246, 242, 232)

PARA = [
    "PDF files pile up: a contract scanned in three parts, a report whose chapters came as separate downloads, "
    "slides exported one section at a time. Joining them should not mean uploading them anywhere.",
    "This sample is text, so a viewer can search it. The merged file keeps that text layer, the bookmarks of "
    "every part, and the links on the pages.",
    "A scanned book is the other common case: each page is one big image. Re-encoding those images is what "
    "makes the file smaller, while the hidden text layer and the bookmarks stay where they are.",
    "The numbers in the headings are there so the order of the merged pages can be checked at a glance.",
]


def text_doc(title: str, chapters: int, pages_per: int, web_link: bool) -> pymupdf.Document:
    doc = pymupdf.open()
    toc = []
    page_no = 0
    for c in range(1, chapters + 1):
        for s in range(1, pages_per + 1):
            page = doc.new_page(width=595, height=842)
            page_no += 1
            head = f"{title} - Chapter {c}" + (f", section {s}" if pages_per > 1 else "")
            page.insert_text((60, 70), head, fontsize=18, fontname="helv")
            y = 110
            for k in range(3):
                text = PARA[(c + s + k) % len(PARA)]
                rect = pymupdf.Rect(60, y, 535, y + 120)
                page.insert_textbox(rect, text, fontsize=11, fontname="helv", lineheight=1.4)
                y += 130
            page.insert_text((290, 800), str(page_no), fontsize=10, fontname="helv")
            if s == 1:
                toc.append([1, f"Chapter {c}", page_no])
            else:
                toc.append([2, f"Section {c}.{s}", page_no])
            if page_no > 1:
                # an internal link back to the first page
                r = pymupdf.Rect(60, 760, 220, 780)
                page.insert_text((60, 775), "Back to page 1", fontsize=10, fontname="helv", color=(0, 0, 0.8))
                page.insert_link({"kind": pymupdf.LINK_GOTO, "from": r, "page": 0})
            if web_link and page_no == 1:
                r = pymupdf.Rect(60, 760, 300, 780)
                page.insert_text((60, 775), "Chaekgalpi tools on the web", fontsize=10, fontname="helv",
                                 color=(0, 0, 0.8))
                page.insert_link({"kind": pymupdf.LINK_URI, "from": r, "uri": "https://chaekgalpi.co.kr/tools"})
    doc.set_toc(toc)
    doc.set_metadata({"title": title, "author": "pdf-merge-split sample"})
    return doc


def scan_page(num: int, dpi: int = 300) -> tuple[bytes, str]:
    """A page image that looks scanned (paper tint, noise, a shadow) and the text drawn on it."""
    w, h = int(8.27 * dpi), int(11.69 * dpi)
    im = Image.new("RGB", (w, h), PAPER)
    noise = Image.effect_noise((w, h), 8).convert("RGB")
    im = Image.blend(im, ImageChops.multiply(im, noise), 0.10)
    d = ImageDraw.Draw(im)
    body = ImageFont.truetype(FONTS + "times.ttf", int(dpi * 0.16))
    head = ImageFont.truetype(FONTS + "arialbd.ttf", int(dpi * 0.24))
    mx, y = int(w * 0.12), int(h * 0.08)
    lines = [f"Scanned page {num}"]
    d.text((mx, y), lines[0], font=head, fill=(40, 38, 35))
    y += int(dpi * 0.5)
    words = (" ".join(PARA) + " ").split()
    k = (num * 37) % len(words)
    while y < h * 0.9:
        line, cur = "", ""
        while k < len(words) * 3:
            wd = words[k % len(words)]
            if body.getlength(cur + " " + wd) > w - 2 * mx:
                break
            cur = (cur + " " + wd).strip()
            k += 1
        line = cur
        d.text((mx, y), line, font=body, fill=(45, 42, 38))
        lines.append(line)
        y += int(dpi * 0.26)
    # a gutter shadow on the left and a slight dark edge: typical of a flatbed scan
    band = Image.new("L", (w, 1))
    px = band.load()
    for x in range(w):
        px[x, 0] = int(255 * (1 - 0.35 * max(0.0, 1 - x / (w * 0.08))))
    im = ImageChops.multiply(im, Image.merge("RGB", (band.resize((w, h)),) * 3))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return buf.getvalue(), "\n".join(lines)


def scan_doc(pages: int) -> pymupdf.Document:
    doc = pymupdf.open()
    toc = []
    for i in range(1, pages + 1):
        data, text = scan_page(i)
        page = doc.new_page(width=595, height=842)
        # the invisible text layer first, then the image over it (as OCR software does)
        page.insert_textbox(pymupdf.Rect(60, 60, 535, 800), text, fontsize=9, fontname="helv",
                            render_mode=3)
        page.insert_image(page.rect, stream=data)
        toc.append([1, f"Scanned page {i}", i])
    doc.set_toc(toc)
    doc.set_metadata({"title": "Sample scan", "author": "pdf-merge-split sample"})
    return doc


def main() -> None:
    for name, doc in (("sample_report_a.pdf", text_doc("Report A", 3, 2, True)),
                      ("sample_report_b.pdf", text_doc("Report B", 2, 2, False)),
                      ("sample_scan.pdf", scan_doc(3))):
        out = os.path.join(HERE, name)
        doc.save(out, garbage=3, deflate=True)
        print(out, len(doc), "pages", os.path.getsize(out), "bytes")
        doc.close()


if __name__ == "__main__":
    main()
