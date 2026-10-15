"""PDFs built on the fly for the tests. Nothing here is a user's file."""
from __future__ import annotations

import io
import os

import pymupdf
from PIL import Image, ImageDraw

A4 = (595, 842)


def _jpeg(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def picture(w: int, h: int, mode: str = "RGB", seed: int = 1) -> Image.Image:
    """A photo-like image (gradients, shapes, noise) that JPEG can shrink."""
    base = Image.effect_noise((w, h), 40).convert("L")
    img = Image.merge("RGB", (base, base.point(lambda v: 255 - v), base.rotate(180)))
    d = ImageDraw.Draw(img)
    for k in range(6):
        x = (k * 97 * seed) % max(w - 40, 1)
        y = (k * 61 * seed) % max(h - 40, 1)
        d.ellipse((x, y, x + w // 3, y + h // 3), fill=((k * 40) % 255, (k * 90) % 255, (k * 150) % 255))
    if mode != "RGB":
        img = img.convert(mode)
    return img


def text_pdf(path: str, pages: int = 3, toc: bool = True, links: bool = True, title: str = "Text") -> str:
    doc = pymupdf.open()
    entries = []
    for i in range(pages):
        page = doc.new_page(width=A4[0], height=A4[1])
        page.insert_text((60, 70), f"{title} page {i + 1}", fontsize=18, fontname="helv")
        page.insert_text((60, 110), f"Searchable words {title} {i + 1} alpha beta gamma", fontsize=11, fontname="helv")
        entries.append([1 if i % 2 == 0 else 2, f"{title} entry {i + 1}", i + 1])
        if links and i > 0:
            page.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(60, 700, 200, 720), "page": 0})
        if links and i == 0:
            page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(60, 740, 200, 760),
                              "uri": "https://example.test/"})
    if toc:
        doc.set_toc(entries)
    doc.set_metadata({"title": title, "author": "tests"})
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


def image_pdf(path: str, pages: int = 2, dpi: int = 300, mode: str = "RGB", fmt: str = "jpeg",
              page_size: tuple[float, float] = A4, rotate: int = 0, text_layer: bool = True,
              shared: bool = False, smask: bool = False) -> str:
    """Scanned-looking pages: one image covering the page at ``dpi``."""
    w, h = int(page_size[0] / 72 * dpi), int(page_size[1] / 72 * dpi)
    doc = pymupdf.open()
    xref = 0
    for i in range(pages):
        page = doc.new_page(width=page_size[0], height=page_size[1])
        if text_layer:
            page.insert_text((40, 60), f"hidden text page {i + 1} lorem", fontsize=9, fontname="helv", render_mode=3)
        if shared and xref:
            page.insert_image(page.rect, xref=xref)
        else:
            img = picture(w, h, mode, seed=i + 1)
            if smask:
                rgba = img.convert("RGBA")
                alpha = Image.linear_gradient("L").resize((w, h))
                rgba.putalpha(alpha)
                stream = _png(rgba)
            elif fmt == "jpeg" and mode in ("RGB", "L", "CMYK"):
                stream = _jpeg(img)
            else:
                stream = _png(img)
            xref = page.insert_image(page.rect, stream=stream)
        if rotate:
            page.set_rotation(rotate)
    doc.set_toc([[1, f"Scan {i + 1}", i + 1] for i in range(pages)])
    doc.set_metadata({"title": "Scan", "author": "tests"})
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


def bitonal_pdf(path: str, pages: int = 1, dpi: int = 300) -> str:
    """1-bit pages (a fax-like scan): JPEG would only make these bigger."""
    w, h = int(A4[0] / 72 * dpi), int(A4[1] / 72 * dpi)
    doc = pymupdf.open()
    for i in range(pages):
        img = Image.new("1", (w, h), 1)
        d = ImageDraw.Draw(img)
        for k in range(0, h, 40):
            d.line((100, k, w - 100, k + 20), fill=0, width=3)
        page = doc.new_page(width=A4[0], height=A4[1])
        page.insert_image(page.rect, stream=_png(img))
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


def zero_page_pdf(path: str) -> str:
    """PyMuPDF cannot write a page-less file, so it is written by hand."""
    body = (b"%PDF-1.4\n"
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
            b"2 0 obj << /Type /Pages /Kids [] /Count 0 >> endobj\n"
            b"trailer << /Root 1 0 R >>\n%%EOF\n")
    with open(path, "wb") as f:
        f.write(body)
    return path


def encrypted_pdf(path: str, user_pw: str = "secret", owner_pw: str = "owner", user: bool = True) -> str:
    src = text_pdf(path + ".tmp.pdf", pages=2, title="Locked")
    doc = pymupdf.open(src)
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=user_pw if user else "", owner_pw=owner_pw)
    doc.close()
    os.remove(src)
    return path


def truncated_pdf(path: str) -> str:
    src = image_pdf(path + ".tmp.pdf", pages=2, dpi=72)
    data = open(src, "rb").read()
    with open(path, "wb") as f:
        f.write(data[: len(data) * 2 // 3])
    os.remove(src)
    return path


def not_a_pdf(path: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        f.write("this is text, not a PDF\n")
    return path


def empty_file(path: str) -> str:
    open(path, "wb").close()
    return path


def sha1(path: str) -> str:
    import hashlib

    return hashlib.sha1(open(path, "rb").read()).hexdigest()
