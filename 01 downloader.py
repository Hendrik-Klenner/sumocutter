from pathlib import Path
import csv
import sys
import time
import re
import yt_dlp

# =========================
# ====== CONFIG ===========
# =========================
USERNAME = "holaabema123"

PLAYLISTS_FILE = Path(
    r"C:\Users\hendr\PycharmProjects\sumocutter\playlistsfordownloading"
) / f"{USERNAME}_playlists.tsv"

DOWNLOAD_DIR = Path(r"C:\Users\hendr\PycharmProjects\sumocutter\unprocessedvideos")

# Batch control ("playlists x to y")
START_INDEX = 6  # inclusive
END_INDEX = 7  # inclusive; set to None to go to the end

# Reject certain videos inside playlists by TITLE
REJECT_TITLE_REGEX = r"(?i)(\bvs\b|cerem)"

# Politeness / safety
SLEEP_BETWEEN_PLAYLISTS_SEC = 2
SLEEP_BETWEEN_VIDEOS_SEC = 1
CONCURRENT_FRAGMENTS = 1
LIMIT_RATE = None  # e.g. "10M"

RETRIES = 10
SOCKET_TIMEOUT = 20
VERBOSE = False

# ===== Quality selection =====
# 1080p30 hard cap: best video that is <=1080p + <=30fps, plus best audio
FORMAT_SELECTOR = "bestvideo[height<=1080][fps<=30]+bestaudio/best[height<=1080]"
FORMAT_SORT = "res,fps,codec,br"

# =========================
# ====== Name parsing =====
# =========================

TOURNAMENT_MONTH = {
    "hatsu": "01",
    "haru": "03",
    "natsu": "05",
    "nagoya": "07",
    "aki": "09",
    "kyushu": "11",
}

DIVISION_CODE = {
    "makuuchi": "M",
    "juryo": "J",
    "makushita": "Ms",
    "sandanme": "Sd",
    "jonidan": "Jd",
    "jonokuchi": "Jk",
}

# Matches titles like:
#   Natsu 2021, Jonidan - Day 1 (Part 01)
#   Aki 2024, Makushita - Day 6
#   Natsu 2019, Sandanme - Day 1 (PART 2)
_TITLE_RE = re.compile(
    r"(?P<tournament>[A-Za-z]+)[\s,_]+"  # Natsu / Aki / ...
    r"(?P<year>\d{4})"  # 2021
    r"[\s,_]+"  # separator
    r"(?P<division>[A-Za-z]+)"  # Jonidan / Makushita / ...
    r"[\s_]*-[\s_]*Day[\s_]*"  # " - Day "
    r"(?P<day>\d+)"  # 1 / 6 / 15
    r"(?:.*?part[\s_]*(?P<part>\d+))?",  # optional (Part 01) / PART 2
    re.IGNORECASE,
)


def parse_sumo_title(title: str) -> str | None:
    """Convert a sumo video title to the short filename format."""
    m = _TITLE_RE.search(title)
    if not m:
        return None

    tournament = m.group("tournament").lower()
    month = TOURNAMENT_MONTH.get(tournament)
    if not month:
        return None

    division_raw = m.group("division").lower()
    div_code = DIVISION_CODE.get(division_raw)
    if not div_code:
        return None

    year = m.group("year")
    day = m.group("day").zfill(2)  # zero-pad to 2 digits
    part = m.group("part")

    name = f"{year}{month}-{day}-{div_code}"
    if part:
        name += f"-pt{int(part)}"  # int() strips leading zeros

    return name


def parse_rate_limit(rate_str) -> int | None:
    """Converts a rate limit string (like '10M') to bytes per second for yt-dlp API."""
    if not rate_str:
        return None
    rate_str = str(rate_str).upper().strip()
    match = re.match(r"^(\d+)([KMG]?)$", rate_str)
    if not match:
        return None
    val, unit = match.groups()
    val = int(val)
    if unit == 'K':
        return val * 1024
    if unit == 'M':
        return val * 1024 * 1024
    if unit == 'G':
        return val * 1024 * 1024 * 1024
    return val


# =========================
# ====== Helpers ==========
# =========================

def read_playlists_tsv(path: Path):
    playlists = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        _header = next(reader, None)

        for row in reader:
            if not row:
                continue
            first = row[0].strip()
            if first.startswith("#"):
                continue
            if len(row) < 5:
                continue
            try:
                idx = int(row[0])
            except ValueError:
                continue
            playlists.append({
                "index": idx,
                "playlist_id": row[1].strip(),
                "videos_total": row[2].strip(),
                "name": row[3].strip(),
                "url": row[4].strip(),
            })
    return playlists


def in_range(i: int, start: int, end):
    if i < start:
        return False
    if end is None:
        return True
    return i <= end


# =========================
# ====== Downloading ======
# =========================

def download_playlist(url: str, download_dir: Path):
    download_dir.mkdir(parents=True, exist_ok=True)

    # Filter out skipped videos natively by looking at their titles before downloading
    def title_filter(info_dict, *, incomplete):
        title = info_dict.get('title', '')
        if re.search(REJECT_TITLE_REGEX, title):
            return f"Skipping: Title '{title}' matches REJECT_TITLE_REGEX"
        return None

    # This hook is executed instantly after an individual video finishes merging
    def rename_hook(d):
        if d['status'] == 'finished':
            filepath = Path(d['filepath'])
            info_dict = d['info_dict']

            # Use the original raw metadata title (bypassing restrictfilenames sanitization)
            raw_title = info_dict.get('title', filepath.stem)

            parsed = parse_sumo_title(raw_title)
            if parsed:
                new_name = f"{parsed}{filepath.suffix}"
            else:
                # Fallback: keep safe raw title
                safe_title = re.sub(r'[\\/:*?"<>|]', "_", raw_title).strip()
                new_name = f"{safe_title}{filepath.suffix}"

            new_path = download_dir / new_name

            # Handle duplicates safely
            counter = 1
            while new_path.exists():
                new_path = download_dir / f"{new_path.stem}_{counter}{new_path.suffix}"
                counter += 1

            try:
                filepath.rename(new_path)
                print(f"\n[Renamer] Success: '{filepath.name}' -> '{new_path.name}'\n")
            except Exception as e:
                print(f"\n[Renamer] Failed to rename '{filepath.name}': {e}\n")

    # Build format sorting list
    sort_list = [s.strip() for s in FORMAT_SORT.split(",") if s.strip()]

    # Convert settings to programmatic yt-dlp options
    ydl_opts = {
        'retries': RETRIES,
        'extractor_retries': RETRIES,
        'fragment_retries': RETRIES,
        'socket_timeout': SOCKET_TIMEOUT,

        'match_filter': title_filter,
        'concurrent_fragment_downloads': CONCURRENT_FRAGMENTS,
        'sleep_interval': SLEEP_BETWEEN_VIDEOS_SEC,
        'max_sleep_interval': SLEEP_BETWEEN_VIDEOS_SEC,

        'format': FORMAT_SELECTOR,
        'format_sort': sort_list,
        'merge_output_format': 'mp4',

        'outtmpl': str(download_dir / '%(title)s.%(ext)s'),
        'ignoreerrors': True,
        'nooverwrites': True,
        'continuedl': True,
        'restrictfilenames': True,

        'postprocessor_hooks': [rename_hook],
        'quiet': not VERBOSE,
    }

    rate_limit = parse_rate_limit(LIMIT_RATE)
    if rate_limit:
        ydl_opts['ratelimit'] = rate_limit

    print(f"\n[Downloader] Starting bulk download for: {url}")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


# =========================
# ====== Main =============
# =========================

def main():
    print(f"yt-dlp version: {yt_dlp.version.__version__}")

    if not PLAYLISTS_FILE.exists():
        print(f"Missing playlists file:\n  {PLAYLISTS_FILE}\n"
              f"Run 01_get_playlists.py first.")
        return

    playlists = read_playlists_tsv(PLAYLISTS_FILE)
    if not playlists:
        print("No playlists found in TSV.")
        return

    selected = [p for p in playlists
                if in_range(p["index"], START_INDEX, END_INDEX)]

    print(f"Playlists file : {PLAYLISTS_FILE}")
    print(f"Download dir   : {DOWNLOAD_DIR}")
    print(f"Selected batch : indexes {START_INDEX}..{END_INDEX if END_INDEX is not None else 'END'}")
    print(f"Will download  : {len(selected)} playlist(s)")
    print(f"Quality        : 1080p @ 30fps max\n")

    for p in selected:
        print(f"\n{'=' * 60}")
        print(f"=== [{p['index']}] {p['name']} ===")
        print(f"URL: {p['url']}")
        print(f"{'=' * 60}")

        download_playlist(p["url"], DOWNLOAD_DIR)

        time.sleep(SLEEP_BETWEEN_PLAYLISTS_SEC)

    print("\nAll done.")


if __name__ == "__main__":
    main()