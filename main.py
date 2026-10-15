"""Entry point for pdf-merge-split.

    python main.py                                  -> GUI
    python main.py a.pdf b.pdf c.pdf --merge        -> a_merged.pdf
    python main.py a.pdf b.pdf --merge --pages 1-10 --pages all
    python main.py scan.pdf --shrink --quality small --dpi 150   -> scan_small.pdf
"""
from __future__ import annotations

import argparse
import os
import sys

import i18n
import pdfops
from i18n import t
from version import APP_VERSION


def _preselect_lang(argv: list[str]) -> str | None:
    """--lang has to be known before the parser is built, so help is translated."""
    for i, a in enumerate(argv):
        if a == "--lang" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--lang="):
            return a.split("=", 1)[1]
    return None


def _localize_argparse() -> None:
    """argparse's own labels go through gettext; route them to lang files."""
    table = {
        "usage: ": t("cli_usage"),
        "positional arguments": t("cli_positional"),
        "options": t("cli_options"),
        "show this help message and exit": t("cli_help"),
        # error messages (exit code 2); argparse fills in the %-placeholders
        "%(prog)s: error: %(message)s\n": t("cli_err_prefix"),
        "argument %(argument_name)s: %(message)s": t("cli_err_argument"),
        "invalid %(type)s value: %(value)r": t("cli_err_invalid_value"),
        "invalid choice: %(value)r (choose from %(choices)s)": t("cli_err_invalid_choice"),
        "not allowed with argument %s": t("cli_err_not_allowed"),
        "unrecognized arguments: %s": t("cli_err_unrecognized"),
        "expected one argument": t("cli_err_expected_one"),
    }
    argparse._ = lambda s: table.get(s, s)  # type: ignore[attr-defined]


def build_parser() -> argparse.ArgumentParser:
    _localize_argparse()
    p = argparse.ArgumentParser(prog="pdf-merge-split", description=t("cli_desc"))
    p.add_argument("inputs", nargs="*", help=t("cli_inputs"))
    g = p.add_mutually_exclusive_group()
    g.add_argument("--merge", action="store_true", help=t("cli_merge"))
    g.add_argument("--shrink", action="store_true", help=t("cli_shrink"))
    p.add_argument("--pages", action="append", default=[], metavar="RANGE", help=t("cli_pages"))
    p.add_argument("--no-bookmarks", dest="keep_bookmarks", action="store_false", default=True,
                   help=t("cli_no_bookmarks"))
    p.add_argument("--quality", choices=pdfops.QUALITIES, default=pdfops.DEFAULT_QUALITY, help=t("cli_quality"))
    p.add_argument("--dpi", type=int, choices=pdfops.DPIS, default=pdfops.DEFAULT_DPI, help=t("cli_dpi"))
    p.add_argument("-o", "--output", metavar="FILE", help=t("cli_output"))
    p.add_argument("-y", "--yes", action="store_true", help=t("cli_yes"))
    p.add_argument("--lang", choices=i18n.LANGS, help=t("cli_lang"))
    p.add_argument("--gui", action="store_true", help=t("cli_gui"))
    p.add_argument("--version", action="version", version=f"pdf-merge-split {APP_VERSION}", help=t("cli_version"))
    return p


def _progress(done: int, total: int) -> None:
    print("\r" + t("cli_progress", done=done, total=total), end="", flush=True)


def _run_merge(files: list[str], args: argparse.Namespace) -> int:
    specs = list(args.pages) + [""] * (len(files) - len(args.pages))
    items = list(zip(files, specs))
    failures = 0
    print(t("cli_merging", count=len(files)))

    def file_failed(path: str, exc: Exception) -> None:
        nonlocal failures
        failures += 1
        name = os.path.basename(path)
        if isinstance(exc, pdfops.PasswordRequired):
            print(t("err_password", name=name), file=sys.stderr)
        elif isinstance(exc, pdfops.EmptyDocument):
            print(t("err_empty_pdf", name=name), file=sys.stderr)
        else:
            print(t("err_open_failed", name=name), file=sys.stderr)

    try:
        r = pdfops.merge_pdfs(items, pdfops.MergeOptions(keep_bookmarks=args.keep_bookmarks),
                              output_path=args.output, progress=_progress, file_failed=file_failed)
    except pdfops.InvalidPageRange as exc:
        print("\n" + t("err_page_range", token=exc.token, name=os.path.basename(files[0]), pages=exc.pages),
              file=sys.stderr)
        return 2
    except pdfops.NothingToMerge:
        print("\n" + t("err_nothing_to_merge"), file=sys.stderr)
        return 1
    except pdfops.OutputIsInput as exc:
        print("\n" + t("err_output_is_input", name=os.path.basename(str(exc))), file=sys.stderr)
        return 2
    print("\n" + t("log_saved", path=r.output_path))
    print(t("log_merge_summary", files=r.files, pages=r.pages, bookmarks=r.bookmarks))
    print(t("msg_done_merge", files=r.files, pages=r.pages))
    return 1 if failures else 0


def _run_shrink(files: list[str], args: argparse.Namespace) -> int:
    opts = pdfops.ShrinkOptions(quality=args.quality, dpi=args.dpi).validated()
    failures = processed = 0
    before = after = 0
    output = args.output if len(files) == 1 else None
    for idx, path in enumerate(files, 1):
        name = os.path.basename(path)
        try:
            pages, _size = pdfops.inspect_pdf(path)
        except pdfops.PasswordRequired:
            print(t("err_password", name=name), file=sys.stderr)
            failures += 1
            continue
        except pdfops.EmptyDocument:
            print(t("err_empty_pdf", name=name), file=sys.stderr)
            failures += 1
            continue
        except Exception:
            print(t("err_open_failed", name=name), file=sys.stderr)
            failures += 1
            continue
        print(t("cli_processing", index=idx, total=len(files), name=name, pages=pages))
        try:
            r = pdfops.shrink_pdf(path, opts, output_path=output, progress=_progress)
        except pdfops.NotSmaller as exc:
            print("\n" + t("err_not_smaller", name=name, size=pdfops.human_size(exc.before)))
            continue
        except pdfops.OutputIsInput as exc:
            print("\n" + t("err_output_is_input", name=os.path.basename(str(exc))), file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # one bad file must not end the batch
            print("\n" + t("err_file_failed", name=name, error=exc), file=sys.stderr)
            failures += 1
            continue
        print("\n" + t("log_saved", path=r.output_path))
        print(t("log_shrink_summary", before=pdfops.human_size(r.size_before), after=pdfops.human_size(r.size_after),
                percent=round(100 * r.size_after / max(r.size_before, 1)), rewritten=r.rewritten, images=r.images))
        if r.failed_images:
            print(t("log_image_failed", count=r.failed_images))
        processed += 1
        before += r.size_before
        after += r.size_after
    print(t("msg_done_shrink", count=processed, before=pdfops.human_size(before), after=pdfops.human_size(after)))
    return 1 if failures else 0


def run_cli(args: argparse.Namespace) -> int:
    files = pdfops.collect_pdfs(args.inputs)
    missing = [p for p in args.inputs if not os.path.exists(p)]
    for p in missing:
        print(t("err_open_failed", name=p), file=sys.stderr)
    if not files:
        print(t("cli_no_input"), file=sys.stderr)
        return 2
    if not args.merge and not args.shrink:
        print(t("cli_need_mode"), file=sys.stderr)
        return 2
    code = _run_merge(files, args) if args.merge else _run_shrink(files, args)
    return 1 if missing and code == 0 else code


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Windows consoles default to cp949; Chinese/Japanese file names would crash print().
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    i18n.init(_preselect_lang(argv))
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.lang:
        i18n.set_lang(args.lang, persist=False)
    # A windowed exe has no console, so dropping files on it opens the GUI
    # with those files loaded instead of running the CLI into nowhere.
    headless = getattr(sys, "frozen", False) and sys.stdout is None
    if args.gui or headless or not args.inputs:
        import gui

        gui.launch(args.inputs or None)
        return 0
    try:
        return run_cli(args)
    except KeyboardInterrupt:
        print("\n" + t("status_cancelled"))
        return 130


if __name__ == "__main__":
    sys.exit(main())
