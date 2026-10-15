"""tkinter GUI for pdf-merge-split.

Threads never touch Tk: every worker is a module-level function that gets
plain values (paths, options, a cancel event, a queue) and posts results into
the queue; the Tk side drains it with ``after``. A worker that held the App
or a widget could free Tk objects off the main thread when the window closes
first, and Tcl then aborts the process.
"""
from __future__ import annotations

import dataclasses
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, font as tkfont, messagebox, ttk

from PIL import Image, ImageTk

import i18n
import pdfops
import update_check
from i18n import t
from pdfops import MergeOptions, ShrinkOptions
from version import APP_VERSION

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    _HAS_DND = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_DND = False

PREVIEW_W, PREVIEW_H = 360, 480
WINDOW_SIZE = (1060, 760)            # client area, when the screen has room for it
MIN_SIZE = (940, 700)
FRAME_W, FRAME_H = 16, 40            # window borders and title bar around the client area
OPTIONS_WIDTH = 400                  # the options column (wraplength of its hints)
UPDATE_BG, UPDATE_BORDER = "#eff6ff", "#bfdbfe"   # the new-version notice line
HINT_FG, BAD_FG = "#666", "#c0392b"
POLL_MS = 100                        # how often the Tk thread drains the worker queue
ESTIMATE_DELAY_MS = 350
PROGRESS_STEPS = 1000


def _saved_state(settings: dict) -> tuple[str, MergeOptions, ShrinkOptions]:
    """Mode and options from settings.json, trusting nothing about its types.
    The file sits beside the exe where anyone can edit it; one wrong value
    must not stop the window from opening."""
    mode = settings.get("mode")
    mode = mode if mode in pdfops.MODES else "merge"
    m = settings.get("merge")
    m = m if isinstance(m, dict) else {}
    s = settings.get("shrink")
    s = s if isinstance(s, dict) else {}
    keep = m.get("keep_bookmarks")
    merge = MergeOptions(keep_bookmarks=keep if isinstance(keep, bool) else True).validated()
    q, d = s.get("quality"), s.get("dpi")
    shrink = ShrinkOptions(quality=q if isinstance(q, str) else pdfops.DEFAULT_QUALITY,
                           dpi=d if isinstance(d, int) and not isinstance(d, bool) else pdfops.DEFAULT_DPI).validated()
    return mode, merge, shrink


# ------------------------------------------------------------------ workers
# These run on background threads and hold nothing of the App: only the
# values they are given and the queue they report into.

def _job_worker(mode: str, files: list[str], specs: dict[str, str], merge_opts: MergeOptions,
                shrink_opts: ShrinkOptions, cancel: threading.Event, q: "queue.Queue") -> None:
    try:
        if mode == "merge":
            _merge_job(files, specs, merge_opts, cancel, q)
        else:
            _shrink_job(files, shrink_opts, cancel, q)
    except Exception as exc:  # pragma: no cover - last resort, keeps the window usable
        q.put(("log", "err_file_failed", {"name": os.path.basename(files[0]) if files else "", "error": str(exc)}))
        q.put(("done", mode, None))


def _merge_job(files, specs, opts, cancel, q) -> None:
    first = os.path.basename(files[0])
    items = [(p, specs.get(p, "")) for p in files]

    def progress(done: int, total: int) -> None:
        q.put(("progress", done / max(total, 1), first))

    def file_failed(path: str, exc: Exception) -> None:
        name = os.path.basename(path)
        if isinstance(exc, pdfops.PasswordRequired):
            q.put(("log", "err_password", {"name": name}))
        elif isinstance(exc, pdfops.EmptyDocument):
            q.put(("log", "err_empty_pdf", {"name": name}))
        else:
            q.put(("log", "err_open_failed", {"name": name}))

    try:
        r = pdfops.merge_pdfs(items, opts, progress=progress, cancel=cancel, file_failed=file_failed)
    except pdfops.Cancelled:
        q.put(("cancelled", first))
        return
    except pdfops.InvalidPageRange as exc:
        q.put(("log", "err_page_range", {"token": exc.token, "name": first, "pages": exc.pages}))
        q.put(("done", "merge", None))
        return
    except pdfops.NothingToMerge:
        q.put(("log", "err_nothing_to_merge", {}))
        q.put(("done", "merge", None))
        return
    q.put(("saved", r.output_path))
    q.put(("log", "log_merge_summary", {"files": r.files, "pages": r.pages, "bookmarks": r.bookmarks}))
    q.put(("done", "merge", {"files": r.files, "pages": r.pages, "skipped": len(r.skipped)}))


def _shrink_job(files, opts, cancel, q) -> None:
    count = failed = 0
    before = after = 0
    n = len(files)
    for idx, path in enumerate(files):
        name = os.path.basename(path)

        def progress(done: int, total: int, _i=idx, _n=name) -> None:
            q.put(("progress", (_i + done / max(total, 1)) / n, _n))

        try:
            r = pdfops.shrink_pdf(path, opts, progress=progress, cancel=cancel)
        except pdfops.Cancelled:
            q.put(("cancelled", name))
            return
        except pdfops.NotSmaller as exc:
            q.put(("log", "err_not_smaller", {"name": name, "size": pdfops.human_size(exc.before)}))
            continue
        except pdfops.PasswordRequired:
            q.put(("log", "err_password", {"name": name}))
            failed += 1
            continue
        except pdfops.EmptyDocument:
            q.put(("log", "err_empty_pdf", {"name": name}))
            failed += 1
            continue
        except Exception as exc:  # one bad file must not end the batch
            q.put(("log", "err_file_failed", {"name": name, "error": str(exc)}))
            failed += 1
            continue
        count += 1
        before += r.size_before
        after += r.size_after
        q.put(("saved", r.output_path))
        q.put(("log", "log_shrink_summary", {
            "before": pdfops.human_size(r.size_before), "after": pdfops.human_size(r.size_after),
            "percent": round(100 * r.size_after / max(r.size_before, 1)), "rewritten": r.rewritten, "images": r.images}))
        if r.failed_images:
            q.put(("log", "log_image_failed", {"count": r.failed_images}))
    q.put(("done", "shrink", {"count": count, "before": before, "after": after, "skipped": failed}))


def _preview_worker(path: str, gen: int, q: "queue.Queue") -> None:
    try:
        image = pdfops.render_first_page(path, (PREVIEW_W * 2, PREVIEW_H * 2))
    except Exception:
        image = None
    q.put(("preview", gen, path, image))


def _estimate_worker(paths: list[str], opts: ShrinkOptions, gen: int, live: dict, cancel: threading.Event,
                     q: "queue.Queue") -> None:
    before = after = 0
    for p in paths:
        if live.get("gen") != gen or cancel.is_set():
            return
        try:
            b, a = pdfops.estimate_shrink(p, opts, cancel)
        except pdfops.Cancelled:
            return
        except Exception:
            try:
                b = a = os.path.getsize(p)
            except OSError:
                continue
        before += b
        after += a
    q.put(("estimate", gen, len(paths), before, after))


# ---------------------------------------------------------------------- App

class App:
    def __init__(self, initial_files: list[str] | None = None) -> None:
        self.root = TkinterDnD.Tk() if _HAS_DND else tk.Tk()

        self.files: list[str] = []
        self.page_counts: dict[str, int] = {}
        self.sizes: dict[str, int] = {}
        self.specs: dict[str, str] = {}
        self.outputs: list[str] = []
        self.q: "queue.Queue" = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.preview_gen = 0
        self.estimate_gen = 0
        self._live = {"gen": 0}
        self.estimate_job: str | None = None
        self.preview_file: str | None = None
        self._preview: Image.Image | None = None
        self._photos: list[ImageTk.PhotoImage] = []
        self._texts: list[tuple[tk.Misc, str, str]] = []
        self._status_key = ""
        self._status_kwargs: dict = {}
        self._estimate_kwargs: dict | None = None
        self._estimating = False
        self._drag_index: int | None = None
        self._closing = False
        self._running = False

        settings = i18n.load_settings()
        mode, merge_opts, shrink_opts = _saved_state(settings)
        self._mode = mode
        self.var_pages = tk.StringVar(value="")
        self.var_keep_bookmarks = tk.BooleanVar(value=merge_opts.keep_bookmarks)
        self.var_quality = tk.StringVar(value=shrink_opts.quality)
        self.var_dpi = tk.IntVar(value=shrink_opts.dpi)
        self.var_lang = tk.StringVar(value=i18n.LANG_NAMES[i18n.current_lang()])
        self.update_prefs = update_check.prefs_from_settings(settings)
        self.var_update_check = tk.BooleanVar(value=self.update_prefs.update_check)
        self._update_info: update_check.UpdateInfo | None = None

        self._build()
        self._apply_texts()
        self._fit_to_screen()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(POLL_MS, self._poll)
        # once a day, 1.5 s after the window is up, on a background thread; silent on any failure
        update_check.check_async(self.root, update_check.SLUG, APP_VERSION, self.update_prefs, self._on_update_result)
        if initial_files:
            self.root.after(100, lambda: self.add_paths(initial_files))

    # ------------------------------------------------------------ building
    def _reg(self, widget: tk.Misc, key: str, attr: str = "text") -> tk.Misc:
        self._texts.append((widget, key, attr))
        return widget

    def _build(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)
        # Bold labels name the UI font family explicitly: a bare ("", 10, "bold") let
        # Tk pick a different family for Korean (a serif face) than the plain labels use.
        family = tkfont.nametofont("TkDefaultFont").actual("family")
        bold, title_font = (family, 10, "bold"), (family, 15, "bold")

        # ---- header
        head = ttk.Frame(root, padding=(12, 6, 12, 2))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        self._reg(ttk.Label(head, font=title_font), "app_title").grid(row=0, column=0, sticky="w")
        self._reg(ttk.Label(head), "lbl_language").grid(row=0, column=1, padx=(0, 6))
        self.cmb_lang = ttk.Combobox(head, state="readonly", width=10, textvariable=self.var_lang,
                                     values=[i18n.LANG_NAMES[c] for c in i18n.LANGS])
        self.cmb_lang.grid(row=0, column=2)
        self.cmb_lang.bind("<<ComboboxSelected>>", self._on_lang)
        self.chk_update = self._reg(ttk.Checkbutton(head, variable=self.var_update_check,
                                                    command=self._on_update_toggle), "opt_update_check")
        self.chk_update.grid(row=0, column=3, padx=(14, 0))

        # ---- files
        files = ttk.LabelFrame(root, padding=6)
        self._reg(files, "lbl_files")
        files.grid(row=1, column=0, sticky="ew", padx=12, pady=4)
        files.columnconfigure(0, weight=1)
        self.lbl_drop = ttk.Label(files, anchor="center", relief="groove", padding=3, foreground="#555")
        self._reg(self.lbl_drop, "drop_hint")
        self.lbl_drop.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        self.lst_files = tk.Listbox(files, height=4, activestyle="none", exportselection=False)
        self.lst_files.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(files, command=self.lst_files.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.lst_files.configure(yscrollcommand=sb.set)
        self.lst_files.bind("<<ListboxSelect>>", self._on_file_select)
        self.lst_files.bind("<ButtonPress-1>", self._on_list_press)
        self.lst_files.bind("<B1-Motion>", self._on_list_drag)
        self.lst_files.bind("<ButtonRelease-1>", self._on_list_release)
        btns = ttk.Frame(files)
        btns.grid(row=1, column=2, sticky="ns", padx=(8, 0))
        btns.columnconfigure((0, 1, 2), weight=1)
        self.btn_add = self._reg(ttk.Button(btns, command=self._add_files_dialog), "btn_add_files")
        self.btn_add.grid(row=0, column=0, sticky="ew", padx=(0, 2))
        self.btn_add_dir = self._reg(ttk.Button(btns, command=self._add_folder_dialog), "btn_add_folder")
        self.btn_add_dir.grid(row=0, column=1, sticky="ew", padx=2)
        self.btn_clear = self._reg(ttk.Button(btns, command=self.clear_files), "btn_clear")
        self.btn_clear.grid(row=0, column=2, sticky="ew", padx=(2, 0))
        self.btn_up = self._reg(ttk.Button(btns, command=lambda: self._move_selected(-1)), "btn_up")
        self.btn_up.grid(row=1, column=0, sticky="ew", padx=(0, 2), pady=(4, 0))
        self.btn_down = self._reg(ttk.Button(btns, command=lambda: self._move_selected(1)), "btn_down")
        self.btn_down.grid(row=1, column=1, sticky="ew", padx=2, pady=(4, 0))
        self.btn_remove = self._reg(ttk.Button(btns, command=self.remove_selected), "btn_remove")
        self.btn_remove.grid(row=1, column=2, sticky="ew", padx=(2, 0), pady=(4, 0))
        if _HAS_DND:
            for w in (root, self.lbl_drop, self.lst_files):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)

        # ---- middle: mode tabs (options) | preview
        mid = ttk.Frame(root)
        mid.grid(row=2, column=0, sticky="nsew", padx=12, pady=4)
        mid.columnconfigure(1, weight=1)
        mid.rowconfigure(0, weight=1)

        self.nb = ttk.Notebook(mid, width=OPTIONS_WIDTH)
        self.nb.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        wrap = OPTIONS_WIDTH - 40

        # merge tab
        tab_m = ttk.Frame(self.nb, padding=10)
        self.nb.add(tab_m, text="")
        self._reg(ttk.Label(tab_m), "opt_pages_for").pack(anchor="w")
        self.ent_pages = ttk.Entry(tab_m, textvariable=self.var_pages, width=30)
        self.ent_pages.pack(anchor="w", fill="x", pady=(2, 2))
        self._pages_trace = self.var_pages.trace_add("write", lambda *_: self._on_pages_edit())
        self.lbl_pages_hint = ttk.Label(tab_m, foreground=HINT_FG, wraplength=wrap, justify="left")
        self.lbl_pages_hint.pack(anchor="w", pady=(0, 10))
        self.chk_bookmarks = self._reg(ttk.Checkbutton(tab_m, variable=self.var_keep_bookmarks,
                                                       command=self._save_state), "opt_keep_bookmarks")
        self.chk_bookmarks.pack(anchor="w")
        self._reg(ttk.Label(tab_m, foreground=HINT_FG, wraplength=wrap, justify="left"),
                  "lbl_merge_output").pack(anchor="w", pady=(10, 0))
        self.lbl_merge_summary = ttk.Label(tab_m, font=bold, wraplength=wrap, justify="left")
        self.lbl_merge_summary.pack(anchor="w", pady=(12, 0))

        # shrink tab
        tab_s = ttk.Frame(self.nb, padding=10)
        self.nb.add(tab_s, text="")
        f_q = self._reg(ttk.LabelFrame(tab_s, padding=4), "opt_quality")
        f_q.pack(fill="x", pady=(0, 4))
        for i, qk in enumerate(pdfops.QUALITIES):
            rb = ttk.Radiobutton(f_q, variable=self.var_quality, value=qk, command=self._on_option)
            self._reg(rb, f"quality_{qk}")
            rb.grid(row=i, column=0, sticky="w")
        f_d = self._reg(ttk.LabelFrame(tab_s, padding=4), "opt_dpi")
        f_d.pack(fill="x", pady=(0, 4))
        for i, dpi in enumerate(pdfops.DPIS):
            rb = ttk.Radiobutton(f_d, variable=self.var_dpi, value=dpi, command=self._on_option)
            self._reg(rb, f"dpi_{dpi}")
            rb.grid(row=i, column=0, sticky="w")
        self._reg(ttk.Label(tab_s, foreground=HINT_FG, wraplength=wrap, justify="left"),
                  "lbl_shrink_output").pack(anchor="w")
        self.lbl_estimate = ttk.Label(tab_s, font=bold, wraplength=wrap, justify="left")
        self.lbl_estimate.pack(anchor="w", pady=(12, 0))

        # preview
        prev = self._reg(ttk.LabelFrame(mid, padding=6), "preview_first_page")
        prev.grid(row=0, column=1, sticky="nsew")
        prev.columnconfigure(0, weight=1)
        prev.rowconfigure(0, weight=1)
        self.cv_preview = tk.Canvas(prev, bg="#d9d9d9", highlightthickness=1, highlightbackground="#999")
        self.cv_preview.grid(row=0, column=0, sticky="nsew")
        self.cv_preview.bind("<Configure>", lambda e: self._redraw_preview())
        foot = ttk.Frame(prev)
        foot.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.lbl_file_info = ttk.Label(foot, anchor="w")
        self.lbl_file_info.pack(side="left")
        self.lbl_preview_msg = ttk.Label(foot, anchor="e", foreground="#555")
        self.lbl_preview_msg.pack(side="right")

        # ---- log
        logf = self._reg(ttk.LabelFrame(root, padding=4), "lbl_log")
        logf.grid(row=3, column=0, sticky="ew", padx=12, pady=4)
        logf.columnconfigure(0, weight=1)
        self.txt_log = tk.Text(logf, height=3, state="disabled", wrap="none")
        self.txt_log.grid(row=0, column=0, sticky="ew")
        sb2 = ttk.Scrollbar(logf, command=self.txt_log.yview)
        sb2.grid(row=0, column=1, sticky="ns")
        self.txt_log.configure(yscrollcommand=sb2.set)

        # ---- bottom: fixed-size buttons first (right), the status label takes the rest
        bot = ttk.Frame(root, padding=(12, 4, 12, 6))
        bot.grid(row=4, column=0, sticky="ew")
        bot.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(bot, mode="determinate", maximum=PROGRESS_STEPS)
        self.progress.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 4))
        self.lbl_status = ttk.Label(bot, anchor="w")
        self.lbl_status.grid(row=1, column=0, sticky="ew")
        self.btn_open = self._reg(ttk.Button(bot, command=self.open_result, state="disabled"), "btn_open_result")
        self.btn_open.grid(row=1, column=1, padx=4)
        self.btn_cancel = self._reg(ttk.Button(bot, command=self.cancel, state="disabled"), "btn_cancel")
        self.btn_cancel.grid(row=1, column=2, padx=4)
        self.btn_run = self._reg(ttk.Button(bot, command=self.run), "btn_run")
        self.btn_run.grid(row=1, column=3, padx=(4, 0))

        # ---- one-line "new version" notice at the very bottom (gridded only when there is one)
        self.frm_update = tk.Frame(root, bg=UPDATE_BG, highlightthickness=1, highlightbackground=UPDATE_BORDER)
        self.lbl_update = tk.Label(self.frm_update, bg=UPDATE_BG, anchor="w")
        self.lbl_update.pack(side="left", fill="x", expand=True, padx=(10, 8), pady=4)
        self.btn_update_later = self._reg(ttk.Button(self.frm_update, command=self._update_later), "btn_later")
        self.btn_update_later.pack(side="right", padx=(4, 8), pady=4)
        self.btn_update_open = self._reg(ttk.Button(self.frm_update, command=self._update_open), "btn_update")
        self.btn_update_open.pack(side="right", pady=4)

        self.nb.select(1 if self._mode == "shrink" else 0)
        self._set_status("status_ready")
        self._update_mode_controls()

    def _apply_texts(self) -> None:
        self.root.title(f"{t('app_title')} v{APP_VERSION}")
        for widget, key, attr in self._texts:
            try:
                widget.configure(**{attr: t(key)})
            except tk.TclError:
                pass
        self.nb.tab(0, text=t("tab_merge"))
        self.nb.tab(1, text=t("tab_shrink"))
        self._refresh_list()
        self._update_pages_hint()
        self._update_merge_summary()
        self._update_estimate_label()
        self._update_file_info()
        if self._preview is None:
            self.lbl_preview_msg.configure(text=t("preview_empty") if not self.files else "")
        if self._status_key:
            self._set_status(self._status_key, **self._status_kwargs)
        if self._update_info is not None:
            self._show_update_notice()

    # ------------------------------------------------------------ new version notice
    def _on_update_result(self, info: update_check.UpdateInfo | None, answered: bool) -> None:
        """Tk thread. The day is recorded only when the server answered, so an offline
        start asks again next time."""
        if self._closing:
            return
        if answered:
            self.update_prefs.update_check_last = update_check.today()
            self._save_state()
        if info is not None and self.var_update_check.get():
            self._update_info = info
            self._show_update_notice()

    def _show_update_notice(self) -> None:
        info = self._update_info
        if info is None:
            self.frm_update.grid_remove()
            return
        self.lbl_update.configure(text=t("update_available", latest=info.latest, current=info.current))
        if not self.frm_update.winfo_manager():
            self.frm_update.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 10))

    def _hide_update_notice(self) -> None:
        self._update_info = None
        self.frm_update.grid_remove()

    def _update_open(self) -> None:
        """The tools page, never a download: the site serves the download."""
        info = self._update_info
        page = update_check.page_for(info, i18n.current_lang()) if info is not None else ""
        if page.startswith(update_check.SITE):
            try:
                webbrowser.open(page)
            except Exception:
                pass
        self._hide_update_notice()

    def _update_later(self) -> None:
        if self._update_info is not None:
            self.update_prefs.update_check_skipped = self._update_info.latest  # a newer one is shown again
            self._save_state()
        self._hide_update_notice()

    def _on_update_toggle(self) -> None:
        self.update_prefs.update_check = bool(self.var_update_check.get())
        self._save_state()
        if not self.update_prefs.update_check:
            self._hide_update_notice()

    # ------------------------------------------------------------ helpers
    def mode(self) -> str:
        try:
            return "shrink" if self.nb.index(self.nb.select()) == 1 else "merge"
        except tk.TclError:
            return self._mode

    def merge_options(self) -> MergeOptions:
        return MergeOptions(keep_bookmarks=bool(self.var_keep_bookmarks.get())).validated()

    def shrink_options(self) -> ShrinkOptions:
        return ShrinkOptions(quality=self.var_quality.get(), dpi=int(self.var_dpi.get())).validated()

    def _save_state(self) -> None:
        settings = i18n.load_settings()
        settings["mode"] = self.mode()
        settings["merge"] = dataclasses.asdict(self.merge_options())
        settings["shrink"] = dataclasses.asdict(self.shrink_options())
        settings.update(dataclasses.asdict(self.update_prefs))
        i18n.save_settings(settings)

    def _set_status(self, key: str, **kwargs) -> None:
        self._status_key, self._status_kwargs = key, kwargs
        self.lbl_status.configure(text=t(key, **kwargs))

    def log(self, key: str, **kwargs) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", t(key, **kwargs) + "\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _on_lang(self, _event=None) -> None:
        index = self.cmb_lang.current()
        if index >= 0:
            i18n.set_lang(i18n.LANGS[index])
        self._apply_texts()

    def _fit_to_screen(self) -> None:
        """Open at the default size, or smaller if the screen's work area
        (taskbar excluded) cannot hold it, so the bottom row stays visible."""
        left, top = 0, 0
        right, bottom = self.root.winfo_screenwidth(), self.root.winfo_screenheight() - 48
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                rect = wintypes.RECT()
                if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
                    left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
            except Exception:
                pass
        avail_w, avail_h = right - left - FRAME_W, bottom - top - FRAME_H
        w, h = min(WINDOW_SIZE[0], avail_w), min(WINDOW_SIZE[1], avail_h)
        x = left + max(0, (avail_w - w) // 2)
        y = top + max(0, (avail_h - h) // 2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(min(MIN_SIZE[0], w), min(MIN_SIZE[1], h))

    # ------------------------------------------------------------ mode and options
    def _on_tab_changed(self, _event=None) -> None:
        self._mode = self.mode()
        self._update_mode_controls()
        self._save_state()
        if self._mode == "shrink":
            self._schedule_estimate()

    def _update_mode_controls(self) -> None:
        merging = self.mode() == "merge"
        state = "normal" if merging and not self._running else "disabled"
        self.btn_up.configure(state=state)
        self.btn_down.configure(state=state)
        self.ent_pages.configure(state="normal" if merging and self.preview_file and not self._running else "disabled")

    def _on_option(self) -> None:
        self._save_state()
        self._schedule_estimate()

    def _selected_index(self) -> int | None:
        sel = self.lst_files.curselection()
        return sel[0] if sel else None

    def _on_pages_edit(self) -> None:
        path = self.preview_file
        if path is None or self._running:
            return
        spec = self.var_pages.get()
        try:
            pdfops.parse_pages(spec, self.page_counts.get(path, 0))
        except pdfops.InvalidPageRange:
            pass
        else:
            self.specs[path] = spec.strip()
            self._refresh_list()
            self._update_merge_summary()
        self._update_pages_hint()

    def _update_pages_hint(self) -> None:
        path = self.preview_file
        spec = self.var_pages.get()
        if path is not None:
            try:
                pdfops.parse_pages(spec, self.page_counts.get(path, 0))
            except pdfops.InvalidPageRange as exc:
                self.lbl_pages_hint.configure(text=t("lbl_pages_bad", token=exc.token, pages=exc.pages), foreground=BAD_FG)
                return
        self.lbl_pages_hint.configure(text=t("lbl_pages_hint"), foreground=HINT_FG)

    def _selected_pages(self, path: str) -> int:
        try:
            return len(pdfops.parse_pages(self.specs.get(path, ""), self.page_counts.get(path, 0)))
        except pdfops.InvalidPageRange:
            return self.page_counts.get(path, 0)

    def _update_merge_summary(self) -> None:
        if len(self.files) < 2:
            self.lbl_merge_summary.configure(text=t("lbl_merge_need_two") if self.files else "")
            return
        pages = sum(self._selected_pages(p) for p in self.files)
        self.lbl_merge_summary.configure(text=t("lbl_merge_summary", files=len(self.files), pages=pages))

    # ------------------------------------------------------------ size estimate
    def _schedule_estimate(self) -> None:
        if self.estimate_job:
            self.root.after_cancel(self.estimate_job)
            self.estimate_job = None
        self.estimate_gen += 1
        self._live["gen"] = self.estimate_gen
        if self.mode() != "shrink" or not self.files or self._running:
            self._estimate_kwargs = None
            self._estimating = False
            self._update_estimate_label()
            return
        self._estimating = True
        self._update_estimate_label()
        self.estimate_job = self.root.after(ESTIMATE_DELAY_MS, self._start_estimate)

    def _start_estimate(self) -> None:
        self.estimate_job = None
        gen = self.estimate_gen
        threading.Thread(target=_estimate_worker, name="estimate", daemon=True,
                         args=(list(self.files), self.shrink_options(), gen, self._live, self.cancel_event, self.q)).start()

    def _update_estimate_label(self) -> None:
        if self._estimating:
            self.lbl_estimate.configure(text=t("lbl_estimating"))
        elif self._estimate_kwargs:
            k = self._estimate_kwargs
            key = "lbl_estimate_all" if k["count"] > 1 else "lbl_estimate"
            self.lbl_estimate.configure(text=t(key, count=k["count"], before=pdfops.human_size(k["before"]),
                                               after=pdfops.human_size(k["after"])))
        else:
            self.lbl_estimate.configure(text="")

    # ------------------------------------------------------------ files
    def _on_drop(self, event) -> None:
        if self._running:
            return
        paths = self.root.tk.splitlist(event.data)
        self.add_paths(list(paths))

    def _add_files_dialog(self) -> None:
        paths = filedialog.askopenfilenames(filetypes=[(t("file_dialog_pdf"), "*.pdf")])
        if paths:
            self.add_paths(list(paths))

    def _add_folder_dialog(self) -> None:
        d = filedialog.askdirectory()
        if d:
            self.add_paths([d])

    def add_paths(self, paths: list[str]) -> None:
        if self._running:
            return
        for p in paths:
            if not os.path.exists(p):
                self.log("err_open_failed", name=p)
        found = pdfops.collect_pdfs(paths)
        if not found:
            self.log("err_no_pdf_found")
            return
        problems: list[str] = []
        first_new = None
        known = {os.path.normcase(p) for p in self.files}
        for path in found:
            if os.path.normcase(path) in known:
                continue
            name = os.path.basename(path)
            try:
                pages, size = pdfops.inspect_pdf(path)
            except pdfops.PasswordRequired:
                problems.append(t("err_password", name=name))
                self.log("err_password", name=name)
                continue
            except pdfops.EmptyDocument:
                problems.append(t("err_empty_pdf", name=name))
                self.log("err_empty_pdf", name=name)
                continue
            except Exception:
                problems.append(t("err_open_failed", name=name))
                self.log("err_open_failed", name=name)
                continue
            self.files.append(path)
            known.add(os.path.normcase(path))
            self.page_counts[path] = pages
            self.sizes[path] = size
            self.log("log_added", name=name, pages=pages, size=pdfops.human_size(size))
            if first_new is None:
                first_new = len(self.files) - 1
        self._refresh_list()
        self._update_merge_summary()
        self._schedule_estimate()
        if first_new is not None and self.preview_file is None:
            self._select_file(first_new)
        if problems:
            messagebox.showwarning(t("dlg_error"), "\n".join(problems), parent=self.root)

    def _refresh_list(self) -> None:
        sel = self._selected_index()
        top = self.lst_files.yview()[0] if self.files else 0.0
        self.lst_files.delete(0, "end")
        for i, path in enumerate(self.files):
            spec = self.specs.get(path, "")
            info = t("lbl_file_info", pages=self.page_counts.get(path, 0), size=pdfops.human_size(self.sizes.get(path, 0)))
            line = f"{i + 1}. {os.path.basename(path)}  ({info})"
            if spec:
                line += f"  [{spec}]"
            self.lst_files.insert("end", line)
        if sel is not None and sel < len(self.files):
            self.lst_files.selection_set(sel)
            self.lst_files.yview_moveto(top)

    def clear_files(self) -> None:
        if self._running:
            return
        self.files.clear()
        self.page_counts.clear()
        self.sizes.clear()
        self.specs.clear()
        self.lst_files.delete(0, "end")
        self.preview_file = None
        self.preview_gen += 1
        self._preview = None
        self._photos.clear()
        self.cv_preview.delete("all")
        self.var_pages.set("")
        self.lbl_preview_msg.configure(text=t("preview_empty"))
        self._update_file_info()
        self._update_merge_summary()
        self._schedule_estimate()
        self._update_mode_controls()

    def remove_selected(self) -> None:
        idx = self._selected_index()
        if self._running or idx is None:
            return
        path = self.files.pop(idx)
        for d in (self.page_counts, self.sizes, self.specs):
            d.pop(path, None)
        self._refresh_list()
        self._update_merge_summary()
        self._schedule_estimate()
        if path == self.preview_file:
            self.preview_file = None
            self._preview = None
            self.cv_preview.delete("all")
            self.var_pages.set("")
            if self.files:
                self._select_file(min(idx, len(self.files) - 1))
            else:
                self.lbl_preview_msg.configure(text=t("preview_empty"))
                self._update_file_info()
                self._update_mode_controls()

    def _move_selected(self, step: int) -> None:
        idx = self._selected_index()
        if self._running or idx is None:
            return
        new = idx + step
        if not (0 <= new < len(self.files)):
            return
        self.files[idx], self.files[new] = self.files[new], self.files[idx]
        self.lst_files.selection_clear(0, "end")
        self.lst_files.selection_set(new)
        self._refresh_list()
        self.lst_files.see(new)

    def _on_list_press(self, event) -> None:
        self._drag_index = self.lst_files.nearest(event.y) if self.files else None

    def _on_list_drag(self, event) -> None:
        if self._drag_index is None or self._running or not self.files:
            return
        over = self.lst_files.nearest(event.y)
        if over != self._drag_index and 0 <= over < len(self.files):
            path = self.files.pop(self._drag_index)
            self.files.insert(over, path)
            self._drag_index = over
            self.lst_files.selection_clear(0, "end")
            self.lst_files.selection_set(over)
            self._refresh_list()

    def _on_list_release(self, _event) -> None:
        self._drag_index = None

    def _on_file_select(self, _event=None) -> None:
        idx = self._selected_index()
        if idx is not None:
            self._select_file(idx, from_list=True)

    def _select_file(self, index: int, from_list: bool = False) -> None:
        if not (0 <= index < len(self.files)):
            return
        if not from_list:
            self.lst_files.selection_clear(0, "end")
            self.lst_files.selection_set(index)
            self.lst_files.see(index)
        path = self.files[index]
        if path != self.preview_file:
            self.preview_file = path
            self.var_pages.set(self.specs.get(path, ""))
            self._update_pages_hint()
            self._start_preview()
        self._update_file_info()
        self._update_mode_controls()

    def _update_file_info(self) -> None:
        path = self.preview_file
        if path is None:
            self.lbl_file_info.configure(text="")
            return
        self.lbl_file_info.configure(text=t("lbl_file_info", pages=self.page_counts.get(path, 0),
                                            size=pdfops.human_size(self.sizes.get(path, 0))))

    # ------------------------------------------------------------ preview
    def _start_preview(self) -> None:
        path = self.preview_file
        if path is None:
            return
        self.preview_gen += 1
        self._preview = None
        self.cv_preview.delete("all")
        self.lbl_preview_msg.configure(text="")
        threading.Thread(target=_preview_worker, name="preview", daemon=True,
                         args=(path, self.preview_gen, self.q)).start()

    def _redraw_preview(self) -> None:
        image = self._preview
        if image is None:
            return
        cv = self.cv_preview
        cw, ch = max(cv.winfo_width(), 50), max(cv.winfo_height(), 50)
        scale = min((cw - 16) / image.width, (ch - 16) / image.height, 1.0)
        w, h = max(1, int(image.width * scale)), max(1, int(image.height * scale))
        # master= pins the image to this window's interpreter: with several Tk()
        # in one process (tests) the default root may be a destroyed one
        photo = ImageTk.PhotoImage(image.resize((w, h), Image.LANCZOS), master=cv)
        self._photos = [photo]
        cv.delete("all")
        ox, oy = (cw - w) // 2, (ch - h) // 2
        cv.create_image(ox, oy, image=photo, anchor="nw")
        cv.create_rectangle(ox, oy, ox + w, oy + h, outline="#888")

    # ------------------------------------------------------------ queue from the workers
    def _poll(self) -> None:
        if self._closing:
            return
        try:
            while True:
                msg = self.q.get_nowait()
                self._handle(msg)
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll)

    def _handle(self, msg: tuple) -> None:
        kind = msg[0]
        if kind == "preview":
            _, gen, path, image = msg
            if gen != self.preview_gen or path != self.preview_file:
                return
            self._preview = image
            if image is None:
                self.lbl_preview_msg.configure(text=t("err_open_failed", name=os.path.basename(path)))
            else:
                self._redraw_preview()
        elif kind == "estimate":
            _, gen, count, before, after = msg
            if gen != self.estimate_gen:
                return
            self._estimating = False
            self._estimate_kwargs = {"count": count, "before": before, "after": after}
            self._update_estimate_label()
        elif kind == "progress":
            _, fraction, name = msg
            self.progress.configure(value=int(fraction * PROGRESS_STEPS))
            self._set_status("status_processing", done=round(fraction * 100), total=100, name=name)
        elif kind == "log":
            _, key, kwargs = msg
            self.log(key, **kwargs)
        elif kind == "saved":
            self.outputs.append(msg[1])
            self.log("log_saved", path=msg[1])
        elif kind == "cancelled":
            self.log("log_cancelled", name=msg[1])
            self._finished(msg_mode=None, info=None, cancelled=True)
        elif kind == "done":
            _, mode, info = msg
            self._finished(mode, info, cancelled=False)

    # ------------------------------------------------------------ running
    def run(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.files:
            messagebox.showinfo(t("app_title"), t("msg_no_files"), parent=self.root)
            return
        mode = self.mode()
        if mode == "merge":
            if len(self.files) < 2:
                messagebox.showinfo(t("app_title"), t("msg_need_two"), parent=self.root)
                return
            for path in self.files:
                try:
                    pdfops.parse_pages(self.specs.get(path, ""), self.page_counts.get(path, 0))
                except pdfops.InvalidPageRange as exc:
                    messagebox.showerror(t("dlg_error"), t("err_page_range", token=exc.token, name=os.path.basename(path),
                                                            pages=exc.pages), parent=self.root)
                    return
        self._save_state()
        total_pages = sum(self.page_counts.get(p, 0) for p in self.files)
        if total_pages >= pdfops.LARGE_PAGE_COUNT:
            if not messagebox.askyesno(t("dlg_confirm"), t("msg_large_file", pages=total_pages), parent=self.root):
                return
        self.cancel_event.clear()
        self.outputs = []
        self.progress.configure(value=0)
        self._set_controls(running=True)
        self.worker = threading.Thread(
            target=_job_worker, name="job", daemon=True,
            args=(mode, list(self.files), dict(self.specs), self.merge_options(), self.shrink_options(),
                  self.cancel_event, self.q))
        self.worker.start()

    def _finished(self, msg_mode: str | None, info: dict | None, cancelled: bool) -> None:
        self._set_controls(running=False)
        if cancelled or self._closing:
            self._set_status("status_cancelled")
            return
        self._set_status("status_done")
        self.progress.configure(value=PROGRESS_STEPS)
        if self.outputs:
            self.btn_open.configure(state="normal")
        if info is None:
            return
        if msg_mode == "merge":
            msg = t("msg_done_merge", files=info["files"], pages=info["pages"])
        else:
            msg = t("msg_done_shrink", count=info["count"], before=pdfops.human_size(info["before"]),
                    after=pdfops.human_size(info["after"]))
        if info.get("skipped"):
            msg += "\n" + t("msg_skipped_files", count=info["skipped"])
        messagebox.showinfo(t("app_title"), msg, parent=self.root)

    def cancel(self) -> None:
        self.cancel_event.set()
        self.btn_cancel.configure(state="disabled")

    def _set_controls(self, running: bool) -> None:
        self._running = running
        state = "disabled" if running else "normal"
        for w in (self.btn_run, self.btn_add, self.btn_add_dir, self.btn_clear, self.btn_remove, self.chk_bookmarks):
            w.configure(state=state)
        self.cmb_lang.configure(state="disabled" if running else "readonly")
        self.btn_cancel.configure(state="normal" if running else "disabled")
        if running:
            self.btn_open.configure(state="disabled")
        self._update_mode_controls()

    def open_result(self) -> None:
        if not self.outputs:
            return
        target = self.outputs[0]
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(target)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", target])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(target)])
        except OSError:
            pass

    def _on_close(self) -> None:
        self._closing = True
        self.cancel_event.set()
        self._live["gen"] = -1
        try:
            self._save_state()
        except Exception:
            pass
        self._close_when_idle()

    def _close_when_idle(self) -> None:
        """Wait for the worker before tearing down. It is a daemon thread, so
        exiting under it mid-save would leave a stray .part file behind. The
        cancel flag stops it at the next page; a save already running is
        allowed to finish."""
        if self.worker and self.worker.is_alive():
            self.root.after(100, self._close_when_idle)
            return
        # The trace callback is registered in Tcl's command table, which the garbage
        # collector cannot see: left in place it would keep this App alive forever.
        try:
            self.var_pages.trace_remove("write", self._pages_trace)
        except Exception:
            pass
        self.root.destroy()

    def mainloop(self) -> None:
        self.root.mainloop()


def launch(initial_files: list[str] | None = None) -> None:
    App(initial_files).mainloop()
