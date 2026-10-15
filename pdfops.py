"""Merging and shrinking PDFs for pdf-merge-split.

Two jobs, nothing uploaded anywhere, the inputs never modified:

* merge  - several PDFs (each optionally cut to a page range) become one file.
  Pages are copied as objects, so text, links and annotations stay as they
  were; each file's bookmarks are kept under a top-level entry named after it.
* shrink - the images inside one PDF are re-encoded as JPEG (three quality
  levels) and downsampled to a DPI ceiling. Only the image streams change:
  the text layer, bookmarks, links and page sizes are untouched. An image is
  replaced only when the new stream is smaller than the old one, so a file
  never grows by this.

GUI and CLI share everything in this module.
"""
from __future__ import annotations

import io
import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import pymupdf
from PIL import Image

log = logging.getLogger("pdf_merge_split")

# MuPDF prints its own "MuPDF error: ..." lines for damaged files. Every
# failure is reported here in the user's language, so keep the console clean.
pymupdf.TOOLS.mupdf_display_errors(False)
pymupdf.TOOLS.mupdf_display_warnings(False)

MODES = ("merge", "shrink")
QUALITIES = ("normal", "small", "minimum")
JPEG_QUALITY = {"normal": 80, "small": 65, "minimum": 45}
DPIS = (300, 200, 150)
DEFAULT_QUALITY = "normal"
DEFAULT_DPI = 200
LARGE_PAGE_COUNT = 500
MERGE_SUFFIX = "_merged"
SHRINK_SUFFIX = "_small"
PRODUCER = "pdf-merge-split (PyMuPDF)"

MIN_IMAGE_BYTES = 4096            # a stream this small is not worth re-encoding
MIN_IMAGE_SIDE = 16               # icons and rules are left alone
MAX_PIXELS = 80_000_000           # a decoded image above this stays as it is (memory)
GAIN_RATIO = 0.95                 # replace an image only when it gets at least 5% smaller
PREVIEW_MAX_PIXELS = 4_000_000    # the first-page preview render
ESTIMATE_PAGES = 4                # pages sampled for the size estimate


class Cancelled(Exception):
    """Raised inside a job when the cancel event is set. Nothing is written."""


class PasswordRequired(Exception):
    """The PDF is encrypted with a user password. This tool skips such files."""


class EmptyDocument(Exception):
    """The PDF has no pages."""


class InvalidPageRange(ValueError):
    """A page range such as "1-3, 9" does not fit the document."""

    def __init__(self, token: str, pages: int) -> None:
        super().__init__(token)
        self.token = token
        self.pages = pages


class NothingToMerge(Exception):
    """Every input was skipped, so there is no page to write."""


class NotSmaller(Exception):
    """Shrinking did not make the file smaller; no result is written."""

    def __init__(self, path: str, before: int, after: int) -> None:
        super().__init__(path)
        self.path, self.before, self.after = path, before, after


class OutputIsInput(ValueError):
    """The requested output path is one of the inputs: writing it would destroy the original."""


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _check_output(output_path: Optional[str], inputs: list[str]) -> None:
    if output_path is not None and any(_same_file(output_path, p) for p in inputs):
        raise OutputIsInput(output_path)


@dataclass
class MergeOptions:
    keep_bookmarks: bool = True

    def validated(self) -> "MergeOptions":
        self.keep_bookmarks = bool(self.keep_bookmarks)
        return self


@dataclass
class ShrinkOptions:
    quality: str = DEFAULT_QUALITY
    dpi: int = DEFAULT_DPI

    def validated(self) -> "ShrinkOptions":
        if self.quality not in QUALITIES:
            self.quality = DEFAULT_QUALITY
        try:
            d = int(self.dpi)
        except (TypeError, ValueError):
            d = DEFAULT_DPI
        if isinstance(self.dpi, bool) or d not in DPIS:
            d = min(DPIS, key=lambda x: abs(x - d)) if not isinstance(self.dpi, bool) and d > 0 else DEFAULT_DPI
        self.dpi = d
        return self


@dataclass
class MergeResult:
    output_path: str
    files: int                      # inputs that went in
    pages: int                      # pages written
    bookmarks: int                  # bookmark entries written
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (path, reason) of inputs left out
    elapsed: float = 0.0


@dataclass
class ShrinkResult:
    input_path: str
    output_path: str
    pages: int
    size_before: int
    size_after: int
    images: int                     # image objects in the file
    rewritten: int                  # images replaced by a smaller one
    failed_images: int = 0          # images that raised while being re-encoded (kept as they were)
    elapsed: float = 0.0


# ---------------------------------------------------------------- PDF access

def open_pdf(path: str, password: Optional[str] = None) -> pymupdf.Document:
    """Open a PDF read-only. Raises PasswordRequired if it is encrypted and
    the password is missing or wrong. Never writes to ``path``."""
    doc = pymupdf.open(path)
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            doc.close()
            raise PasswordRequired(path)
    return doc


def inspect_pdf(path: str) -> tuple[int, int]:
    """(page count, file size in bytes) for a file about to be queued. Raises
    PasswordRequired, EmptyDocument, or whatever opening a broken file raises;
    each front end turns those into its own prompts and messages."""
    doc = open_pdf(path)
    try:
        pages = len(doc)
        if pages == 0:
            raise EmptyDocument(path)
        return pages, os.path.getsize(path)
    finally:
        doc.close()


def output_path_for(input_path: str, suffix: str) -> str:
    """<name><suffix>.pdf next to the input; numbered if that name is taken by
    anything at all (a file, or a folder someone happened to name so)."""
    base, _ = os.path.splitext(input_path)
    candidate = f"{base}{suffix}.pdf"
    n = 2
    while os.path.exists(candidate):
        candidate = f"{base}{suffix}({n}).pdf"
        n += 1
    return candidate


def collect_pdfs(paths: list[str]) -> list[str]:
    """Expand folders (recursively) into PDF paths; keep order, drop dupes."""
    seen: set[str] = set()
    result: list[str] = []

    def add(p: str) -> None:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            result.append(os.path.abspath(p))

    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs.sort()
                for name in sorted(files):
                    if name.lower().endswith(".pdf"):
                        add(os.path.join(root, name))
        elif p.lower().endswith(".pdf") and os.path.isfile(p):
            add(p)
    return result


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit in ("B", "KB") else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} GB"


def render_first_page(path: str, max_size: tuple[int, int]) -> Image.Image:
    """Page 1 as shown on screen, fitted into ``max_size``; a white tile when
    the page cannot be drawn. Memory is capped whatever the page size."""
    doc = open_pdf(path)
    try:
        page = doc[0]
        r = page.rect
        if r.width <= 0 or r.height <= 0:
            return Image.new("RGB", (1, 1), "white")
        zoom = min(max_size[0] / r.width, max_size[1] / r.height)
        zoom = min(zoom, math.sqrt(PREVIEW_MAX_PIXELS / (r.width * r.height)))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()


# --------------------------------------------------------------- page ranges

_RANGE = re.compile(r"^(\d*)\s*-\s*(\d*)$")


def parse_pages(spec: str, count: int) -> list[int]:
    """0-based page indexes for a spec like "1-3, 7, 10-" (1-based, inclusive).
    Empty means every page. Raises InvalidPageRange for anything that does not
    fit a document of ``count`` pages."""
    spec = (spec or "").strip()
    if not spec or spec.lower() == "all":
        return list(range(count))
    out: list[int] = []
    for token in re.split(r"[,;]", spec):
        token = re.sub(r"\s+", "", token)
        if not token:
            continue
        m = _RANGE.match(token)
        if token.isdigit():
            a = b = int(token)
        elif m and (m.group(1) or m.group(2)):
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else count
        else:
            raise InvalidPageRange(token, count)
        if a < 1 or b > count or a > b:
            raise InvalidPageRange(token, count)
        out.extend(range(a - 1, b))
    if not out:
        raise InvalidPageRange(spec, count)
    return out


def _runs(indexes: list[int]) -> list[tuple[int, int]]:
    """Consecutive stretches of a page list, so a range is copied in one pass."""
    runs: list[tuple[int, int]] = []
    for i in indexes:
        if runs and runs[-1][1] + 1 == i:
            runs[-1] = (runs[-1][0], i)
        else:
            runs.append((i, i))
    return runs


# ----------------------------------------------------------------- merging

def _save_atomic(out: pymupdf.Document, path: str, **save_kwargs) -> None:
    """Write next to the target, then rename: an interrupted save leaves at
    most a stray .part file, never a truncated result under the real name."""
    folder, name = os.path.split(path)
    tmp = os.path.join(folder, f".{name}.{os.getpid()}.part")
    try:
        out.save(tmp, **save_kwargs)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _file_toc(src: pymupdf.Document, selected: list[int], first_out: int, title: str,
              keep: bool) -> list[list]:
    """Bookmarks of one input, placed under a top-level entry for the file.
    An entry on a page that was not selected is dropped; levels never jump by
    more than one so the outline stays well-formed."""
    entries: list[list] = [[1, title, first_out + 1]]
    if not keep:
        return entries
    try:
        toc = src.get_toc(simple=True)
    except Exception:
        toc = []
    where: dict[int, int] = {}
    for pos, page in enumerate(selected):
        where.setdefault(page, first_out + pos + 1)       # first occurrence of a repeated page
    for entry in toc:
        level, text, page = entry[0], entry[1], entry[2]
        if not isinstance(page, int) or (page - 1) not in where:
            continue
        prev = entries[-1][0]
        entries.append([min(max(int(level), 1) + 1, prev + 1), str(text), where[page - 1]])
    return entries


def merge_pdfs(
    items: list[tuple[str, str]],
    opts: MergeOptions,
    output_path: Optional[str] = None,
    progress: Optional[Callable[[int, int], None]] = None,
    cancel: Optional[threading.Event] = None,
    file_failed: Optional[Callable[[str, Exception], None]] = None,
) -> MergeResult:
    """Join ``items`` - (path, page spec) in order - into one PDF written next
    to the first input. An input that cannot be opened (damaged, encrypted) is
    reported through ``file_failed`` and left out; the rest still merge.
    Raises Cancelled (writing nothing) if ``cancel`` is set, NothingToMerge
    when no input could be used, InvalidPageRange for a bad spec."""
    opts = opts.validated()
    t0 = time.perf_counter()
    if not items:
        raise NothingToMerge()
    _check_output(output_path, [p for p, _ in items])
    # page specs are checked before any work, so a typo costs nothing
    plan: list[tuple[str, list[int], str]] = []
    skipped: list[tuple[str, str]] = []
    for path, spec in items:
        try:
            doc = open_pdf(path)
        except Exception as exc:
            skipped.append((path, _reason(exc)))
            if file_failed:
                file_failed(path, exc)
            continue
        try:
            n = len(doc)
            if n == 0:
                raise EmptyDocument(path)
            selected = parse_pages(spec, n)
        except InvalidPageRange:
            doc.close()
            raise
        except Exception as exc:
            doc.close()
            skipped.append((path, _reason(exc)))
            if file_failed:
                file_failed(path, exc)
            continue
        doc.close()
        plan.append((path, selected, os.path.splitext(os.path.basename(path))[0]))
    if not plan:
        raise NothingToMerge()

    total = sum(len(sel) for _, sel, _ in plan)
    out = pymupdf.open()
    try:
        done = 0
        toc: list[list] = []
        meta: dict = {}
        for path, selected, title in plan:
            if cancel is not None and cancel.is_set():
                raise Cancelled(path)
            src = open_pdf(path)
            try:
                if not meta:
                    meta = {k: v for k, v in (src.metadata or {}).items()
                            if k in ("title", "author", "subject", "keywords", "creator") and v}
                first_out = out.page_count
                for a, b in _runs(selected):
                    if cancel is not None and cancel.is_set():
                        raise Cancelled(path)
                    out.insert_pdf(src, from_page=a, to_page=b)
                    done += b - a + 1
                    if progress:
                        progress(done, total)
                toc.extend(_file_toc(src, selected, first_out, title, opts.keep_bookmarks))
            finally:
                src.close()
        if cancel is not None and cancel.is_set():
            raise Cancelled(plan[0][0])
        meta["producer"] = PRODUCER
        try:
            out.set_metadata(meta)
        except Exception as exc:
            log.info("metadata not set: %s", exc)
        if toc:
            try:
                out.set_toc(toc)
            except Exception as exc:         # a malformed outline must not cost the file
                log.info("bookmarks not written: %s", exc)
                toc = []
        out_path = output_path or output_path_for(plan[0][0], MERGE_SUFFIX)
        if cancel is not None and cancel.is_set():
            raise Cancelled(plan[0][0])
        _save_atomic(out, out_path, garbage=4, deflate=True)
        return MergeResult(out_path, len(plan), out.page_count, len(toc), skipped, time.perf_counter() - t0)
    finally:
        out.close()


def _reason(exc: Exception) -> str:
    if isinstance(exc, PasswordRequired):
        return "password"
    if isinstance(exc, EmptyDocument):
        return "empty"
    return "open"


# ---------------------------------------------------------------- shrinking

@dataclass
class _ImageUse:
    page: int                       # first page that shows the image
    dpi: float = 0.0                # resolution at the LARGEST placement (0 = unknown): the
                                    # pixels it needs are the most any placement needs


def _image_uses(doc: pymupdf.Document, pages: Optional[list[int]] = None,
                cancel: Optional[threading.Event] = None,
                progress: Optional[Callable[[int], None]] = None) -> dict[int, _ImageUse]:
    """Every image xref drawn on ``pages`` (all pages by default) with the DPI
    it has at its largest placement - the lowest DPI over all placements, so an
    image shown full-page on one page and as a thumbnail on another keeps the
    pixels the full page needs. Inline images (xref 0) are not touched."""
    uses: dict[int, _ImageUse] = {}
    for i in (range(len(doc)) if pages is None else pages):
        if cancel is not None and cancel.is_set():
            raise Cancelled(doc.name)
        try:
            infos = doc[i].get_image_info(xrefs=True)
        except Exception:
            infos = []
        for info in infos:
            xref = info.get("xref", 0)
            if not xref:
                continue
            x0, y0, x1, y1 = info["bbox"]
            w_pt, h_pt = abs(x1 - x0), abs(y1 - y0)
            dpi = 0.0
            if w_pt > 0 and h_pt > 0:
                dpi = max(info["width"] * 72.0 / w_pt, info["height"] * 72.0 / h_pt)
            use = uses.get(xref)
            if use is None:
                uses[xref] = _ImageUse(i, dpi)
            elif dpi > 0:
                use.dpi = dpi if use.dpi <= 0 else min(use.dpi, dpi)
        if progress:
            progress(i + 1)
    return uses


def _key(doc: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    try:
        return doc.xref_get_key(xref, key)
    except Exception:
        return "null", "null"


def _eligible(doc: pymupdf.Document, xref: int) -> bool:
    """Images this tool re-encodes: plain colour or gray raster images without
    a transparency mask. 1-bit scans (CCITT/JBIG2) are already compact and
    would only grow as JPEG; masked images would lose their transparency."""
    if _key(doc, xref, "Subtype")[1] != "/Image":
        return False
    if _key(doc, xref, "ImageMask")[1] == "true":
        return False
    for k in ("SMask", "Mask"):
        if _key(doc, xref, k)[0] != "null":
            return False
    bpc = _key(doc, xref, "BitsPerComponent")[1]
    if bpc.isdigit() and int(bpc) == 1:
        return False
    return True


def _decode(doc: pymupdf.Document, xref: int, target: Optional[tuple[int, int]]) -> Optional[Image.Image]:
    """The image as a PIL RGB or L image, or None when it is too large to
    decode within MAX_PIXELS. A plain JPEG stream in RGB or gray is handed to
    PIL straight from the file (its draft mode decodes a big photo small);
    everything else (PNG-like, CMYK, indexed, a /Decode array) goes through
    MuPDF, which applies the colour space and decode array itself."""
    info = doc.extract_image(xref)
    w, h = int(info.get("width", 0)), int(info.get("height", 0))
    if w * h > MAX_PIXELS or w < MIN_IMAGE_SIDE or h < MIN_IMAGE_SIDE:
        return None
    plain = _key(doc, xref, "Decode")[0] == "null"
    if plain and info.get("ext") == "jpeg" and info.get("colorspace") in (1, 3) \
            and info.get("cs-name") in ("DeviceRGB", "DeviceGray"):
        img = Image.open(io.BytesIO(info["image"]))
        if img.mode in ("RGB", "L"):
            if target:
                img.draft(img.mode, target)
            img.load()
            return img
    pix = pymupdf.Pixmap(doc, xref)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.n not in (1, 3):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    if pix.width * pix.height > MAX_PIXELS:
        return None
    return Image.frombytes("RGB" if pix.n == 3 else "L", (pix.width, pix.height), pix.samples)


def _rewrite_image(doc: pymupdf.Document, xref: int, use: _ImageUse, opts: ShrinkOptions) -> Optional[bytes]:
    """A smaller JPEG for the image, or None when it should stay as it is."""
    if not _eligible(doc, xref):
        return None
    try:
        old_len = len(doc.xref_stream_raw(xref))
    except Exception:
        return None
    if old_len < MIN_IMAGE_BYTES:
        return None
    scale = 1.0
    if use.dpi > opts.dpi:
        scale = opts.dpi / use.dpi
    w = int(_key(doc, xref, "Width")[1] or 0)
    h = int(_key(doc, xref, "Height")[1] or 0)
    target = (max(1, round(w * scale)), max(1, round(h * scale))) if scale < 1 and w and h else None
    img = _decode(doc, xref, target)
    if img is None:
        return None
    if img.mode not in ("RGB", "L"):
        img = img.convert("L" if img.mode in ("LA", "I", "I;16", "1") else "RGB")
    if target and (img.width > target[0] or img.height > target[1]):
        img = img.resize(target, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=JPEG_QUALITY[opts.quality], optimize=True)
    data = buf.getvalue()
    if len(data) >= old_len * GAIN_RATIO:
        return None
    return data


def shrink_pdf(
    input_path: str,
    opts: ShrinkOptions,
    output_path: Optional[str] = None,
    progress: Optional[Callable[[int, int], None]] = None,
    cancel: Optional[threading.Event] = None,
    image_failed: Optional[Callable[[int, Exception], None]] = None,
) -> ShrinkResult:
    """Write a smaller copy of one PDF next to it. The input is never modified.
    An image that fails to re-encode stays as it was and is reported through
    ``image_failed``. Raises Cancelled (writing nothing) if ``cancel`` is set,
    NotSmaller (writing nothing) when the result would not be smaller."""
    opts = opts.validated()
    t0 = time.perf_counter()
    _check_output(output_path, [input_path])
    size_before = os.path.getsize(input_path)
    doc = open_pdf(input_path)
    try:
        n = len(doc)
        if n == 0:
            raise EmptyDocument(input_path)
        # progress: the first half walks the pages, the second half the images
        total = 2 * n
        uses = _image_uses(doc, cancel=cancel, progress=lambda p: progress and progress(p, total))
        rewritten = failed = 0
        for k, (xref, use) in enumerate(uses.items(), 1):
            if cancel is not None and cancel.is_set():
                raise Cancelled(input_path)
            try:
                data = _rewrite_image(doc, xref, use, opts)
                if data is not None:
                    doc[use.page].replace_image(xref, stream=data)
                    rewritten += 1
            except Exception as exc:
                failed += 1
                log.warning("%s image %d kept as it was: %s", input_path, xref, exc)
                if image_failed:
                    image_failed(xref, exc)
            if progress:
                progress(n + k * n // len(uses), total)
        if cancel is not None and cancel.is_set():
            raise Cancelled(input_path)
        meta = dict(doc.metadata or {})
        meta["producer"] = PRODUCER
        try:
            doc.set_metadata({k: v for k, v in meta.items() if isinstance(v, str)})
        except Exception as exc:
            log.info("metadata not set: %s", exc)
        out_path = output_path or output_path_for(input_path, SHRINK_SUFFIX)
        if cancel is not None and cancel.is_set():
            raise Cancelled(input_path)
        _save_atomic(doc, out_path, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True)
        if progress:
            progress(total, total)
        size_after = os.path.getsize(out_path)
        if size_after >= size_before:
            try:
                os.remove(out_path)
            except OSError:
                pass
            raise NotSmaller(input_path, size_before, size_after)
        return ShrinkResult(input_path, out_path, n, size_before, size_after, len(uses), rewritten, failed,
                            time.perf_counter() - t0)
    finally:
        doc.close()


def estimate_shrink(input_path: str, opts: ShrinkOptions, cancel: Optional[threading.Event] = None,
                    sample: int = ESTIMATE_PAGES) -> tuple[int, int]:
    """(size now, rough size after shrinking) from the images on a few pages
    spread through the file. Good enough to show "12.4 MB -> about 4 MB" while
    the user picks options; the real number comes from shrink_pdf."""
    opts = opts.validated()
    size = os.path.getsize(input_path)
    doc = open_pdf(input_path)
    try:
        n = len(doc)
        if n == 0:
            return size, size
        pages = sorted({int(i * (n - 1) / max(sample - 1, 1)) for i in range(min(sample, n))})
        uses = _image_uses(doc, pages, cancel)
        old = new = 0
        for xref, use in uses.items():
            if cancel is not None and cancel.is_set():
                raise Cancelled(input_path)
            try:
                before = len(doc.xref_stream_raw(xref))
                data = _rewrite_image(doc, xref, use, opts)
            except Exception:
                continue
            old += before
            new += len(data) if data is not None else before
        saved = (old - new) * (n / len(pages))
        return size, int(max(size * 0.05, size - saved))
    finally:
        doc.close()
