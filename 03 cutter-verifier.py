import cv2
import json
import os
import queue
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
import easyocr
import torch
from PIL import Image, ImageTk
import urllib.request
import urllib.parse
import urllib.error
import difflib
import re

# ============================================================
# CONFIGURATION
# ============================================================
VIDEO_FOLDER = r"C:\Users\hendr\PycharmProjects\sumocutter\videos"
JSON_FOLDER = r"C:\Users\hendr\PycharmProjects\sumocutter\output"
CUT_OUTPUT_DIR = r"D:\cutvideos"

SUMOSTATS_API = "https://sumostats.com"
SUPABASE_URL = "https://jkvxeamupwubyzqijwya.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImprdnhlYW11cHd1Ynl6cWlqd3lhIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MDI0Nzc3MzMsImV4cCI6MjAxODA1MzczM30.jVViIrwrLPl3Vfi_KkXWawJ_TD-Mh9sj48wQnSuqiMI"

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov"}

REGION_F1 = (220, 55, 445, 118)
REGION_F2 = (595, 55, 832, 117)
OCR_MIN_CONFIDENCE = 0.2

# ── TIMELINE RANGE ROOM CONFIGURATION ───────────────────────
DEFAULT_LO = 90
DEFAULT_HI = 90

# ── Greyscale palette ────────────────────────────────────────
BG = "#1c1c1c"
BG2 = "#252525"
BG3 = "#2e2e2e"
BG4 = "#383838"
BORDER = "#484848"
BORDER_LT = "#5a5a5a"
TEXT = "#e0e0e0"
TEXT_DIM = "#888888"
ACCENT2 = "#c8c8c8"
RED_MARK = "#cc4444"
ORG_MARK = "#cc8833"
THUMB_COL = "#b0b0b0"
FILL_COL = "#606060"
ALERT_BG = "#3a1a1a"
ALERT_BD = "#884444"
WARN_BG = "#2e2010"
WARN_BD = "#886633"

# ── "Beginning not found" card colours ──────────────────────
BEGIN_BG = "#1a0a2e"
BEGIN_BD = "#7744cc"

# Torikumi-tab specific colours
ASSIGNED_BG = "#0d2a1a"
ASSIGNED_BD = "#2a7a4a"
PARTIAL_BG = "#1a2000"
PARTIAL_BD = "#607020"
UNASSIGNED_BG = "#1c1c1c"
UNASSIGNED_BD = "#484848"
FUSEN_BG = "#2a0a0a"
FUSEN_BD = "#884444"

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_HEAD = ("Segoe UI", 13, "bold")
FONT_MONO = ("Consolas", 10)

FONT_SIDEBAR = ("Segoe UI", 12, "bold")
FONT_INPUTS = ("Segoe UI", 13, "bold")
FONT_SUGGEST = ("Segoe UI", 13, "bold")

CARD_COLS = 4
THUMB_W = 300
THUMB_H = 169
RANGE_STEP = 60

BASHO_MONTH = {
    "hatsu": 1,
    "haru": 3,
    "natsu": 5,
    "nagoya": 7,
    "aki": 9,
    "kyushu": 11,
}

FUZZY_THRESHOLD = 0.55
FUSEN_KEYWORDS = ["fusen", "不戦", "fusenpai", "fusensho"]


# ============================================================
# MISC HELPERS
# ============================================================
def fmt_time(s):
    if s is None:
        return "00:00:00.00"
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    return f"{h:02d}:{m:02d}:{s % 60:05.2f}"


def fmt_dur(s):
    if s is None:
        return "0s"
    m, sec = int(s // 60), int(s % 60)
    return f"{m}m {sec:02d}s" if m else f"{sec}s"


def build_output_name(video_path: str, fight: dict) -> str:
    stem = os.path.splitext(os.path.basename(video_path))[0]
    ext = os.path.splitext(video_path)[1]
    n = fight.get("fight_number", 0)
    return f"{stem}_fight{n:02d}{ext}"


def apply_style(style: ttk.Style):
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, font=FONT)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab",
                    background=BG3, foreground=TEXT_DIM,
                    padding=[16, 6], font=FONT)
    style.map("TNotebook.Tab",
              background=[("selected", BG2)],
              foreground=[("selected", TEXT)])
    style.configure("TButton",
                    background=BG4, foreground=TEXT,
                    borderwidth=1, relief="flat",
                    font=FONT, padding=[7, 3])
    style.map("TButton",
              background=[("active", BORDER_LT), ("pressed", BORDER)])
    style.configure("TCombobox",
                    fieldbackground=BG3, background=BG3,
                    foreground=TEXT, selectbackground=BG4,
                    selectforeground=TEXT, insertcolor=TEXT)
    style.map("TCombobox",
              fieldbackground=[("readonly", BG3)],
              selectbackground=[("readonly", BG4)],
              foreground=[("readonly", TEXT)])
    style.configure("TScrollbar",
                    background=BG3, troughcolor=BG,
                    bordercolor=BG, arrowcolor=TEXT_DIM)


# ============================================================
# BASHO / DAY PARSING
# ============================================================
def parse_basho_day(json_filename: str):
    text = json_filename.lower()
    year_m = re.search(r"(\d{4})", text)
    if not year_m:
        return None, None
    year = int(year_m.group(1))

    month = None
    for name, mon in BASHO_MONTH.items():
        if name in text:
            month = mon
            break
    if month is None:
        return None, None

    day_m = re.search(r"day[\s_]*(\d{1,2})", text)
    if not day_m:
        return None, None
    day = int(day_m.group(1))

    basho_id = f"{year}{month:02d}"
    return basho_id, day


# ============================================================
# FUZZY KANJI NAME MATCHING
# ============================================================
def _norm(name: str) -> str:
    if not name:
        return ""
    return name.strip().lower().replace(" ", "").replace("\u3000", "")


def get_rikishi_kanji(bout: dict, num: int) -> str:
    return (bout.get(f"rikishi{num}_shikona_ja") or
            bout.get(f"rikishi{num}_shikona_kanji") or
            bout.get(f"rikishi{num}_kanji") or
            bout.get(f"rikishi{num}_shikona") or "")


def get_rikishi_romaji(bout: dict, num: int) -> str:
    return bout.get(f"rikishi{num}_shikona") or ""


def name_similarity(a: str, b: str) -> float:
    norm_a = _norm(a)
    norm_b = _norm(b)
    if not norm_a or not norm_b:
        return 0.0
    if norm_a == norm_b:
        return 1.0
    if norm_a in norm_b or norm_b in norm_a:
        return 0.95
    return difflib.SequenceMatcher(None, norm_a, norm_b).ratio()


def match_fight_to_bout(fight: dict, bouts: list[dict]):
    f1 = fight.get("fighter1") or ""
    f2 = fight.get("fighter2") or ""

    if not f1 and not f2:
        return None, 0.0, False

    best_bout = None
    best_score = 0.0
    best_partial = False

    for bout in bouts:
        r1 = get_rikishi_kanji(bout, 1)
        r2 = get_rikishi_kanji(bout, 2)

        f1_norm, f2_norm = _norm(f1), _norm(f2)
        r1_norm, r2_norm = _norm(r1), _norm(r2)

        if f1_norm and f2_norm and r1_norm and r2_norm:
            if (f1_norm == r1_norm and f2_norm == r2_norm) or \
               (f1_norm == r2_norm and f2_norm == r1_norm):
                return bout, 1.0, False

        pairs_both = [
            (f1, r1, f2, r2),
            (f1, r2, f2, r1),
        ]

        single_scores = []
        if f1:
            single_scores += [name_similarity(f1, r1), name_similarity(f1, r2)]
        if f2:
            single_scores += [name_similarity(f2, r1), name_similarity(f2, r2)]

        if f1 and f2:
            for a1, b1, a2, b2 in pairs_both:
                s1 = name_similarity(a1, b1)
                s2 = name_similarity(a2, b2)
                combined = (s1 + s2) / 2
                if combined > best_score:
                    best_score = combined
                    best_bout = bout
                    best_partial = (s1 < FUZZY_THRESHOLD or
                                    s2 < FUZZY_THRESHOLD)
        else:
            if single_scores:
                sc = max(single_scores)
                if sc > best_score:
                    best_score = sc
                    best_bout = bout
                    best_partial = True

    if best_score < FUZZY_THRESHOLD:
        return None, best_score, False

    return best_bout, best_score, best_partial


# ============================================================
# TORIKUMI FETCHER
# ============================================================
def fetch_torikumi(basho_id: str, day: int) -> list[dict]:
    url = (f"{SUMOSTATS_API}/api/v1/basho/{basho_id}/torikumi"
           f"?day={day}&names=at_basho")
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = json.loads(resp.read().decode())

    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("matches", "bouts", "torikumi", "results", "data"):
            if key in raw and isinstance(raw[key], list):
                return raw[key]
    return []


def is_fusen(kimarite: str | None) -> bool:
    if not kimarite:
        return False
    return any(kw in kimarite.lower() for kw in FUSEN_KEYWORDS)


# ============================================================
# CUSTOM CANVAS SLIDER
# ============================================================
class RangeSlider(tk.Frame):
    TRACK_H = 6
    THUMB_R = 9
    H = 40

    def __init__(self, parent, from_=0.0, to=100.0,
                 initial=0.0, on_change=None, **kw):
        super().__init__(parent, bg=BG2, **kw)
        self._from = float(from_)
        self._to = float(to)
        self._value = float(initial)
        self._mark_s = None
        self._mark_e = None
        self._on_change = on_change

        self.canvas = tk.Canvas(self, bg=BG2, highlightthickness=0,
                                height=self.H)
        self.canvas.pack(fill=tk.X, expand=True, padx=6)
        self.canvas.bind("<Configure>", self._draw)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)

    def set_range(self, lo: float, hi: float):
        self._from = lo
        self._to = hi
        self._value = max(lo, min(hi, self._value))
        self._draw()

    def set(self, v: float):
        self._value = max(self._from, min(self._to, float(v)))
        self._draw()

    def get(self) -> float:
        return self._value

    def set_marks(self, start_sec, end_sec):
        self._mark_s = start_sec
        self._mark_e = end_sec
        self._draw()

    def _pads(self):
        return self.THUMB_R + 6, self.canvas.winfo_width() - self.THUMB_R - 6

    def _v2x(self, v):
        x0, x1 = self._pads()
        span = self._to - self._from
        if span == 0 or x1 <= x0:
            return x0
        return x0 + (v - self._from) / span * (x1 - x0)

    def _x2v(self, x):
        x0, x1 = self._pads()
        span = self._to - self._from
        if x1 <= x0:
            return self._from
        return max(self._from,
                   min(self._to,
                       self._from + (x - x0) / (x1 - x0) * span))

    def _draw(self, _=None):
        c = self.canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 20:
            return
        cy = h // 2
        x0, x1 = self._pads()

        c.create_rectangle(x0, cy - self.TRACK_H // 2,
                           x1, cy + self.TRACK_H // 2,
                           fill=BG3, outline=BORDER)
        tx = self._v2x(self._value)
        c.create_rectangle(x0, cy - self.TRACK_H // 2,
                           tx, cy + self.TRACK_H // 2,
                           fill=FILL_COL, outline="")

        for val, col in ((self._mark_s, RED_MARK),
                         (self._mark_e, ORG_MARK)):
            if val is None:
                continue
            mx = self._v2x(val)
            c.create_line(mx, cy - 14, mx, cy + 14, fill=col, width=2)
            c.create_polygon(mx - 4, cy - 14,
                             mx + 4, cy - 14,
                             mx, cy - 9,
                             fill=col, outline="")

        c.create_oval(tx - self.THUMB_R, cy - self.THUMB_R,
                      tx + self.THUMB_R, cy + self.THUMB_R,
                      fill=THUMB_COL, outline=ACCENT2, width=1)

    def _press(self, e):
        self._update(e.x)

    def _drag(self, e):
        self._update(e.x)

    def _update(self, x):
        v = round(self._x2v(x), 3)
        if v != self._value:
            self._value = v
            self._draw()
            if self._on_change:
                self._on_change(v)


# ============================================================
# SCROLLABLE FRAME
# ============================================================
class ScrollableFrame(ttk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical",
                                 command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=BG)

        self.inner.bind("<Configure>", lambda e:
            self.canvas.configure(
                scrollregion=self.canvas.bbox("all")))

        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")

        for w in (self.canvas, self.inner):
            w.bind("<MouseWheel>", self._wheel)

    def _wheel(self, e):
        self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def bind_mousewheel(self, widget):
        widget.bind("<MouseWheel>", self._wheel)


# ============================================================
# SCAN QUICK-EDIT FLOATING WINDOW
# ============================================================
class ScanQuickEditWindow(tk.Toplevel):
    """
    Floating Toplevel for quick timestamp nudges from scan grids.

    Design decisions
    ────────────────
    • geometry() is set explicitly the very first time the window is
      shown so it pops open at a fixed size immediately, with no
      grow-animation artefact.
    • Closing hides (withdraw) rather than destroying the window so
      the same object is reused cheaply on subsequent card clicks.
    • A prominent "Save to JSON" button lets the user commit all
      nudges explicitly; it flashes green for 800 ms as feedback.
    • The preview image is cached as a raw OpenCV frame and
      re-scaled on every <Configure> event so it fills the space
      without re-decoding from the video file.
    """

    # Fixed popup dimensions
    _W = 560
    _H = 480

    def __init__(self, master, on_save):
        super().__init__(master)
        self.title("Quick Edit")
        self.configure(bg=BG2)
        self.resizable(True, True)
        self.minsize(400, 320)

        # Hide on close rather than destroy
        self.protocol("WM_DELETE_WINDOW", self.withdraw)

        # Start completely hidden; _show() reveals it properly
        self.withdraw()

        self._on_save = on_save
        self._fight_idx = None
        self._fight = None
        self._cap_ref = None
        self._cap_lock = None
        self._img_tk = None
        self._raw_frame = None
        self._first_show = True          # track whether geometry is set yet

        self._build_ui()

    # ── UI construction ─────────────────────────────────────
    def _build_ui(self):
        # ── title / close bar ───────────────────────────────
        hdr = tk.Frame(self, bg=BG3)
        hdr.pack(fill=tk.X)

        self._title_lbl = tk.Label(
            hdr, text="No fight selected",
            font=FONT_BOLD, bg=BG3, fg=ACCENT2, anchor="w")
        self._title_lbl.pack(side=tk.LEFT, padx=10, pady=6,
                             fill=tk.X, expand=True)

        tk.Button(
            hdr, text="✕",
            font=("Segoe UI", 10, "bold"),
            bg=BG3, fg=TEXT_DIM,
            activebackground="#5a1a1a", activeforeground="#ffaaaa",
            relief="flat", bd=0, padx=8, pady=4,
            cursor="hand2",
            command=self.withdraw,
        ).pack(side=tk.RIGHT)

        # ── preview (fills all spare vertical space) ─────────
        self._img_lbl = tk.Label(
            self, text="Select a card to preview",
            bg="#111111", fg=TEXT_DIM)
        self._img_lbl.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self._img_lbl.bind("<Configure>", self._on_img_resize)

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X, padx=6)

        # ── nudge rows ───────────────────────────────────────
        controls = tk.Frame(self, bg=BG2)
        controls.pack(fill=tk.X, padx=8, pady=(6, 2))

        self._start_time_var = tk.StringVar(value="--:--:--.--")
        self._end_time_var   = tk.StringVar(value="--:--:--.--")

        self._build_nudge_row(controls, "Start",
                              "start_seconds", "start_hms",
                              self._start_time_var)
        self._build_nudge_row(controls, "End  ",
                              "end_seconds", "end_hms",
                              self._end_time_var)

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X, padx=6,
                                                  pady=(4, 0))

        # ── bottom bar: Save button + hint ───────────────────
        bottom = tk.Frame(self, bg=BG2)
        bottom.pack(fill=tk.X, padx=8, pady=6)

        self._save_btn = tk.Button(
            bottom,
            text="💾  Save to JSON",
            font=FONT_BOLD,
            bg=BG4, fg=TEXT,
            activebackground=BORDER_LT, activeforeground=TEXT,
            relief="flat", bd=1,
            padx=14, pady=5,
            cursor="hand2",
            command=self._on_save_clicked,
        )
        self._save_btn.pack(side=tk.LEFT)

        tk.Label(
            bottom,
            text="Single-click → quick edit  |  Double-click → editor",
            bg=BG2, fg=TEXT_DIM,
            font=("Segoe UI", 9, "italic"),
        ).pack(side=tk.RIGHT, padx=6)

    def _build_nudge_row(self, parent, label, field_s, field_h, time_var):
        row = tk.Frame(parent, bg=BG2)
        row.pack(fill=tk.X, pady=3)

        tk.Label(row, text=label, bg=BG2, fg=TEXT,
                 font=FONT_BOLD, width=5, anchor="w").pack(side=tk.LEFT)

        tk.Label(row, textvariable=time_var,
                 bg=BG2, fg=ACCENT2,
                 font=FONT_MONO, width=14).pack(side=tk.LEFT, padx=(4, 10))

        for btn_lbl, delta in (("−1s", -1.0), ("−½s", -0.5),
                                ("+½s", +0.5), ("+1s", +1.0)):
            ttk.Button(
                row, text=btn_lbl, width=5,
                command=lambda d=delta, fs=field_s,
                               fh=field_h, tv=time_var:
                    self._nudge(fs, fh, d, tv),
            ).pack(side=tk.LEFT, padx=2)

    # ── public API ──────────────────────────────────────────
    def load(self, fight_idx: int, fight: dict,
             cap, cap_lock, frame_field: str):
        """Call when a scan card is single-clicked."""
        self._fight_idx = fight_idx
        self._fight = fight
        self._cap_ref = cap
        self._cap_lock = cap_lock
        self._current_preview_field = frame_field

        n  = fight.get("fight_number", fight_idx + 1)
        f1 = fight.get("fighter1") or "?"
        f2 = fight.get("fighter2") or "?"
        self._title_lbl.config(text=f"#{n:02d}  {f1} vs {f2}")

        self._refresh_labels()
        self._refresh_preview(fight.get(frame_field))
        self._show()

    # ── show helper: set geometry once, then just deiconify ──
    def _show(self):
        if self._first_show:
            # Place the window to the right of the master window,
            # or centred on screen if there isn't enough room.
            master = self.master
            mx = master.winfo_x() + master.winfo_width()
            my = master.winfo_y()
            sw = master.winfo_screenwidth()

            # If there's room to the right, place it there;
            # otherwise fall back to a centred position.
            if mx + self._W + 20 <= sw:
                x, y = mx + 10, my
            else:
                x = max(0, (sw - self._W) // 2)
                y = max(0, (master.winfo_screenheight() - self._H) // 2)

            self.geometry(f"{self._W}x{self._H}+{x}+{y}")
            self._first_show = False

        self.deiconify()
        self.lift()

    # ── internal helpers ────────────────────────────────────
    def _refresh_labels(self):
        if self._fight is None:
            return
        s = self._fight.get("start_seconds")
        e = self._fight.get("end_seconds")
        self._start_time_var.set(
            fmt_time(s) if s is not None else "--:--:--.--")
        self._end_time_var.set(
            fmt_time(e) if e is not None else "--:--:--.--")

    def _refresh_preview(self, seconds):
        if seconds is None or self._cap_ref is None:
            self._img_lbl.config(image="", text="No frame")
            self._img_tk = None
            self._raw_frame = None
            return

        with self._cap_lock:
            fps = self._cap_ref.get(cv2.CAP_PROP_FPS) or 30
            self._cap_ref.set(cv2.CAP_PROP_POS_FRAMES,
                              int(seconds * fps))
            ret, frame = self._cap_ref.read()

        if not ret or frame is None:
            self._img_lbl.config(image="", text="Could not decode frame")
            self._img_tk = None
            self._raw_frame = None
            return

        self._raw_frame = frame
        self._scale_and_show()

    def _scale_and_show(self):
        if self._raw_frame is None:
            return
        lbl_w = self._img_lbl.winfo_width()
        lbl_h = self._img_lbl.winfo_height()
        if lbl_w < 10:
            lbl_w = self._W - 12
        if lbl_h < 10:
            lbl_h = 260
        img = Image.fromarray(
            cv2.cvtColor(self._raw_frame, cv2.COLOR_BGR2RGB))
        img.thumbnail((lbl_w, lbl_h), Image.LANCZOS)
        ph = ImageTk.PhotoImage(img)
        self._img_lbl.config(image=ph, text="")
        self._img_tk = ph

    def _on_img_resize(self, _event=None):
        if self._raw_frame is not None:
            self._scale_and_show()

    # ── nudge ────────────────────────────────────────────────
    def _nudge(self, field_s, field_h, delta, time_var):
        if self._fight is None or self._fight_idx is None:
            return
        current = self._fight.get(field_s) or 0.0
        new_val = max(0.0, round(current + delta, 3))
        self._fight[field_s] = new_val
        self._fight[field_h] = fmt_time(new_val)
        time_var.set(fmt_time(new_val))
        self._refresh_preview(new_val)

        # Auto-save on every nudge (keeps things consistent)
        self._on_save(self._fight_idx, field_s, new_val)

    # ── explicit save button ─────────────────────────────────
    def _on_save_clicked(self):
        """
        Explicit save — calls the same on_save callback and gives
        the user a brief green flash on the button as confirmation.
        """
        if self._fight is None or self._fight_idx is None:
            return

        # The fight dict is already up-to-date from nudges;
        # calling on_save with the current start value is enough
        # to trigger a full JSON write.
        field = getattr(self, "_current_preview_field", "start_seconds")
        self._on_save(self._fight_idx, field,
                      self._fight.get(field) or 0.0)

        # Visual feedback: flash green then restore
        original_bg = self._save_btn.cget("bg")
        original_fg = self._save_btn.cget("fg")
        self._save_btn.config(bg="#1a4a1a", fg="#44cc44",
                              text="✓  Saved!")
        self.after(800, lambda: self._save_btn.config(
            bg=original_bg, fg=original_fg,
            text="💾  Save to JSON"))


# ============================================================
# GENERIC SCAN GRID
# ============================================================
def build_scan_grid(scroll_frame, fights, frame_key,
                    quick_scan_widgets, jump_callback,
                    select_callback=None):
    for w in scroll_frame.inner.winfo_children():
        w.destroy()
    quick_scan_widgets.clear()

    if not fights:
        tk.Label(scroll_frame.inner, text="No fights in this file.",
                 bg=BG, fg=TEXT_DIM, font=FONT_HEAD).pack(pady=30)
        return

    current_row = None
    for idx, fight in enumerate(fights):
        if idx % CARD_COLS == 0:
            current_row = tk.Frame(scroll_frame.inner, bg=BG)
            current_row.pack(side=tk.TOP, anchor="w", padx=6, pady=4)

        n  = fight["fight_number"]
        f1 = fight.get("fighter1") or "?"
        f2 = fight.get("fighter2") or "?"
        s  = fight.get("start_seconds")
        e  = fight.get("end_seconds")
        dur = (e - s) if (s is not None and e is not None) else 0

        # Colour priority — read stored flag, NOT live timestamp
        missing_start = fight.get("start_missing", False)

        if missing_start:
            card_bg, card_bd = BEGIN_BG, BEGIN_BD
            badge    = "BEGINNING NOT FOUND"
            badge_fg = "#b080ff"
        elif dur > 90:
            card_bg, card_bd = ALERT_BG, ALERT_BD
            badge    = "EXTREME"
            badge_fg = TEXT
        elif dur > 40:
            card_bg, card_bd = WARN_BG, WARN_BD
            badge    = "SUSPICIOUS"
            badge_fg = TEXT
        else:
            card_bg, card_bd = BG2, BORDER
            badge    = ""
            badge_fg = TEXT

        card = tk.Frame(
            current_row,
            bg=card_bg,
            highlightbackground=card_bd,
            highlightthickness=1,
            width=THUMB_W + 8,
            height=THUMB_H + 44,
        )
        card.pack(side=tk.LEFT, padx=4, pady=4)
        card.pack_propagate(False)

        img_lbl = tk.Label(card, text="loading…",
                           bg="#111111", fg=TEXT_DIM,
                           font=("Segoe UI", 8),
                           width=THUMB_W, height=THUMB_H)
        img_lbl.place(x=4, y=4)

        info = f"#{n:02d}  {f1} vs {f2}  —  {fmt_dur(dur)}"
        if badge:
            info += f"  [{badge}]"

        txt_lbl = tk.Label(card, text=info,
                           bg=card_bg, fg=badge_fg,
                           font=("Segoe UI", 8, "bold"),
                           anchor="center", wraplength=THUMB_W)
        txt_lbl.place(x=4, y=THUMB_H + 6, width=THUMB_W)

        for w in (card, img_lbl, txt_lbl):
            w.bind("<Double-Button-1>",
                   lambda e, i=idx: jump_callback(i))
            if select_callback:
                w.bind("<Button-1>",
                       lambda e, i=idx: select_callback(i))
            scroll_frame.bind_mousewheel(w)

        quick_scan_widgets[idx] = img_lbl


# ============================================================
# TORIKUMI TAB  –  row widget
# ============================================================
class TorikumiRow(tk.Frame):
    ROW_H = 48

    def __init__(self, parent, bout: dict,
                 fight_options: list[tuple[int, str]],
                 on_assign, on_upload, scroll_frame, **kw):
        super().__init__(parent, bg=UNASSIGNED_BG,
                         highlightbackground=UNASSIGNED_BD,
                         highlightthickness=1, **kw)
        self._bout = bout
        self._on_assign = on_assign
        self._on_upload = on_upload
        self._scroll = scroll_frame
        self._has_matched_fight = False
        self._fight_options = fight_options

        r1_kanji  = get_rikishi_kanji(bout, 1)
        r2_kanji  = get_rikishi_kanji(bout, 2)
        r1_romaji = get_rikishi_romaji(bout, 1)
        r2_romaji = get_rikishi_romaji(bout, 2)

        r1_display = (f"{r1_kanji} ({r1_romaji})"
                      if r1_romaji and r1_romaji != r1_kanji
                      else r1_kanji) or "?"
        r2_display = (f"{r2_kanji} ({r2_romaji})"
                      if r2_romaji and r2_romaji != r2_kanji
                      else r2_kanji) or "?"

        kimarite = bout.get("kimarite") or ""
        self._is_fusen = is_fusen(kimarite)

        inner = tk.Frame(self, bg=self["bg"])
        inner.pack(fill=tk.X, padx=8, pady=6)

        tk.Label(inner, text=bout.get("division", ""),
                 bg=self["bg"], fg=TEXT_DIM,
                 font=("Segoe UI", 8), width=9, anchor="w"
                 ).pack(side=tk.LEFT)
        tk.Label(inner, text=r1_display,
                 bg=self["bg"], fg=TEXT,
                 font=("Segoe UI", 9), width=24, anchor="e"
                 ).pack(side=tk.LEFT)
        tk.Label(inner, text=" vs ", bg=self["bg"], fg=TEXT_DIM,
                 font=FONT).pack(side=tk.LEFT)
        tk.Label(inner, text=r2_display,
                 bg=self["bg"], fg=TEXT,
                 font=("Segoe UI", 9), width=24, anchor="w"
                 ).pack(side=tk.LEFT)

        kim_fg = RED_MARK if self._is_fusen else TEXT_DIM
        tk.Label(inner, text=kimarite, bg=self["bg"], fg=kim_fg,
                 font=("Segoe UI", 9, "italic"), width=12, anchor="w"
                 ).pack(side=tk.LEFT, padx=(8, 0))

        self._badge = tk.Label(inner, text="unassigned",
                               bg=self["bg"], fg=TEXT_DIM,
                               font=("Segoe UI", 9, "italic"), width=12,
                               anchor="center")
        self._badge.pack(side=tk.LEFT, padx=(12, 4))

        combo_values = ["— none —"] + [opt[1] for opt in fight_options]
        self._var = tk.StringVar(value=combo_values[0])
        self._combo = ttk.Combobox(inner, textvariable=self._var,
                                   values=combo_values, state="readonly",
                                   width=24, font=("Segoe UI", 9))
        self._combo.pack(side=tk.LEFT, padx=4)
        self._combo.bind("<<ComboboxSelected>>", self._on_combo)

        self._upload_btn = ttk.Button(
            inner, text="Upload", state="disabled",
            command=self._on_upload_clicked, width=8)
        self._upload_btn.pack(side=tk.LEFT, padx=6)

        self._upload_status_lbl = tk.Label(
            inner, text="", bg=self["bg"], fg="#88cc88",
            font=("Segoe UI", 8, "bold"))
        self._upload_status_lbl.pack(side=tk.LEFT, padx=4)

        scroll_frame.bind_mousewheel(self)
        scroll_frame.bind_mousewheel(inner)

    def set_assignment(self, fight_label: str | None,
                       partial: bool, score: float):
        if fight_label is None:
            if self._is_fusen:
                self._set_colors(FUSEN_BG, FUSEN_BD, "fusen", RED_MARK)
            else:
                self._set_colors(UNASSIGNED_BG, UNASSIGNED_BD,
                                 "unassigned", TEXT_DIM)
            self._var.set("— none —")
            self._has_matched_fight = False
            self._upload_btn.config(state="disabled")
            return

        if partial:
            self._set_colors(PARTIAL_BG, PARTIAL_BD,
                             f"partial  {score:.0%}", "#b8b820")
        else:
            self._set_colors(ASSIGNED_BG, ASSIGNED_BD,
                             f"matched  {score:.0%}", "#44cc88")
        self._var.set(fight_label)
        self._has_matched_fight = True

    def update_upload_state(self, logged_in: bool, status_text: str = ""):
        self._upload_btn.config(
            state="normal" if (logged_in and self._has_matched_fight)
            else "disabled")
        if status_text:
            self._upload_status_lbl.config(text=status_text)

    def _set_colors(self, bg, bd, badge_text, badge_fg):
        self.config(bg=bg, highlightbackground=bd)
        for child in self.winfo_children():
            self._recolor(child, bg)
        self._badge.config(text=badge_text, fg=badge_fg, bg=bg)

    def _recolor(self, widget, bg):
        try:
            widget.config(bg=bg)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._recolor(child, bg)

    def _on_combo(self, _=None):
        val = self._var.get()
        bout_id = self._bout.get("id")
        if val == "— none —":
            self._on_assign(bout_id, None)
        else:
            fight_num = next(
                (num for num, txt in self._fight_options if txt == val),
                None)
            self._on_assign(bout_id, fight_num)

    def _on_upload_clicked(self):
        val = self._var.get()
        if val and val != "— none —":
            fight_num = next(
                (num for num, txt in self._fight_options if txt == val),
                None)
            if fight_num is not None:
                self._on_upload(self._bout.get("id"), fight_num, self)


# ============================================================
# MAIN APPLICATION
# ============================================================
class SumoVerifierApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Sumo Cut Verifier")
        self.geometry("1680x980")
        self.minsize(1280, 720)
        self.configure(bg=BG)

        apply_style(ttk.Style())

        self.json_files = []
        self.current_json_path = None
        self.current_video_path = None
        self.json_data = {}
        self.current_fight_idx = None
        self.video_duration = 0.0

        self._saved_start = None
        self._saved_end = None
        self._range_lo = DEFAULT_LO
        self._range_hi = DEFAULT_HI

        self.cap = None
        self.cap_lock = threading.Lock()

        self.start_img_tk = None
        self.end_img_tk = None

        self.latest_ocr_f1 = ""
        self.latest_ocr_f2 = ""

        self.tachiai_queue = queue.Queue()
        self.ending_queue  = queue.Queue()
        self.tachiai_thread = None
        self.ending_thread  = None
        self.stop_tachiai = threading.Event()
        self.stop_ending  = threading.Event()
        self.tachiai_widgets = {}
        self.ending_widgets  = {}

        self.access_token    = None
        self.logged_in_email = None

        self._torikumi_bouts  = []
        self._torikumi_rows   = []
        self._torikumi_assign: dict[int, int | None] = {}
        self._fight_to_bout:  dict[int, int | None] = {}
        self._torikumi_basho_id = None
        self._torikumi_day      = None
        self._torikumi_thread   = None
        self._torikumi_status_var = tk.StringVar(value="")

        self.ocr_reader = None
        self._init_ocr()

        self._build_ui()

        # Quick-edit windows — created once after the master exists
        self._tachiai_qe = ScanQuickEditWindow(
            self, on_save=self._scan_quick_save)
        self._ending_qe  = ScanQuickEditWindow(
            self, on_save=self._scan_quick_save)

        self._load_files()

    def _init_ocr(self):
        gpu = torch.cuda.is_available()
        print(f"[OCR] Loading ({'GPU' if gpu else 'CPU'})…")
        self.ocr_reader = easyocr.Reader(["ja", "en"], gpu=gpu)
        print("[OCR] Ready.")

    # ============================================================
    # UI BUILD
    # ============================================================
    def _build_ui(self):
        bar = tk.Frame(self, bg=BG3, height=46)
        bar.pack(side=tk.TOP, fill=tk.X)
        bar.pack_propagate(False)
        tk.Label(bar, text="Sumo Cut Verifier",
                 font=("Segoe UI", 14, "bold"),
                 bg=BG3, fg=TEXT).pack(side=tk.LEFT, padx=18, pady=10)

        body = tk.Frame(self, bg=BG)
        body.pack(fill=tk.BOTH, expand=True)

        self._build_sidebar(body)

        self.notebook = ttk.Notebook(body)
        self.notebook.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True,
                           padx=(0, 6), pady=6)

        self.editor_tab   = ttk.Frame(self.notebook, padding=8)
        self.tachiai_tab  = ttk.Frame(self.notebook, padding=8)
        self.ending_tab   = ttk.Frame(self.notebook, padding=8)
        self.torikumi_tab = ttk.Frame(self.notebook, padding=8)

        self.notebook.add(self.editor_tab,   text="  Fight Editor  ")
        self.notebook.add(self.tachiai_tab,  text="  Tachiai Scan  ")
        self.notebook.add(self.ending_tab,   text="  Ending Scan   ")
        self.notebook.add(self.torikumi_tab, text="  Torikumi      ")

        self._build_editor_tab()
        self._build_tachiai_tab()
        self._build_ending_tab()
        self._build_torikumi_tab()

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    # ── sidebar ─────────────────────────────────────────────
    def _build_sidebar(self, parent):
        sb = tk.Frame(parent, bg=BG2, width=320)
        sb.pack(side=tk.LEFT, fill=tk.Y, padx=(6, 3), pady=6)
        sb.pack_propagate(False)

        tk.Label(sb, text="SumoStats Auth", font=FONT_BOLD,
                 bg=BG2, fg=ACCENT2).pack(
            anchor=tk.W, padx=12, pady=(14, 2))

        self.email_entry = tk.Entry(
            sb, bg=BG, fg=TEXT, insertbackground=TEXT,
            relief="flat", highlightbackground=BORDER,
            highlightthickness=1)
        self.email_entry.pack(fill=tk.X, padx=10, pady=2)
        self.email_entry.insert(0, "Email")

        self.pass_entry = tk.Entry(
            sb, show="*", bg=BG, fg=TEXT, insertbackground=TEXT,
            relief="flat", highlightbackground=BORDER,
            highlightthickness=1)
        self.pass_entry.pack(fill=tk.X, padx=10, pady=2)
        self.pass_entry.insert(0, "Password")

        self.login_btn = ttk.Button(sb, text="Log In",
                                    command=self._on_login_clicked)
        self.login_btn.pack(fill=tk.X, padx=10, pady=6)

        self.auth_status_lbl = tk.Label(
            sb, text="Not Authenticated",
            font=("Segoe UI", 9, "italic"), bg=BG2, fg=RED_MARK)
        self.auth_status_lbl.pack(anchor=tk.W, padx=12, pady=(0, 10))

        tk.Frame(sb, bg=BORDER, height=1).pack(fill=tk.X, padx=10, pady=5)

        tk.Label(sb, text="Day / VOD", font=FONT_BOLD,
                 bg=BG2, fg=ACCENT2).pack(
            anchor=tk.W, padx=12, pady=(10, 2))

        self.files_combo = ttk.Combobox(sb, state="readonly",
                                        font=("Segoe UI", 10))
        self.files_combo.pack(fill=tk.X, padx=10, pady=4)
        self.files_combo.bind("<<ComboboxSelected>>",
                              self._on_json_selected)

        tk.Frame(sb, bg=BORDER, height=1).pack(
            fill=tk.X, padx=10, pady=10)

        tk.Label(sb, text="Fights", font=FONT_BOLD,
                 bg=BG2, fg=ACCENT2).pack(anchor=tk.W, padx=12)

        self.fights_lb = tk.Listbox(
            sb, bg=BG, fg=TEXT,
            selectbackground=BG4, selectforeground=TEXT,
            activestyle="none", font=FONT_SIDEBAR,
            borderwidth=0, highlightthickness=0, relief="flat")
        self.fights_lb.pack(fill=tk.BOTH, expand=True,
                            padx=8, pady=(4, 10))
        self.fights_lb.bind("<<ListboxSelect>>",
                            self._on_fight_selected)

    # ── editor tab ──────────────────────────────────────────
    def _build_editor_tab(self):
        et = self.editor_tab

        prev_row = tk.Frame(et, bg=BG)
        prev_row.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        self.start_lbl = self._make_preview(prev_row, "START / TACHIAI")
        tk.Frame(prev_row, bg=BORDER, width=2).pack(
            side=tk.LEFT, fill=tk.Y, padx=4)
        self.end_lbl = self._make_preview(prev_row, "END / FIGHT DECIDED")

        ctrl = tk.Frame(et, bg=BG2,
                        highlightbackground=BORDER, highlightthickness=1)
        ctrl.pack(fill=tk.X)

        nr = tk.Frame(ctrl, bg=BG2)
        nr.pack(fill=tk.X, padx=12, pady=(10, 2))
        self.f1_entry = self._name_entry(nr, "Fighter 1:")
        tk.Label(nr, text="vs", bg=BG2, fg=TEXT_DIM,
                 font=FONT_BOLD).pack(side=tk.LEFT, padx=8)
        self.f2_entry = self._name_entry(nr, "Fighter 2:")
        ttk.Button(nr, text="OCR Start",
                   command=lambda: self._run_ocr("start")
                   ).pack(side=tk.LEFT, padx=6)
        ttk.Button(nr, text="OCR End",
                   command=lambda: self._run_ocr("end")
                   ).pack(side=tk.LEFT, padx=2)

        ocr_sug_row = tk.Frame(ctrl, bg=BG2)
        ocr_sug_row.pack(fill=tk.X, padx=12, pady=(2, 4))
        tk.Label(ocr_sug_row, text="OCR Hits:", bg=BG2, fg=TEXT_DIM,
                 font=FONT_BOLD).pack(side=tk.LEFT, padx=(0, 10))
        self.f1_sug_btn = tk.Button(
            ocr_sug_row, text="[ None ]", font=FONT_SUGGEST,
            bg=BG3, fg=TEXT_DIM,
            activebackground=BG4, activeforeground=TEXT,
            relief="flat", bd=1, cursor="hand2", padx=10, pady=2,
            command=self._apply_f1_ocr_suggestion)
        self.f1_sug_btn.pack(side=tk.LEFT, padx=5)
        tk.Label(ocr_sug_row, text=" | ", bg=BG2, fg=BORDER,
                 font=FONT_BOLD).pack(side=tk.LEFT, padx=10)
        self.f2_sug_btn = tk.Button(
            ocr_sug_row, text="[ None ]", font=FONT_SUGGEST,
            bg=BG3, fg=TEXT_DIM,
            activebackground=BG4, activeforeground=TEXT,
            relief="flat", bd=1, cursor="hand2", padx=10, pady=2,
            command=self._apply_f2_ocr_suggestion)
        self.f2_sug_btn.pack(side=tk.LEFT, padx=5)

        self._uncertain_row = tk.Frame(ctrl, bg=BG2)
        self._uncertain_row.pack(fill=tk.X, padx=12, pady=(0, 4))
        self._uncertain_btns = []

        tk.Frame(ctrl, bg=BORDER, height=1).pack(
            fill=tk.X, padx=12, pady=6)

        sr = tk.Frame(ctrl, bg=BG2)
        sr.pack(fill=tk.X, padx=12, pady=(0, 6))
        sr.columnconfigure(0, weight=1)
        sr.columnconfigure(2, weight=1)

        self._start_panel = self._build_slider_panel(
            sr, "Start / Tachiai", "start")
        self._start_panel.grid(row=0, column=0, sticky="ew")
        tk.Frame(sr, bg=BORDER, width=2).grid(
            row=0, column=1, sticky="ns", padx=10)
        self._end_panel = self._build_slider_panel(
            sr, "End / Fight Decided", "end")
        self._end_panel.grid(row=0, column=2, sticky="ew")

        alt_outer = tk.Frame(ctrl, bg=BG2)
        alt_outer.pack(fill=tk.X, padx=12, pady=(2, 4))
        tk.Label(alt_outer, text="Alt peaks:", bg=BG2, fg=TEXT_DIM,
                 font=FONT_BOLD).pack(side=tk.LEFT, padx=(0, 8))
        self._alt_peak_frame = tk.Frame(alt_outer, bg=BG2)
        self._alt_peak_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._alt_peak_btns = []

        tk.Frame(ctrl, bg=BORDER, height=1).pack(
            fill=tk.X, padx=12, pady=4)

        rr = tk.Frame(ctrl, bg=BG2)
        rr.pack(fill=tk.X, padx=12, pady=(4, 6))
        self._range_display = tk.Label(
            rr, text=self._range_text(),
            font=("Segoe UI", 9), bg=BG2, fg=TEXT_DIM)
        self._range_display.pack(side=tk.LEFT, padx=6)
        for label, cmd in reversed([
            ("◀◀  Past",   lambda: self._shift_lo(+RANGE_STEP)),
            ("Past  ▶",    lambda: self._shift_lo(-RANGE_STEP)),
            ("◀  Future",  lambda: self._shift_hi(-RANGE_STEP)),
            ("Future  ▶▶", lambda: self._shift_hi(+RANGE_STEP)),
        ]):
            ttk.Button(rr, text=label, command=cmd).pack(
                side=tk.RIGHT, padx=3)

        tk.Frame(ctrl, bg=BORDER, height=1).pack(
            fill=tk.X, padx=12, pady=4)

        ar = tk.Frame(ctrl, bg=BG2)
        ar.pack(fill=tk.X, padx=12, pady=(0, 10))
        ttk.Button(ar, text="Save Changes to JSON",
                   command=self._save_json).pack(side=tk.LEFT, padx=4)
        ttk.Button(ar, text="Duplicate Fight",
                   command=self._duplicate_fight).pack(side=tk.LEFT, padx=4)
        ttk.Button(ar, text="Cut Selected Fight",
                   command=self._cut_selected).pack(side=tk.LEFT, padx=4)
        tk.Frame(ar, bg=BG2, width=20).pack(side=tk.LEFT)
        self._del_btn = tk.Button(
            ar, text="Delete Fight",
            font=FONT, bg="#5a1a1a", fg="#ffaaaa",
            activebackground="#7a2a2a", activeforeground="#ffffff",
            relief="flat", bd=0, padx=10, pady=3,
            command=self._delete_fight)
        self._del_btn.pack(side=tk.LEFT, padx=4)
        ttk.Button(ar, text="Cut All Verified Fights",
                   command=self._cut_all).pack(side=tk.RIGHT, padx=4)

    def _make_preview(self, parent, title):
        outer = tk.Frame(parent, bg=BG2,
                         highlightbackground=BORDER, highlightthickness=1)
        outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        hdr = tk.Frame(outer, bg=BG3, height=26)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text=title, font=FONT_BOLD,
                 bg=BG3, fg=ACCENT2).pack(side=tk.LEFT, padx=10, pady=4)
        lbl = tk.Label(outer, text="No frame loaded",
                       bg="#111111", fg=TEXT_DIM, anchor=tk.CENTER)
        lbl.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        return lbl

    def _name_entry(self, parent, label):
        tk.Label(parent, text=label, bg=BG2, fg=TEXT_DIM,
                 font=FONT_BOLD).pack(side=tk.LEFT, padx=(0, 4))
        e = tk.Entry(parent, width=18, font=FONT_INPUTS,
                     bg=BG, fg=TEXT, insertbackground=TEXT,
                     relief="flat",
                     highlightbackground=BORDER,
                     highlightthickness=1, bd=4)
        e.pack(side=tk.LEFT, padx=(0, 10), ipady=4)
        return e

    def _build_slider_panel(self, parent, title, marker):
        f = tk.Frame(parent, bg=BG2)
        hdr = tk.Frame(f, bg=BG2)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text=title, font=FONT_BOLD,
                 bg=BG2, fg=TEXT).pack(side=tk.LEFT)
        time_lbl = tk.Label(hdr, text="--:--:--.--",
                            font=FONT_MONO, bg=BG2, fg=ACCENT2)
        time_lbl.pack(side=tk.RIGHT)
        slider = RangeSlider(
            f, on_change=lambda v, m=marker:
                self._on_slider_move(m, v))
        slider.pack(fill=tk.X, pady=4)
        f._slider   = slider
        f._time_lbl = time_lbl
        f._marker   = marker
        return f

    # ── scan tabs ───────────────────────────────────────────
    def _build_tachiai_tab(self):
        tk.Label(self.tachiai_tab,
                 text="Tachiai frames — single-click to quick-edit, "
                      "double-click to open in editor.",
                 font=("Segoe UI", 10, "italic"),
                 bg=BG, fg=TEXT_DIM).pack(anchor=tk.W, pady=(0, 6))
        self.tachiai_scroll = ScrollableFrame(self.tachiai_tab)
        self.tachiai_scroll.pack(fill=tk.BOTH, expand=True)

    def _build_ending_tab(self):
        tk.Label(self.ending_tab,
                 text="Ending frames — single-click to quick-edit, "
                      "double-click to open in editor.",
                 font=("Segoe UI", 10, "italic"),
                 bg=BG, fg=TEXT_DIM).pack(anchor=tk.W, pady=(0, 6))
        self.ending_scroll = ScrollableFrame(self.ending_tab)
        self.ending_scroll.pack(fill=tk.BOTH, expand=True)

    # ── torikumi tab ────────────────────────────────────────
    def _build_torikumi_tab(self):
        tt = self.torikumi_tab

        hdr = tk.Frame(tt, bg=BG2,
                       highlightbackground=BORDER, highlightthickness=1)
        hdr.pack(fill=tk.X, pady=(0, 6))

        self._torikumi_title = tk.Label(
            hdr, text="Torikumi", font=FONT_HEAD, bg=BG2, fg=ACCENT2)
        self._torikumi_title.pack(side=tk.LEFT, padx=12, pady=8)

        self._batch_upload_btn = ttk.Button(
            hdr, text="⚡ Cut & Upload All Matched",
            state="disabled", command=self._on_batch_upload_clicked)
        self._batch_upload_btn.pack(side=tk.RIGHT, padx=8, pady=6)

        ttk.Button(hdr, text="↺  Refresh",
                   command=self._fetch_torikumi).pack(
            side=tk.RIGHT, padx=8, pady=6)

        self._torikumi_status_lbl = tk.Label(
            hdr, textvariable=self._torikumi_status_var,
            font=("Segoe UI", 9, "italic"), bg=BG2, fg=TEXT_DIM)
        self._torikumi_status_lbl.pack(side=tk.RIGHT, padx=8)

        leg = tk.Frame(tt, bg=BG)
        leg.pack(fill=tk.X, pady=(0, 4))
        for color, label in (
                (ASSIGNED_BD,   "Full match"),
                (PARTIAL_BD,    "Partial match (one name)"),
                (FUSEN_BD,      "Fusen (walkover)"),
                (UNASSIGNED_BD, "Unassigned"),
        ):
            tk.Frame(leg, bg=color, width=12, height=12).pack(
                side=tk.LEFT, padx=(8, 3))
            tk.Label(leg, text=label, bg=BG, fg=TEXT_DIM,
                     font=("Segoe UI", 9)).pack(
                side=tk.LEFT, padx=(0, 14))

        col_hdr = tk.Frame(tt, bg=BG3,
                           highlightbackground=BORDER,
                           highlightthickness=1)
        col_hdr.pack(fill=tk.X)
        for text, width in (
                ("Division", 9), ("East (Rikishi 1)", 24),
                ("", 4), ("West (Rikishi 2)", 24),
                ("Kimarite", 12), ("Status", 12), ("Assigned fight", 24),
        ):
            tk.Label(col_hdr, text=text, bg=BG3, fg=TEXT_DIM,
                     font=FONT_BOLD, width=width, anchor="w"
                     ).pack(side=tk.LEFT, padx=4, pady=4)

        self.torikumi_scroll = ScrollableFrame(tt)
        self.torikumi_scroll.pack(fill=tk.BOTH, expand=True)

    # ============================================================
    # AUTHENTICATION
    # ============================================================
    def _on_login_clicked(self):
        email    = self.email_entry.get().strip()
        password = self.pass_entry.get().strip()
        if not email or not password or \
                email == "Email" or password == "Password":
            messagebox.showerror("Auth Error",
                                 "Please input a valid email and password.")
            return
        self.login_btn.config(state="disabled", text="Logging in...")
        threading.Thread(target=self._run_login_bg,
                         args=(email, password), daemon=True).start()

    def _run_login_bg(self, email, password):
        try:
            url     = f"{SUPABASE_URL}/auth/v1/token?grant_type=password"
            payload = json.dumps(
                {"email": email, "password": password}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, method="POST")
            req.add_header("apikey", SUPABASE_ANON_KEY)
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=12) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
            token      = res_data.get("access_token")
            user_email = res_data.get("user", {}).get("email")
            if token:
                self.after(0, lambda: self._on_login_success(
                    token, user_email))
            else:
                self.after(0, lambda: self._on_login_failed(
                    "Token missing from server response."))
        except Exception as e:
            self.after(0, lambda err=str(e): self._on_login_failed(err))

    def _on_login_success(self, token, email):
        self.access_token    = token
        self.logged_in_email = email
        self.auth_status_lbl.config(
            text=f"Logged in: {email}", fg="#44cc88")
        self.login_btn.config(state="normal", text="Log In")
        self._update_all_upload_ui()
        messagebox.showinfo("Authenticated",
                            f"Successfully logged into SumoStats as:\n{email}")

    def _on_login_failed(self, error_msg):
        self.access_token    = None
        self.logged_in_email = None
        self.auth_status_lbl.config(text="Authentication Failed",
                                    fg=RED_MARK)
        self.login_btn.config(state="normal", text="Log In")
        self._update_all_upload_ui()
        messagebox.showerror("Auth Failure",
                             f"Failed to log in:\n{error_msg}")

    def _update_all_upload_ui(self):
        logged_in = self.access_token is not None
        for row in self._torikumi_rows:
            row.update_upload_state(logged_in)
        self._batch_upload_btn.config(
            state="normal"
            if logged_in and len(self._torikumi_bouts) > 0
            else "disabled")

    # ============================================================
    # RANGE HELPERS
    # ============================================================
    def _range_text(self):
        def fmt(secs):
            m, s = divmod(secs, 60)
            return f"{m}m {s:02d}s" if m else f"{s}s"
        return (f"Window: −{fmt(self._range_lo)} before  /  "
                f"+{fmt(self._range_hi)} after  (both sliders)")

    def _shift_lo(self, delta):
        self._range_lo = max(RANGE_STEP, self._range_lo + delta)
        self._on_range_changed()

    def _shift_hi(self, delta):
        self._range_hi = max(RANGE_STEP, self._range_hi + delta)
        self._on_range_changed()

    def _on_range_changed(self):
        self._range_display.config(text=self._range_text())
        if self.current_fight_idx is not None:
            self._apply_range_to_sliders()
            self._refresh_marks()

    def _apply_range_to_sliders(self):
        fight   = self.json_data["fights"][self.current_fight_idx]
        start_s = fight.get("start_seconds") or 0.0
        end_s   = fight.get("end_seconds")   or 0.0
        vd      = self.video_duration
        for panel, anchor in ((self._start_panel, start_s),
                              (self._end_panel,   end_s)):
            lo = max(0.0, anchor - self._range_lo)
            hi = (min(vd, anchor + self._range_hi)
                  if vd else anchor + self._range_hi)
            panel._slider.set_range(lo, hi)
            panel._slider.set(panel._slider.get())

    def _refresh_marks(self):
        for panel in (self._start_panel, self._end_panel):
            panel._slider.set_marks(self._saved_start, self._saved_end)

    def _update_time_labels(self):
        if self.current_fight_idx is None:
            return
        fight = self.json_data["fights"][self.current_fight_idx]
        s   = fight.get("start_seconds")
        e   = fight.get("end_seconds")
        dur = (e - s) if (s is not None and e is not None) else None
        self._start_panel._time_lbl.config(
            text=(f"{fmt_time(s)}  ({s:.2f}s)"
                  if s is not None else "--"))
        self._end_panel._time_lbl.config(
            text=(f"{fmt_time(e)}  |  {fmt_dur(dur)}"
                  if e is not None else "--"))

    # ============================================================
    # DATA LOADING
    # ============================================================
    def _load_files(self):
        if not os.path.exists(JSON_FOLDER):
            messagebox.showerror(
                "Error", f"JSON folder missing:\n{JSON_FOLDER}")
            return
        self.json_files = sorted(
            f for f in os.listdir(JSON_FOLDER)
            if f.endswith("_fights.json"))
        if not self.json_files:
            messagebox.showinfo("No files",
                                "No *_fights.json files found.")
            return
        self.files_combo["values"] = self.json_files
        self.files_combo.current(0)
        self._on_json_selected()

    def _on_json_selected(self, _=None):
        self._stop_all_bg()
        fname = self.files_combo.get()
        self.current_json_path = os.path.join(JSON_FOLDER, fname)
        stem = fname.replace("_fights.json", "")

        self.current_video_path = None
        for ext in VIDEO_EXTENSIONS:
            c = os.path.join(VIDEO_FOLDER, stem + ext)
            if os.path.exists(c):
                self.current_video_path = c
                break

        with self.cap_lock:
            if self.cap:
                self.cap.release()
                self.cap = None
            if self.current_video_path:
                self.cap = cv2.VideoCapture(self.current_video_path)
                fps = self.cap.get(cv2.CAP_PROP_FPS) or 30
                fc  = self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
                self.video_duration = fc / fps

        with open(self.current_json_path, encoding="utf-8") as fh:
            self.json_data = json.load(fh)

        # Flag FIRST, then fill in default timestamps
        self._mark_missing_starts()
        self._apply_missing_start_defaults()

        self.current_fight_idx = None

        basho_id, day = parse_basho_day(fname)
        if basho_id != self._torikumi_basho_id or \
                day != self._torikumi_day:
            self._torikumi_bouts  = []
            self._torikumi_assign = {}
            self._fight_to_bout   = {}
            self._torikumi_basho_id = basho_id
            self._torikumi_day      = day

        self._populate_list()
        self._reload_active_scan_tab()

    def _mark_missing_starts(self):
        """
        STEP 1 — inspects raw start_seconds values (before any
        default is applied) and stamps start_missing=True/False.
        """
        changed = False
        for fight in self.json_data.get("fights", []):
            s       = fight.get("start_seconds")
            missing = (s is None or s == 0)
            if fight.get("start_missing") != missing:
                fight["start_missing"] = missing
                changed = True
        if changed and self.current_json_path:
            with open(self.current_json_path, "w",
                      encoding="utf-8") as fh:
                json.dump(self.json_data, fh,
                          indent=2, ensure_ascii=False)

    def _apply_missing_start_defaults(self):
        """
        STEP 2 — gives missing starts a workable anchor of
        max(0, end − 30).  Does NOT clear start_missing.
        """
        changed = False
        for fight in self.json_data.get("fights", []):
            if fight.get("start_missing"):
                e             = fight.get("end_seconds") or 0.0
                default_start = max(0.0, e - 30.0)
                if fight.get("start_seconds") != default_start:
                    fight["start_seconds"] = round(default_start, 3)
                    fight["start_hms"]     = fmt_time(default_start)
                    changed = True
        if changed and self.current_json_path:
            with open(self.current_json_path, "w",
                      encoding="utf-8") as fh:
                json.dump(self.json_data, fh,
                          indent=2, ensure_ascii=False)

    def _populate_list(self):
        self.fights_lb.delete(0, tk.END)
        for f in self.json_data.get("fights", []):
            n     = f["fight_number"]
            f1    = f.get("fighter1") or "?"
            f2    = f.get("fighter2") or "?"
            badge = (" [FLAG]" if f.get("flagged")
                     else " [NAME]" if f.get("names_flagged") else "")
            self.fights_lb.insert(
                tk.END, f"  {n:02d}  {f1} vs {f2}{badge}")

        fights = self.json_data.get("fights", [])
        if fights:
            idx = min(self.current_fight_idx or 0, len(fights) - 1)
            self.fights_lb.selection_set(idx)
            self._on_fight_selected()

    def _on_fight_selected(self, _=None):
        sel = self.fights_lb.curselection()
        if not sel:
            return
        self.current_fight_idx = sel[0]
        self._range_lo = DEFAULT_LO
        self._range_hi = DEFAULT_HI
        self._range_display.config(text=self._range_text())
        self._load_fight_into_ui()

    def _load_fight_into_ui(self):
        fight = self.json_data["fights"][self.current_fight_idx]

        self.f1_entry.delete(0, tk.END)
        self.f1_entry.insert(0, fight.get("fighter1") or "")
        self.f2_entry.delete(0, tk.END)
        self.f2_entry.insert(0, fight.get("fighter2") or "")

        self._clear_ocr_suggestions()
        self._rebuild_uncertain_buttons(fight)
        self._rebuild_alt_peak_buttons(fight)

        start_s = fight.get("start_seconds") or 0.0
        end_s   = fight.get("end_seconds")   or 0.0
        self._saved_start = start_s
        self._saved_end   = end_s

        vd = self.video_duration
        for panel, anchor in ((self._start_panel, start_s),
                              (self._end_panel,   end_s)):
            lo = max(0.0, anchor - self._range_lo)
            hi = (min(vd, anchor + self._range_hi)
                  if vd else anchor + self._range_hi)
            panel._slider.set_range(lo, hi)
            panel._slider.set(anchor)

        self._refresh_marks()
        self._update_time_labels()
        self._render_previews()

    # ============================================================
    # UNCERTAIN NAME SUGGESTIONS
    # ============================================================
    def _rebuild_uncertain_buttons(self, fight):
        for w in self._uncertain_row.winfo_children():
            w.destroy()
        self._uncertain_btns.clear()
        uncertain = fight.get("uncertain_names", [])
        if not uncertain:
            return
        tk.Label(self._uncertain_row, text="Uncertain names:",
                 bg=BG2, fg=TEXT_DIM, font=FONT_BOLD
                 ).pack(side=tk.LEFT, padx=(0, 8))
        for name in uncertain:
            btn = tk.Button(
                self._uncertain_row, text=name, font=FONT_SUGGEST,
                bg=BG3, fg="#ffb300",
                activebackground=BG4, activeforeground=TEXT,
                relief="flat", bd=1, cursor="hand2", padx=10, pady=2,
                command=lambda n=name: self._apply_uncertain_name(n))
            btn.pack(side=tk.LEFT, padx=4)
            self._uncertain_btns.append(btn)

    def _apply_uncertain_name(self, name: str):
        f1_val = self.f1_entry.get().strip()
        f2_val = self.f2_entry.get().strip()
        if not f1_val:
            self.f1_entry.delete(0, tk.END)
            self.f1_entry.insert(0, name)
            self._save_json()
        elif not f2_val:
            self.f2_entry.delete(0, tk.END)
            self.f2_entry.insert(0, name)
            self._save_json()

    # ============================================================
    # ALTERNATIVE PEAK BUTTONS
    # ============================================================
    def _rebuild_alt_peak_buttons(self, fight):
        for w in self._alt_peak_frame.winfo_children():
            w.destroy()
        self._alt_peak_btns.clear()
        for peak_s in fight.get("alt_peak_seconds", []):
            label = fmt_time(peak_s)
            btn = tk.Button(
                self._alt_peak_frame, text=label, font=FONT_SUGGEST,
                bg=BG3, fg="#7eb8ff",
                activebackground=BG4, activeforeground=TEXT,
                relief="flat", bd=1, cursor="hand2", padx=10, pady=2,
                command=lambda s=peak_s: self._apply_alt_peak(s))
            btn.pack(side=tk.LEFT, padx=4)
            self._alt_peak_btns.append(btn)

    def _apply_alt_peak(self, peak_s: float):
        if self.current_fight_idx is None:
            return
        fight = self.json_data["fights"][self.current_fight_idx]
        fight["start_seconds"] = round(peak_s, 3)
        fight["start_hms"]     = fmt_time(peak_s)

        vd = self.video_duration
        lo = max(0.0, peak_s - self._range_lo)
        hi = (min(vd, peak_s + self._range_hi)
              if vd else peak_s + self._range_hi)
        self._start_panel._slider.set_range(lo, hi)
        self._start_panel._slider.set(peak_s)
        self._update_time_labels()

        img = self._frame_at(peak_s)
        if img:
            ph = ImageTk.PhotoImage(img)
            self.start_lbl.config(image=ph, text="")
            self.start_img_tk = ph

        self._save_json()
        for btn in self._alt_peak_btns:
            btn.config(fg="#44cc44"
                       if btn.cget("text") == fmt_time(peak_s)
                       else "#7eb8ff")

    # ============================================================
    # VIDEO PREVIEW
    # ============================================================
    def _frame_at(self, seconds, w=560, h=315):
        if seconds is None:
            return None
        with self.cap_lock:
            if not self.cap:
                return None
            fps = self.cap.get(cv2.CAP_PROP_FPS) or 30
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, int(seconds * fps))
            ret, frame = self.cap.read()
        if not ret or frame is None:
            return None
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        img.thumbnail((w, h), Image.LANCZOS)
        return img

    def _render_previews(self):
        fight = self.json_data["fights"][self.current_fight_idx]

        def show(lbl, sec, attr):
            img = self._frame_at(sec)
            if img:
                ph = ImageTk.PhotoImage(img)
                lbl.config(image=ph, text="")
                setattr(self, attr, ph)
            else:
                lbl.config(image="",
                           text="No frame" if not self.cap
                           else "Could not decode")

        show(self.start_lbl, fight.get("start_seconds"), "start_img_tk")
        show(self.end_lbl,   fight.get("end_seconds"),   "end_img_tk")

    # ============================================================
    # SLIDER EVENTS
    # ============================================================
    def _on_slider_move(self, marker, value):
        if self.current_fight_idx is None:
            return
        fight = self.json_data["fights"][self.current_fight_idx]
        key_s = "start_seconds" if marker == "start" else "end_seconds"
        key_h = "start_hms"     if marker == "start" else "end_hms"
        fight[key_s] = round(value, 3)
        fight[key_h] = fmt_time(value)
        self._update_time_labels()

        lbl  = self.start_lbl    if marker == "start" else self.end_lbl
        attr = "start_img_tk"   if marker == "start" else "end_img_tk"
        img  = self._frame_at(value)
        if img:
            ph = ImageTk.PhotoImage(img)
            lbl.config(image=ph, text="")
            setattr(self, attr, ph)

    # ============================================================
    # OCR
    # ============================================================
    def _run_ocr(self, marker):
        if self.current_fight_idx is None:
            return
        fight = self.json_data["fights"][self.current_fight_idx]
        sec   = fight.get(
            "start_seconds" if marker == "start" else "end_seconds")
        if sec is None:
            messagebox.showerror("OCR", "No timestamp for this frame.")
            return
        with self.cap_lock:
            if not self.cap:
                return
            fps = self.cap.get(cv2.CAP_PROP_FPS) or 30
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, int(sec * fps))
            ret, frame = self.cap.read()
        if not ret or frame is None:
            messagebox.showerror(
                "OCR", f"Could not decode frame at {sec:.2f}s.")
            return

        def ocr_crop(region):
            x1, y1, x2, y2 = region
            results = self.ocr_reader.readtext(
                frame[y1:y2, x1:x2], detail=1)
            parts = [t for _, t, c in results
                     if c >= OCR_MIN_CONFIDENCE and t.strip()]
            return " ".join(parts).strip() or None

        f1, f2 = ocr_crop(REGION_F1), ocr_crop(REGION_F2)
        if f1 or f2:
            self.latest_ocr_f1 = f1 or ""
            self.latest_ocr_f2 = f2 or ""
            self.f1_sug_btn.config(
                text=f" Apply: {f1} " if f1 else "[ None ]",
                fg="#ffb300" if f1 else TEXT_DIM, bg=BG3)
            self.f2_sug_btn.config(
                text=f" Apply: {f2} " if f2 else "[ None ]",
                fg="#ffb300" if f2 else TEXT_DIM, bg=BG3)
        else:
            self._clear_ocr_suggestions()
            messagebox.showinfo("OCR Status",
                                "No names detected in either field.")

    def _clear_ocr_suggestions(self):
        self.latest_ocr_f1 = ""
        self.latest_ocr_f2 = ""
        self.f1_sug_btn.config(text="[ None ]", fg=TEXT_DIM, bg=BG3)
        self.f2_sug_btn.config(text="[ None ]", fg=TEXT_DIM, bg=BG3)

    def _apply_f1_ocr_suggestion(self):
        if self.latest_ocr_f1 and self.current_fight_idx is not None:
            self.f1_entry.delete(0, tk.END)
            self.f1_entry.insert(0, self.latest_ocr_f1)
            self.f1_sug_btn.config(fg="#44cc44")
            self._save_json()

    def _apply_f2_ocr_suggestion(self):
        if self.latest_ocr_f2 and self.current_fight_idx is not None:
            self.f2_entry.delete(0, tk.END)
            self.f2_entry.insert(0, self.latest_ocr_f2)
            self.f2_sug_btn.config(fg="#44cc44")
            self._save_json()

    # ============================================================
    # SAVE
    # ============================================================
    def _save_json(self):
        if self.current_fight_idx is None:
            return
        fight = self.json_data["fights"][self.current_fight_idx]
        fight["fighter1"] = self.f1_entry.get().strip() or None
        fight["fighter2"] = self.f2_entry.get().strip() or None

        with open(self.current_json_path, "w", encoding="utf-8") as fh:
            json.dump(self.json_data, fh, indent=2, ensure_ascii=False)

        self._saved_start = fight.get("start_seconds")
        self._saved_end   = fight.get("end_seconds")
        self._refresh_marks()

        idx = self.current_fight_idx
        self._populate_list()
        self.fights_lb.selection_set(idx)

    def _write_json_only(self):
        """Persist json_data without touching editor UI state."""
        with open(self.current_json_path, "w", encoding="utf-8") as fh:
            json.dump(self.json_data, fh, indent=2, ensure_ascii=False)

    # ============================================================
    # SCAN QUICK-EDIT SAVE CALLBACK
    # ============================================================
    def _scan_quick_save(self, fight_idx: int, field_s: str,
                         new_value: float):
        self._write_json_only()
        if self.current_fight_idx == fight_idx:
            fight = self.json_data["fights"][fight_idx]
            self._saved_start = fight.get("start_seconds")
            self._saved_end   = fight.get("end_seconds")
            self._refresh_marks()
            self._update_time_labels()

    # ============================================================
    # DELETE
    # ============================================================
    def _delete_fight(self):
        if self.current_fight_idx is None:
            return
        fight = self.json_data["fights"][self.current_fight_idx]
        f1  = fight.get("fighter1") or "?"
        f2  = fight.get("fighter2") or "?"
        num = fight["fight_number"]
        if not messagebox.askyesno(
                "Delete Fight",
                f"Permanently delete Fight #{num:02d}:\n{f1} vs {f2}\n\n"
                "This cannot be undone.", icon="warning"):
            return
        del self.json_data["fights"][self.current_fight_idx]
        for i, f in enumerate(self.json_data["fights"]):
            f["fight_number"] = i + 1
        self.json_data["total_fights"] = len(self.json_data["fights"])
        with open(self.current_json_path, "w", encoding="utf-8") as fh:
            json.dump(self.json_data, fh, indent=2, ensure_ascii=False)
        new_idx = max(0, self.current_fight_idx - 1)
        self.current_fight_idx = (
            new_idx if self.json_data["fights"] else None)
        self._populate_list()
        if self.json_data["fights"]:
            self.fights_lb.selection_clear(0, tk.END)
            self.fights_lb.selection_set(new_idx)
            self.fights_lb.see(new_idx)

    # ============================================================
    # DUPLICATE
    # ============================================================
    def _duplicate_fight(self):
        if self.current_fight_idx is None:
            return
        src   = self.json_data["fights"][self.current_fight_idx]
        clone = {k: src.get(k) for k in src}
        self.json_data["fights"].insert(
            self.current_fight_idx + 1, clone)
        for i, f in enumerate(self.json_data["fights"]):
            f["fight_number"] = i + 1
        self.json_data["total_fights"] = len(self.json_data["fights"])
        with open(self.current_json_path, "w", encoding="utf-8") as fh:
            json.dump(self.json_data, fh, indent=2, ensure_ascii=False)
        target = self.current_fight_idx + 1
        self._populate_list()
        self.fights_lb.selection_clear(0, tk.END)
        self.fights_lb.selection_set(target)
        self.fights_lb.see(target)
        self._on_fight_selected()
        messagebox.showinfo("Duplicated",
                            f"Created copy as Fight #{target + 1:02d}")

    # ============================================================
    # CUT
    # ============================================================
    def _ffmpeg_cut(self, start, end, out_name):
        os.makedirs(CUT_OUTPUT_DIR, exist_ok=True)
        out = os.path.join(CUT_OUTPUT_DIR, out_name)
        cmd = ["ffmpeg", "-y",
               "-ss", str(start), "-to", str(end),
               "-i", self.current_video_path,
               "-c", "copy", out]
        r = subprocess.run(cmd, capture_output=True)
        return r.returncode == 0, out

    def _cut_selected(self):
        if self.current_fight_idx is None or not self.current_video_path:
            return
        self._save_json()
        fight = self.json_data["fights"][self.current_fight_idx]
        s, e  = fight.get("start_seconds"), fight.get("end_seconds")
        if s is None or e is None:
            messagebox.showerror("Cut", "Start or end is null.")
            return
        out_name = build_output_name(self.current_video_path, fight)
        ok, path = self._ffmpeg_cut(s, e, out_name)
        if ok:
            messagebox.showinfo("Done", f"Saved to:\n{path}")
        else:
            messagebox.showerror("ffmpeg error", "Cut failed.")

    def _cut_all(self):
        if not self.current_video_path:
            return
        self._save_json()
        fights    = self.json_data.get("fights", [])
        ok_count  = 0
        for fight in fights:
            s, e = fight.get("start_seconds"), fight.get("end_seconds")
            if s is None or e is None:
                continue
            out_name = build_output_name(self.current_video_path, fight)
            ok, _    = self._ffmpeg_cut(s, e, out_name)
            if ok:
                ok_count += 1
        messagebox.showinfo("All Cuts Done",
                            f"{ok_count}/{len(fights)} fights cut to:\n"
                            f"{CUT_OUTPUT_DIR}")

    # ============================================================
    # TAB SWITCHING
    # ============================================================
    def _on_tab_changed(self, _=None):
        self._reload_active_scan_tab()

    def _reload_active_scan_tab(self):
        tab = self.notebook.tab(
            self.notebook.select(), "text").strip()
        if tab == "Tachiai Scan":
            self._load_tachiai_grid()
        elif tab == "Ending Scan":
            self._load_ending_grid()
        elif tab == "Torikumi":
            self._load_torikumi_tab()

    # ============================================================
    # BACKGROUND SCAN WORKER
    # ============================================================
    def _scan_worker(self, path, fights, frame_key, stop_evt, q):
        if not path or not os.path.exists(path):
            return
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        for idx, fight in enumerate(fights):
            if stop_evt.is_set():
                break
            sec = fight.get(frame_key)
            if sec is None:
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(sec * fps))
            ret, frame = cap.read()
            if ret and frame is not None:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(rgb).resize(
                    (THUMB_W, THUMB_H), Image.LANCZOS)
                q.put((idx, img))
        cap.release()

    # ── tachiai ─────────────────────────────────────────────
    def _load_tachiai_grid(self):
        self._stop_bg(self.stop_tachiai, self.tachiai_thread)
        self.stop_tachiai.clear()
        fights = self.json_data.get("fights", [])
        build_scan_grid(
            self.tachiai_scroll, fights, "start_seconds",
            self.tachiai_widgets,
            jump_callback=self._jump_to_fight,
            select_callback=lambda i: self._on_scan_select(
                i, "start_seconds", self._tachiai_qe),
        )
        self.tachiai_thread = threading.Thread(
            target=self._scan_worker,
            args=(self.current_video_path, fights, "start_seconds",
                  self.stop_tachiai, self.tachiai_queue),
            daemon=True)
        self.tachiai_thread.start()
        self.after(80, self._drain_tachiai)

    def _drain_tachiai(self):
        self._drain(self.tachiai_queue, self.tachiai_widgets)
        if not self.stop_tachiai.is_set():
            self.after(100, self._drain_tachiai)

    # ── ending ──────────────────────────────────────────────
    def _load_ending_grid(self):
        self._stop_bg(self.stop_ending, self.ending_thread)
        self.stop_ending.clear()
        fights = self.json_data.get("fights", [])
        build_scan_grid(
            self.ending_scroll, fights, "end_seconds",
            self.ending_widgets,
            jump_callback=self._jump_to_fight,
            select_callback=lambda i: self._on_scan_select(
                i, "end_seconds", self._ending_qe),
        )
        self.ending_thread = threading.Thread(
            target=self._scan_worker,
            args=(self.current_video_path, fights, "end_seconds",
                  self.stop_ending, self.ending_queue),
            daemon=True)
        self.ending_thread.start()
        self.after(80, self._drain_ending)

    def _drain_ending(self):
        self._drain(self.ending_queue, self.ending_widgets)
        if not self.stop_ending.is_set():
            self.after(100, self._drain_ending)

    # ── shared drain ────────────────────────────────────────
    def _drain(self, q, widgets):
        try:
            while True:
                idx, img = q.get_nowait()
                if idx in widgets:
                    lbl = widgets[idx]
                    ph  = ImageTk.PhotoImage(img)
                    lbl.config(image=ph, text="",
                               width=THUMB_W, height=THUMB_H)
                    lbl.image = ph
                q.task_done()
        except queue.Empty:
            pass

    # ── stop helpers ────────────────────────────────────────
    def _stop_bg(self, stop_evt, thread):
        stop_evt.set()
        if thread and thread.is_alive():
            thread.join(timeout=1.5)

    def _stop_all_bg(self):
        self._stop_bg(self.stop_tachiai, self.tachiai_thread)
        self._stop_bg(self.stop_ending,  self.ending_thread)
        self.stop_tachiai.clear()
        self.stop_ending.clear()

    def _jump_to_fight(self, idx):
        self._stop_all_bg()
        self.fights_lb.selection_clear(0, tk.END)
        self.fights_lb.selection_set(idx)
        self.fights_lb.see(idx)
        self._on_fight_selected()
        self.notebook.select(self.editor_tab)

    def _on_scan_select(self, idx: int, frame_field: str,
                        window: ScanQuickEditWindow):
        fights = self.json_data.get("fights", [])
        if idx >= len(fights):
            return
        window.load(idx, fights[idx], self.cap, self.cap_lock,
                    frame_field)

    # ============================================================
    # TORIKUMI
    # ============================================================
    def _load_torikumi_tab(self):
        self._fetch_torikumi()

    def _fetch_torikumi(self):
        basho_id = self._torikumi_basho_id
        day      = self._torikumi_day

        if not basho_id or not day:
            self._torikumi_status_var.set(
                "Cannot determine basho/day from filename.")
            self._render_torikumi_empty(
                "Filename not recognised as YYYY-BashoName-DayN.")
            return

        self._torikumi_status_var.set(
            f"Fetching {basho_id} day {day}…")
        self._torikumi_title.config(
            text=f"Torikumi — {basho_id}  Day {day}")

        for w in self.torikumi_scroll.inner.winfo_children():
            w.destroy()
        self._torikumi_rows.clear()

        def worker():
            try:
                bouts = fetch_torikumi(basho_id, day)
                self.after(0, lambda: self._on_torikumi_fetched(bouts))
            except Exception as exc:
                msg = str(exc)
                self.after(0, lambda: self._on_torikumi_error(msg))

        self._torikumi_thread = threading.Thread(
            target=worker, daemon=True)
        self._torikumi_thread.start()

    def _on_torikumi_fetched(self, bouts: list[dict]):
        self._torikumi_bouts = bouts
        self._torikumi_status_var.set(f"{len(bouts)} bouts fetched.")
        if bouts:
            print("\n" + "=" * 60)
            print("[API DIAGNOSTIC] First bout received from SumoStats:")
            for key, val in bouts[0].items():
                print(f"  {key}: {val}")
            print("=" * 60 + "\n")
        self._run_auto_match()
        self._render_torikumi()

    def _on_torikumi_error(self, msg: str):
        self._torikumi_status_var.set(f"Error: {msg}")
        self._render_torikumi_empty(f"API error:\n{msg}")

    def _fight_label(self, fight: dict) -> str:
        n  = fight.get("fight_number", 0)
        f1 = fight.get("fighter1") or "?"
        f2 = fight.get("fighter2") or "?"
        return f"#{n:02d}  {f1} vs {f2}"

    def _run_auto_match(self):
        fights = self.json_data.get("fights", [])
        bouts  = self._torikumi_bouts

        candidates = []
        for bout in bouts:
            bout_id = bout.get("id")
            for fidx, fight in enumerate(fights):
                matched_bout, score, partial = match_fight_to_bout(
                    fight, [bout])
                if matched_bout is not None:
                    candidates.append((score, partial, bout_id, fidx))

        candidates.sort(key=lambda x: -x[0])

        assigned_fights: set[int] = set()
        assigned_bouts:  set      = set()
        new_assign: dict[int, int | None] = {
            b.get("id"): None for b in bouts}
        self._fight_to_bout = {}

        for score, partial, bout_id, fidx in candidates:
            if bout_id in assigned_bouts or fidx in assigned_fights:
                continue
            fight = fights[fidx]
            fnum  = fight["fight_number"]
            new_assign[bout_id]      = fnum
            self._fight_to_bout[fnum] = bout_id
            assigned_fights.add(fidx)
            assigned_bouts.add(bout_id)

        self._torikumi_assign = new_assign

    def _render_torikumi_empty(self, msg: str):
        for w in self.torikumi_scroll.inner.winfo_children():
            w.destroy()
        self._torikumi_rows.clear()
        tk.Label(self.torikumi_scroll.inner,
                 text=msg, bg=BG, fg=TEXT_DIM,
                 font=FONT_HEAD, wraplength=600,
                 justify="center").pack(pady=40)

    def _render_torikumi(self):
        for w in self.torikumi_scroll.inner.winfo_children():
            w.destroy()
        self._torikumi_rows.clear()

        bouts  = self._torikumi_bouts
        fights = self.json_data.get("fights", [])

        if not bouts:
            self._render_torikumi_empty(
                "No bouts returned by the API.")
            return

        fight_options = [
            (f["fight_number"], self._fight_label(f)) for f in fights]

        score_cache: dict[int, tuple[float, bool]] = {}
        for bout in bouts:
            bout_id = bout.get("id")
            fnum    = self._torikumi_assign.get(bout_id)
            if fnum is not None:
                fight = next(
                    (f for f in fights if f["fight_number"] == fnum),
                    None)
                if fight:
                    _, score, partial = match_fight_to_bout(
                        fight, [bout])
                    score_cache[bout_id] = (score, partial)

        for bout in bouts:
            bout_id = bout.get("id")
            row = TorikumiRow(
                self.torikumi_scroll.inner,
                bout=bout,
                fight_options=fight_options,
                on_assign=self._on_manual_assign,
                on_upload=self._on_upload_request,
                scroll_frame=self.torikumi_scroll,
            )
            row.pack(fill=tk.X, padx=6, pady=2)
            self._torikumi_rows.append(row)

            fnum = self._torikumi_assign.get(bout_id)
            if fnum is not None:
                label = next(
                    (txt for num, txt in fight_options if num == fnum),
                    None)
                sc, pt = score_cache.get(bout_id, (0.0, False))
                row.set_assignment(label, pt, sc)
            else:
                row.set_assignment(None, False, 0.0)

        self._update_all_upload_ui()
        self._update_summary_stats()

    def _update_summary_stats(self):
        total_bouts    = len(self._torikumi_bouts)
        assigned_bouts = sum(
            1 for v in self._torikumi_assign.values() if v is not None)
        total_fights   = len(self.json_data.get("fights", []))
        matched_fights = len(
            set(v for v in self._torikumi_assign.values()
                if v is not None))
        self._torikumi_status_var.set(
            f"Fights Matched: {matched_fights}/{total_fights}  ·  "
            f"Bouts Assigned: {assigned_bouts}/{total_bouts}")

    def _on_manual_assign(self, bout_id, fight_num_or_none):
        old_fnum = self._torikumi_assign.get(bout_id)
        if old_fnum and old_fnum in self._fight_to_bout:
            del self._fight_to_bout[old_fnum]

        if fight_num_or_none is not None:
            for bid, fnum in list(self._torikumi_assign.items()):
                if fnum == fight_num_or_none and bid != bout_id:
                    self._torikumi_assign[bid] = None
                    self._update_row_widget(bid, None, False, 0.0)

        self._torikumi_assign[bout_id] = fight_num_or_none
        if fight_num_or_none is not None:
            self._fight_to_bout[fight_num_or_none] = bout_id

        if fight_num_or_none is not None:
            fights = self.json_data.get("fights", [])
            bout   = next(
                (b for b in self._torikumi_bouts
                 if b.get("id") == bout_id), None)
            fight  = next(
                (f for f in fights
                 if f["fight_number"] == fight_num_or_none), None)
            if bout and fight:
                _, score, partial = match_fight_to_bout(fight, [bout])
                self._update_row_widget(
                    bout_id, self._fight_label(fight), partial, score)
            else:
                self._update_row_widget(
                    bout_id, "— none —", True, 0.0)
        else:
            self._update_row_widget(bout_id, None, False, 0.0)

        self._update_summary_stats()

    def _update_row_widget(self, bout_id, label, partial, score):
        for row, bout in zip(
                self._torikumi_rows, self._torikumi_bouts):
            if bout.get("id") == bout_id:
                row.set_assignment(label, partial, score)
                row.update_upload_state(self.access_token is not None)
                return

    # ============================================================
    # UPLOAD
    # ============================================================
    def _get_cut_file_path(self, fight_num: int):
        for fight in self.json_data.get("fights", []):
            if fight.get("fight_number") == fight_num:
                out_name = build_output_name(
                    self.current_video_path, fight)
                return os.path.join(CUT_OUTPUT_DIR, out_name), fight
        return None, None

    def _on_upload_request(self, bout_id, fight_num, row_widget):
        if not self.access_token:
            messagebox.showerror("Auth Error",
                                 "You must log in to upload videos.")
            return
        row_widget.update_upload_state(True, "Verifying clip...")
        threading.Thread(
            target=self._run_upload_flow_bg,
            args=(bout_id, fight_num, row_widget),
            daemon=True).start()

    def _run_upload_flow_bg(self, bout_id, fight_num, row_widget):
        try:
            file_path, fight = self._get_cut_file_path(fight_num)
            if not file_path or not fight:
                raise ValueError(
                    "Localized file path configurations missing.")

            if not os.path.exists(file_path):
                self.after(0, lambda: row_widget.update_upload_state(
                    True, "Cutting clip..."))
                s = fight.get("start_seconds")
                e = fight.get("end_seconds")
                if s is None or e is None:
                    raise ValueError(
                        "Cut timestamps are incomplete or missing.")
                ok, _ = self._ffmpeg_cut(
                    s, e, os.path.basename(file_path))
                if not ok:
                    raise RuntimeError(
                        "FFmpeg failed on-the-fly execution.")

            self.after(0, lambda: row_widget.update_upload_state(
                True, "Reading data..."))
            with open(file_path, "rb") as f:
                file_bytes = f.read()

            if len(file_bytes) > 200 * 1024 * 1024:
                raise ValueError(
                    "File exceeds the SumoStats maximum of 200MB.")

            bout = next(
                (b for b in self._torikumi_bouts
                 if b.get("id") == bout_id), None)
            if not bout:
                raise ValueError(
                    "Bout reference missing from torikumi cache.")

            self.after(0, lambda: row_widget.update_upload_state(
                True, "Obtaining intent..."))
            intent_url    = f"{SUMOSTATS_API}/api/v1/videos/upload-intent"
            payload_data  = {
                "result_id":   int(bout_id),
                "basho_id":    int(self._torikumi_basho_id),
                "day":         int(self._torikumi_day),
                "rikishi1_id": int(bout.get("rikishi1_id")),
                "rikishi2_id": int(bout.get("rikishi2_id")),
                "source":      "COMMUNITY",
            }
            intent_payload = json.dumps(payload_data).encode("utf-8")
            req = urllib.request.Request(
                intent_url, data=intent_payload, method="POST")
            req.add_header("Authorization",
                           f"Bearer {self.access_token}")
            req.add_header("Content-Type", "application/json")

            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    intent_res = json.loads(
                        resp.read().decode("utf-8"))
            except urllib.error.HTTPError as he:
                error_body   = he.read().decode("utf-8", errors="ignore")
                try:
                    parsed_err   = json.loads(error_body)
                    detailed_msg = (parsed_err.get("message")
                                    or parsed_err.get("error")
                                    or error_body)
                except Exception:
                    detailed_msg = error_body or he.reason
                raise RuntimeError(
                    f"Server Intent Failure: {detailed_msg}")

            upload_id  = intent_res.get("uploadId")
            upload_url = intent_res.get("uploadUrl")
            if not upload_id or not upload_url:
                raise ValueError(
                    "Server declined to release upload keys.")

            self.after(0, lambda: row_widget.update_upload_state(
                True, "Uploading to R2..."))
            put_req = urllib.request.Request(
                upload_url, data=file_bytes, method="PUT")
            put_req.add_header("Content-Type", "video/mp4")
            with urllib.request.urlopen(
                    put_req, timeout=240) as put_resp:
                if put_resp.status not in (200, 201, 204):
                    raise RuntimeError(
                        f"R2 aborted. Code: {put_resp.status}")

            self.after(0, lambda: row_widget.update_upload_state(
                True, "Validating..."))
            confirm_url     = f"{SUMOSTATS_API}/api/v1/videos/confirm"
            confirm_payload = json.dumps(
                {"uploadId": int(upload_id)}).encode("utf-8")
            conf_req = urllib.request.Request(
                confirm_url, data=confirm_payload, method="POST")
            conf_req.add_header("Authorization",
                                f"Bearer {self.access_token}")
            conf_req.add_header("Content-Type", "application/json")

            try:
                with urllib.request.urlopen(
                        conf_req, timeout=15) as conf_resp:
                    conf_res = json.loads(
                        conf_resp.read().decode("utf-8"))
            except urllib.error.HTTPError as he:
                error_body   = he.read().decode("utf-8", errors="ignore")
                try:
                    parsed_err   = json.loads(error_body)
                    detailed_msg = (parsed_err.get("message")
                                    or parsed_err.get("error")
                                    or error_body)
                except Exception:
                    detailed_msg = error_body or he.reason
                raise RuntimeError(
                    f"Server Confirmation Failure: {detailed_msg}")

            status = conf_res.get("status") or "unknown"
            self.after(0, lambda: row_widget.update_upload_state(
                True, f"Success ({status})"))
            return True

        except Exception as e:
            self.after(0,
                       lambda err=str(e):
                       row_widget.update_upload_state(
                           True, f"Error: {err}"))
            return False

    # ============================================================
    # BATCH UPLOAD
    # ============================================================
    def _on_batch_upload_clicked(self):
        if not self.access_token:
            messagebox.showerror("Auth Error",
                                 "Please authenticate before bulk uploads.")
            return

        matched_items = []
        for row in self._torikumi_rows:
            assigned_val = row._var.get()
            if assigned_val and assigned_val != "— none —":
                fight_num = next(
                    (num for num, txt in row._fight_options
                     if txt == assigned_val), None)
                if fight_num is not None:
                    matched_items.append(
                        (row._bout.get("id"), fight_num, row))

        if not matched_items:
            messagebox.showinfo(
                "No Matches",
                "No fights assigned to Torikumi bouts yet.")
            return

        if not messagebox.askyesno(
                "Launch Batch",
                f"Cut and upload all matched fights?\n\n"
                f"Matches found: {len(matched_items)}\n"
                "Cuts processed on-the-fly and streamed to SumoStats.",
                icon="question"):
            return

        self._batch_upload_btn.config(
            state="disabled", text="⚡ Batch Running...")
        threading.Thread(
            target=self._run_batch_worker,
            args=(matched_items,), daemon=True).start()

    def _run_batch_worker(self, matched_items):
        import concurrent.futures

        def process_item(item):
            bout_id, fight_num, row_widget = item
            self.after(0, lambda rw=row_widget:
                       rw.update_upload_state(True, "Starting..."))
            return self._run_upload_flow_bg(
                bout_id, fight_num, row_widget)

        with concurrent.futures.ThreadPoolExecutor(
                max_workers=15) as executor:
            results = list(executor.map(process_item, matched_items))

        success_count = sum(1 for r in results if r)
        failure_count = len(results) - success_count

        self.after(0, lambda: self._batch_upload_btn.config(
            state="normal", text="⚡ Cut & Upload All Matched"))

        msg = (f"Batch complete.\n\n"
               f"Uploaded: {success_count}\nFailures: {failure_count}")
        if failure_count > 0:
            self.after(0, lambda: messagebox.showwarning(
                "Batch Summary", msg))
        else:
            self.after(0, lambda: messagebox.showinfo(
                "Batch Summary", msg))


# ============================================================
if __name__ == "__main__":
    app = SumoVerifierApp()

    def _on_close():
        app._stop_all_bg()
        with app.cap_lock:
            if app.cap:
                app.cap.release()
        app.destroy()

    app.protocol("WM_DELETE_WINDOW", _on_close)
    app.mainloop()
