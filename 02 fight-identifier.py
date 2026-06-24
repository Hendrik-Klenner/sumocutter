import cv2
import numpy as np
import json
import os
import subprocess
import torch
import librosa
import soundfile as sf
import easyocr
from faster_whisper import WhisperModel
from pathlib import Path

# ============================================================
# FOLDER CONFIGURATION
# ============================================================

INPUT_FOLDER = r"C:\Users\hendr\PycharmProjects\sumocutter\videos"
OUTPUT_FOLDER = r"C:\Users\hendr\PycharmProjects\sumocutter\output"
TEMP_DIR = r"C:\Users\hendr\PycharmProjects\sumocutter\temp_windows"

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov"}

# ============================================================
# STEP 1 — OVERLAY / ENDING DETECTION SETTINGS
# ============================================================

REGION1A_LOSSES = (51, 97, 63, 109)
REGION1B_WINS   = (120, 97, 130, 107)

REGION1A_RGB_TARGET    = (248, 252, 255)
REGION1A_RGB_TOLERANCE = (15, 15, 15)

REGION1B_RGB_TARGET    = (4, 14, 53)
REGION1B_RGB_TOLERANCE = (15, 15, 15)

REGION2A = (271, 63, 390, 111)
REGION2B = (656, 65, 767, 109)
REGION2_THRESHOLD = 55

CHECK_INTERVAL_SECONDS = 3
CONSECUTIVE_HITS_REQUIRED = 2
END_DELAY_SECONDS = 10
END_OF_DAY_TIMEOUT_MINUTES = 999

REPLAY_REGION = (79, 922, 336, 978)
REPLAY_OCR_KEYWORD = "replay"
REPLAY_FIRST_STEP_SECONDS = 5
REPLAY_STEP_SECONDS = 3
REPLAY_CLEAR_EXTRA_SECONDS = 10

# ============================================================
# STEP 2 — WHISPER / ANNOUNCER SETTINGS
# ============================================================

WHISPER_MODEL = "small"
TARGET_SR = 16000
POST_ANNOUNCER_BUFFER = 10.0
MAX_WHISPER_SECONDS = 60

ANNOUNCER_TARGETS = [
    "出身", "しゅっしん", "シュッシン", "shusshin",
    "部屋", "べや", "へや", "beya", "heya",
]

# ============================================================
# STEP 3 — AUDIO ENERGY SETTINGS
# ============================================================

ENERGY_WINDOW_SECONDS = 4.0
ENERGY_STEP_SECONDS = 1.0
MULTIPEAK_MIN_SEPARATION_SECONDS = 5.0
TOP_PEAKS_TO_CHECK = 3
MULTIPEAK_SIMILARITY_THRESHOLD = 0.2

# ============================================================
# STEP 4 — OCR / NAME READING SETTINGS
# ============================================================

REGION3A_FIGHTER1 = (220, 55, 445, 118)
REGION3B_FIGHTER2 = (595, 55, 832, 117)

OCR_MIN_CONFIDENCE = 0.3
OCR_EXTRA_OFFSET_SECONDS = 1.0

# ============================================================
# SHARED HELPERS
# ============================================================

def format_time(seconds):
    if seconds is None:
        return "UNKNOWN"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def seconds_to_hms(seconds):
    return format_time(seconds)

def timestamp_to_seconds(frame_number, fps):
    return frame_number / fps

class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.float32, np.float64)):
            return float(obj)
        if isinstance(obj, (np.int32, np.int64)):
            return int(obj)
        return super().default(obj)

# ============================================================
# AUDIO EXTRACTION FROM VIDEO
# ============================================================

def extract_audio_from_video(video_path: str, output_wav_path: str,
                              target_sr: int = TARGET_SR) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-ar", str(target_sr),
        "-ac", "1",
        "-vn",
        output_wav_path
    ]
    print(f"  Extracting audio from video...")
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        print(f"  ffmpeg error: {result.stderr.decode()}")
        return False
    print(f"  Audio extracted to {output_wav_path}")
    return True

# ============================================================
# STEP 1 HELPERS
# ============================================================

def get_region(frame, region):
    x1, y1, x2, y2 = region
    return frame[y1:y2, x1:x2]

def is_overlay_up(frame):
    def matches_rgb(region, target, tolerance):
        # frame is BGR in OpenCV, convert to RGB first
        rgb = cv2.cvtColor(region, cv2.COLOR_BGR2RGB)
        r_target, g_target, b_target = target
        r_tol,    g_tol,    b_tol    = tolerance
        mask = (
            (np.abs(rgb[:,:,0].astype(int) - r_target) < r_tol) &
            (np.abs(rgb[:,:,1].astype(int) - g_target) < g_tol) &
            (np.abs(rgb[:,:,2].astype(int) - b_target) < b_tol)
        )
        return mask.any()

    region1a = get_region(frame, REGION1A_LOSSES)
    region1b = get_region(frame, REGION1B_WINS)
    return (matches_rgb(region1a, REGION1A_RGB_TARGET, REGION1A_RGB_TOLERANCE) and
            matches_rgb(region1b, REGION1B_RGB_TARGET, REGION1B_RGB_TOLERANCE))

def store_region2(frame):
    return (
        get_region(frame, REGION2A).copy(),
        get_region(frame, REGION2B).copy(),
    )

def is_same_fighter(stored_regions2, current_frame):
    current_regions = (
        get_region(current_frame, REGION2A),
        get_region(current_frame, REGION2B),
    )
    diffs = [
        np.abs(cur.astype(float) - sto.astype(float)).mean()
        for sto, cur in zip(stored_regions2, current_regions)
    ]
    max_diff = max(diffs)
    print(f"    Region2 diffs: A={diffs[0]:.1f} B={diffs[1]:.1f} "
          f"| max={max_diff:.1f} (threshold={REGION2_THRESHOLD})")
    return max_diff < REGION2_THRESHOLD


def is_cut_detected(stored_regions2, current_frame):
    """
    Returns True if EITHER Region 2A or Region 2B differs from the stored
    snapshot by more than REGION2_THRESHOLD — indicating the fighter names
    have changed while the overlay never went down (a hard cut).
    """
    if stored_regions2 is None:
        return False
    current_regions = (
        get_region(current_frame, REGION2A),
        get_region(current_frame, REGION2B),
    )
    diffs = [
        np.abs(cur.astype(float) - sto.astype(float)).mean()
        for sto, cur in zip(stored_regions2, current_regions)
    ]
    print(f"    Cut-check Region2 diffs: A={diffs[0]:.1f} B={diffs[1]:.1f} "
          f"| threshold={REGION2_THRESHOLD} "
          f"| triggered={'YES' if any(d >= REGION2_THRESHOLD for d in diffs) else 'no'}")
    return any(d >= REGION2_THRESHOLD for d in diffs)


def read_frame_at(cap, frame_number):
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
    return cap.read()


def has_replay_text(frame, ocr_reader):
    """
    Return True if the word 'REPLAY' is detected inside REPLAY_REGION.
    """
    crop = get_region(frame, REPLAY_REGION)
    results = ocr_reader.readtext(crop, detail=1)
    for (_, text, conf) in results:
        if REPLAY_OCR_KEYWORD in text.strip().lower():
            print(f"      OCR found '{text.strip()}' (conf={conf:.2f}) in replay region")
            return True
    return False


def find_replay_adjusted_end(cap, fps, end_frame, ocr_reader):
    """
    Check whether REPLAY is visible at end_frame. If so, walk back in
    steps until it disappears, then subtract REPLAY_CLEAR_EXTRA_SECONDS.

    Returns adjusted end in seconds, or None if no replay was found.
    """
    end_seconds = timestamp_to_seconds(end_frame, fps)
    print(f"    Checking for REPLAY text at {seconds_to_hms(end_seconds)}...")

    ret, frame = read_frame_at(cap, end_frame)
    if not ret:
        print(f"    Could not read frame — skipping replay check.")
        return None

    if not has_replay_text(frame, ocr_reader):
        print(f"    No REPLAY text found — treating as normal ending.")
        return None

    current_seconds = end_seconds
    first_step = True
    print(f"    REPLAY detected. Walking back...")

    while True:
        step = REPLAY_FIRST_STEP_SECONDS if first_step else REPLAY_STEP_SECONDS
        first_step = False
        current_seconds = max(0.0, current_seconds - step)
        check_frame = int(current_seconds * fps)

        ret, frame = read_frame_at(cap, check_frame)
        if not ret:
            print(f"    Could not read frame at {seconds_to_hms(current_seconds)} "
                  f"— stopping walk-back here.")
            break

        print(f"    Checking {seconds_to_hms(current_seconds)} "
              f"(stepped back {step}s)...")

        if not has_replay_text(frame, ocr_reader):
            adjusted = max(0.0, current_seconds - REPLAY_CLEAR_EXTRA_SECONDS)
            print(f"    REPLAY gone at {seconds_to_hms(current_seconds)}. "
                  f"Adjusted end → {seconds_to_hms(adjusted)}")
            return adjusted

        if current_seconds == 0.0:
            print(f"    Reached start of video with REPLAY still visible — giving up.")
            return 0.0

# ============================================================
# STEP 1 — find endings
# ============================================================

def find_endings(video_path: str) -> list[dict]:
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    check_interval_frames = int(CHECK_INTERVAL_SECONDS * fps)
    end_of_day_timeout_frames = int(END_OF_DAY_TIMEOUT_MINUTES * 60 * fps)

    print(f"\n[Step 1] Video: {video_path}")
    print(f"  FPS: {fps:.2f} | Frames: {total_frames} | "
          f"Duration: {seconds_to_hms(total_frames / fps)}")

    overlay_state = "down"
    consecutive_up = 0
    consecutive_down = 0
    first_negative_frame = None
    provisional_end_frame = None
    stored_regions2 = None
    last_overlay_up_frame = None
    prev_overlay_up_seconds = 0.0
    confirmed_endings = []
    frame_number = 0

    def commit_ending(overlay_up_seconds, raw_end_frame):
        end_s = max(
            0.0,
            timestamp_to_seconds(raw_end_frame, fps) - END_DELAY_SECONDS,
        )
        ov_up_s = overlay_up_seconds
        confirmed_endings.append({
            "overlay_up_seconds": round(ov_up_s, 2),
            "end_seconds":        round(end_s, 2),
            "overlay_up_hms":     seconds_to_hms(ov_up_s),
            "end_hms":            seconds_to_hms(end_s),
        })
        print(f"  END #{len(confirmed_endings)} logged at {seconds_to_hms(end_s)}")

    while True:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        ret, frame = cap.read()

        if not ret:
            print("\n  Reached end of video.")
            if provisional_end_frame is not None:
                commit_ending(prev_overlay_up_seconds, provisional_end_frame)
            break

        overlay_currently_up = is_overlay_up(frame)
        if overlay_currently_up:
            consecutive_up += 1
            consecutive_down = 0
        else:
            consecutive_down += 1
            if consecutive_down == 1:
                first_negative_frame = frame_number
            consecutive_up = 0

        timestamp = timestamp_to_seconds(frame_number, fps)

        # ---- overlay just came UP ----
        if overlay_state == "down" and consecutive_up >= CONSECUTIVE_HITS_REQUIRED:
            overlay_state = "up"
            consecutive_up = 0
            consecutive_down = 0
            last_overlay_up_frame = frame_number

            if stored_regions2 is None:
                print(f"  [{seconds_to_hms(timestamp)}] First overlay of the day.")
                stored_regions2 = store_region2(frame)
                prev_overlay_up_seconds = timestamp

            elif provisional_end_frame is not None:
                same = is_same_fighter(stored_regions2, frame)

                if same:
                    print(f"  [{seconds_to_hms(timestamp)}] Same fighter — "
                          f"committing held ending...")
                    commit_ending(prev_overlay_up_seconds, provisional_end_frame)
                    provisional_end_frame = None
                    first_negative_frame = None
                    prev_overlay_up_seconds = timestamp
                    stored_regions2 = store_region2(frame)

                else:
                    print(f"  [{seconds_to_hms(timestamp)}] New fighter — "
                          f"committing normal ending...")
                    commit_ending(prev_overlay_up_seconds, provisional_end_frame)
                    stored_regions2 = store_region2(frame)
                    provisional_end_frame = None
                    first_negative_frame = None
                    prev_overlay_up_seconds = timestamp

            else:
                print(f"  [{seconds_to_hms(timestamp)}] Overlay up, no provisional "
                      f"end — updating fighter.")
                stored_regions2 = store_region2(frame)
                prev_overlay_up_seconds = timestamp

        # ---- overlay just went DOWN ----
        elif overlay_state == "up" and consecutive_down >= CONSECUTIVE_HITS_REQUIRED:
            overlay_state = "down"
            consecutive_up = 0
            consecutive_down = 0
            provisional_end_frame = first_negative_frame
            prev_overlay_up_seconds = (
                timestamp_to_seconds(last_overlay_up_frame, fps)
                if last_overlay_up_frame is not None else 0.0
            )
            prov_ts = timestamp_to_seconds(provisional_end_frame, fps)
            print(f"  [{seconds_to_hms(prov_ts)}] Overlay went down.")


        # ---- cut detection: completely separate, not elif ----
        if (overlay_state == "up"
              and overlay_currently_up
              and stored_regions2 is not None
              and is_cut_detected(stored_regions2, frame)):
            ...

            print(f"  [{seconds_to_hms(timestamp)}] Cut detected — overlay stayed up "
                  f"but fighter region changed. Committing ending...")

            commit_ending(prev_overlay_up_seconds, frame_number)

            # This frame is already the new fight's overlay — reset accordingly.
            stored_regions2 = store_region2(frame)
            prev_overlay_up_seconds = timestamp
            provisional_end_frame = None
            first_negative_frame = None
            last_overlay_up_frame = frame_number
            # overlay_state remains "up" — the overlay never dropped.

        # ---- end-of-day timeout ----
        if (provisional_end_frame is not None
                and overlay_state == "down"
                and (frame_number - provisional_end_frame)
                > end_of_day_timeout_frames):

            print(f"  Timeout — committing ending...")
            commit_ending(prev_overlay_up_seconds, provisional_end_frame)
            provisional_end_frame = None

        if frame_number > 0 and frame_number % int(fps * 300) == 0:
            pct = (frame_number / total_frames) * 100
            print(f"  Progress: {pct:.1f}% "
                  f"[{seconds_to_hms(timestamp_to_seconds(frame_number, fps))}] "
                  f"Endings so far: {len(confirmed_endings)}")

        frame_number += check_interval_frames

    cap.release()
    print(f"\n[Step 1] Done — {len(confirmed_endings)} endings found.")
    return confirmed_endings


# ============================================================
# STEP 1b — replay adjustment pass
# ============================================================

def adjust_endings_for_replays(endings: list[dict],
                                video_path: str,
                                ocr_reader) -> list[dict]:
    """
    Post-pass over confirmed endings. For each one, open the video at
    end_seconds and check for REPLAY text. If found, walk back until
    it disappears and adjust end_seconds accordingly.
    """
    print(f"\n[Step 1b] Checking {len(endings)} ending(s) for replays...")
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    adjusted = []

    for i, ending in enumerate(endings):
        end_seconds = ending["end_seconds"]
        end_frame = int(end_seconds * fps)
        print(f"\n  Ending {i+1}/{len(endings)} at {seconds_to_hms(end_seconds)}")

        new_end_seconds = find_replay_adjusted_end(
            cap, fps, end_frame, ocr_reader
        )

        if new_end_seconds is not None:
            entry = dict(ending)
            entry["end_seconds"] = round(new_end_seconds, 2)
            entry["end_hms"] = seconds_to_hms(new_end_seconds)
            adjusted.append(entry)
        else:
            adjusted.append(ending)

    cap.release()
    print(f"\n[Step 1b] Done.")
    return adjusted

# ============================================================
# STEP 2 HELPERS — Whisper
# ============================================================

def extract_window_to_file(full_audio, sr, start_s, end_s, output_path):
    start_sample = max(0, int(start_s * sr))
    end_sample = min(len(full_audio), int(end_s * sr))
    sf.write(output_path, full_audio[start_sample:end_sample], sr)

def find_last_announcer_segment(segments, window_start_seconds):
    last_end = None
    for segment in segments:
        text = segment["text"].strip()
        text_lower = text.lower()
        for target in ANNOUNCER_TARGETS:
            if target.lower() in text_lower:
                segment_end = window_start_seconds + segment["end"]
                if last_end is None or segment_end > last_end:
                    last_end = segment_end
                    print(f"    ✓ '{target}' in: \"{text}\" "
                          f"[{segment['start']:.1f}s–{segment['end']:.1f}s] "
                          f"→ abs end: {format_time(segment_end)}")
                break
    return last_end

# ============================================================
# STEP 2 — provisional starts from announcer detection
# ============================================================

def find_provisional_starts(endings: list[dict],
                             full_audio, sr,
                             whisper_model) -> list[dict]:
    results = []
    os.makedirs(TEMP_DIR, exist_ok=True)

    for i, fight in enumerate(endings):
        fight_number = i + 1
        original_start = fight["overlay_up_seconds"]
        ending_seconds = fight["end_seconds"]
        window_duration = ending_seconds - original_start
        whisper_end = min(ending_seconds, original_start + MAX_WHISPER_SECONDS)
        whisper_duration = whisper_end - original_start

        print(f"\n{'='*60}")
        print(f"[Step 2] Fight {fight_number}/{len(endings)} | "
              f"{format_time(original_start)} → {format_time(ending_seconds)} | "
              f"window={window_duration:.1f}s | whisper={whisper_duration:.1f}s")

        window_path = os.path.join(TEMP_DIR, f"window_{fight_number:03d}.wav")
        extract_window_to_file(full_audio, sr, original_start,
                               whisper_end, window_path)

        segments_raw, _ = whisper_model.transcribe(
            window_path,
            language="ja",
            task="transcribe",
            word_timestamps=False,
            vad_filter=True,
        )
        segments = [{"text": s.text, "start": s.start, "end": s.end}
                    for s in segments_raw]

        print("  Transcript:")
        for seg in segments:
            print(f"    [{seg['start']:.1f}s–{seg['end']:.1f}s] "
                  f"{seg['text'].strip()}")

        last_announcer_end = find_last_announcer_segment(segments, original_start)

        if last_announcer_end is not None:
            provisional_start = min(
                last_announcer_end + POST_ANNOUNCER_BUFFER,
                ending_seconds - 3.0,
            )
            method = "announcer_found"
            print(f"  Last announcer ends : {format_time(last_announcer_end)}")
            print(f"  Provisional start   : {format_time(provisional_start)}")
        else:
            provisional_start = original_start
            method = "announcer_not_found"
            print(f"  No announcer — keeping window start: "
                  f"{format_time(original_start)}")

        results.append({
            "fight_number": fight_number,
            "overlay_up_seconds": original_start,
            "end_seconds": ending_seconds,
            "provisional_start_seconds": round(provisional_start, 2),
            "method": method,
        })

        try:
            os.remove(window_path)
        except OSError:
            pass

    try:
        os.rmdir(TEMP_DIR)
    except OSError:
        pass

    no_announcer = [r for r in results if r["method"] == "announcer_not_found"]
    if no_announcer:
        print(f"\n[Step 2] No announcer detected in "
              f"{len(no_announcer)} fight(s):")
        for r in no_announcer:
            print(f"  Fight {r['fight_number']:>3} | "
                  f"{format_time(r['overlay_up_seconds'])} → "
                  f"{format_time(r['end_seconds'])}")

    print(f"\n[Step 2] Done — {len(results)} fights processed.")
    return results

# ============================================================
# STEP 3 — sliding window absolute energy increase
# ============================================================

def compute_sliding_window_increases(audio, sr,
                                     window_seconds, step_seconds):
    window_samples = int(window_seconds * sr)
    step_samples   = int(step_seconds * sr)

    times     = []
    increases = []

    i = 0
    while True:
        a_start = i * step_samples
        a_end   = a_start + window_samples
        b_start = a_end
        b_end   = b_start + window_samples

        if b_end > len(audio):
            break

        rms_a = float(np.sqrt(np.mean(audio[a_start:a_end] ** 2)))
        rms_b = float(np.sqrt(np.mean(audio[b_start:b_end] ** 2)))
        increase = rms_b - rms_a

        times.append(i * step_seconds)
        increases.append(increase)
        i += 1

    return np.array(times), np.array(increases)


def find_fight_start(window_audio, sr, window_start_seconds,
                     fight_number, ending_seconds):
    times, increases = compute_sliding_window_increases(
        window_audio, sr, ENERGY_WINDOW_SECONDS, ENERGY_STEP_SECONDS
    )

    if len(increases) < 2:
        return None, True, "Window too short, likely fusen", []

    max_inc = max(increases) if increases.max() > 0 else 1.0
    print(f"  Sliding window increases ({ENERGY_WINDOW_SECONDS}s windows, "
          f"{ENERGY_STEP_SECONDS}s step):")
    for t, inc in zip(times, increases):
        bar_val = max(0, inc)
        bar = '█' * int((bar_val / max_inc) * 40) if max_inc > 0 else ''
        abs_t = window_start_seconds + t
        print(f"    {t:6.1f}s ({format_time(abs_t)}): "
              f"Δ{inc:+.4f}  {bar}")

    ranked_indices = np.argsort(increases)[::-1]
    top_indices    = ranked_indices[:TOP_PEAKS_TO_CHECK]

    best_idx      = top_indices[0]
    best_time     = times[best_idx]
    best_increase = increases[best_idx]

    start_in_window = best_time + ENERGY_WINDOW_SECONDS
    absolute_start  = window_start_seconds + start_in_window

    print(f"\n  Best jump at window-A-start={best_time:.1f}s "
          f"→ fight start at {format_time(absolute_start)} "
          f"(Δ={best_increase:+.4f})")

    competing = []
    for idx in top_indices[1:]:
        candidate_time     = times[idx]
        candidate_increase = increases[idx]

        if abs(candidate_time - best_time) < MULTIPEAK_MIN_SEPARATION_SECONDS:
            if candidate_time < best_time:
                best_idx      = idx
                best_time     = candidate_time
                best_increase = candidate_increase
                start_in_window = best_time + ENERGY_WINDOW_SECONDS
                absolute_start  = window_start_seconds + start_in_window
                print(f"  Nearby earlier peak found at {best_time:.1f}s "
                      f"— using that instead (same jump, earlier edge)")
            continue

        if best_increase <= 0:
            continue
        similarity = (best_increase - candidate_increase) / best_increase
        if similarity <= MULTIPEAK_SIMILARITY_THRESHOLD:
            competing.append(candidate_time)

    if competing:
        all_plausible_absolute = sorted([
            round(window_start_seconds + best_time + ENERGY_WINDOW_SECONDS, 2)
        ] + [
            round(window_start_seconds + t + ENERGY_WINDOW_SECONDS, 2) for t in competing
        ])

        flag_reason = (
            f"Multiple similar energy peaks found "
            f"({len(competing) + 1} candidates)"
        )
        print(f"  FLAGGED — {flag_reason}")
        print(f"  Plausible starts (absolute): "
              f"{[format_time(p) for p in all_plausible_absolute]}")

        return (absolute_start, True, flag_reason, all_plausible_absolute)

    return (absolute_start, False, None, [])


def find_all_starts(provisional_starts: list[dict],
                    full_audio, sr) -> list[dict]:
    results = []

    for fight in provisional_starts:
        fight_number      = fight["fight_number"]
        provisional_start = fight["provisional_start_seconds"]
        ending_seconds    = fight["end_seconds"]
        window_duration   = ending_seconds - provisional_start
        announcer_method  = fight["method"]

        print(f"\n{'='*60}")
        print(f"[Step 3] Fight {fight_number}/{len(provisional_starts)} | "
              f"prov. start: {format_time(provisional_start)} | "
              f"end: {format_time(ending_seconds)} | "
              f"window: {window_duration:.1f}s")

        start_sample = max(0, int(provisional_start * sr))
        end_sample   = min(len(full_audio), int(ending_seconds * sr))
        window_audio = full_audio[start_sample:end_sample]

        start_seconds, flagged, flag_reason, alt_peaks = find_fight_start(
            window_audio, sr, provisional_start,
            fight_number, ending_seconds
        )

        if start_seconds is None:
            results.append({
                "fight_number":    fight_number,
                "start_seconds":   None,
                "end_seconds":     round(ending_seconds, 2),
                "start_hms":       None,
                "end_hms":         format_time(ending_seconds),
                "flagged":         True,
                "flag_reason":     flag_reason,
                "alt_peak_seconds": [],
                "announcer_method": announcer_method,
            })
            continue

        fight_duration = ending_seconds - start_seconds
        print(f"\n  ► Start    : {format_time(start_seconds)}")
        print(f"  ► End      : {format_time(ending_seconds)}")
        print(f"  ► Duration : {fight_duration:.1f}s")
        if flagged:
            print(f"  ► FLAGGED  : {flag_reason}")

        results.append({
            "fight_number":    fight_number,
            "start_seconds":   round(start_seconds, 2),
            "end_seconds":     round(ending_seconds, 2),
            "start_hms":       format_time(start_seconds),
            "end_hms":         format_time(ending_seconds),
            "flagged":         flagged,
            "flag_reason":     flag_reason,
            "alt_peak_seconds": alt_peaks,
            "announcer_method": announcer_method,
        })

    flagged_count = sum(1 for r in results if r["flagged"])
    print(f"\n[Step 3] Done — {len(results)} fights | "
          f"{flagged_count} flagged.")
    return results

# ============================================================
# STEP 4 HELPERS — OCR
# ============================================================

def read_name_from_frame(frame, region, ocr_reader):
    x1, y1, x2, y2 = region
    crop = frame[y1:y2, x1:x2]
    results = ocr_reader.readtext(crop, detail=1)
    accepted = [
        text for (_, text, conf) in results
        if conf >= OCR_MIN_CONFIDENCE and text.strip()
    ]
    if not accepted:
        return None
    return " ".join(accepted).strip()


def read_names_at_timestamp(video_path, timestamp_seconds, ocr_reader):
    if timestamp_seconds is None:
        return None, None

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    target_frame = int(timestamp_seconds * fps)

    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
    ret, frame = cap.read()
    cap.release()

    if not ret:
        print(f"    Could not read frame at {format_time(timestamp_seconds)}")
        return None, None

    name_a = read_name_from_frame(frame, REGION3A_FIGHTER1, ocr_reader)
    name_b = read_name_from_frame(frame, REGION3B_FIGHTER2, ocr_reader)

    print(f"    @ {format_time(timestamp_seconds)} → "
          f"A='{name_a or '–'}' | B='{name_b or '–'}'")

    return name_a, name_b


def resolve_fighter_names(readings_a: list, readings_b: list):
    """
    Takes three readings for fighter A and three for fighter B.
    For each fighter:
      - If any value appears 2 or more times, that is the agreed name.
      - Otherwise the name is uncertain.
    Returns:
      fighter1, fighter2      — agreed names or None
      uncertain_a, uncertain_b — readings that appeared only once (singletons)
      names_flagged           — True if either fighter has no agreement
      flag_reason             — explanation string or None
    """
    def find_agreement(readings: list):
        # Strip None values
        valid = [r for r in readings if r is not None]
        if not valid:
            return None, list(readings)

        from collections import Counter
        counts = Counter(valid)
        # Any name seen 2+ times wins
        agreed = [name for name, cnt in counts.items() if cnt >= 2]
        if agreed:
            # If somehow multiple names hit 2+ (shouldn't happen with 3 reads
            # but be safe), take the most common
            winner = max(agreed, key=lambda n: counts[n])
            # Singletons: readings that appeared only once
            singletons = [name for name, cnt in counts.items() if cnt == 1]
            return winner, singletons
        else:
            # No agreement — all are singletons
            return None, valid

    agreed_a, singletons_a = find_agreement(readings_a)
    agreed_b, singletons_b = find_agreement(readings_b)

    names_flagged = (agreed_a is None) or (agreed_b is None)

    if not names_flagged:
        return agreed_a, agreed_b, [], [], False, None

    missing = []
    if agreed_a is None:
        missing.append("fighter 1")
    if agreed_b is None:
        missing.append("fighter 2")
    reason = f"No OCR agreement for: {', '.join(missing)}"

    return agreed_a, agreed_b, singletons_a, singletons_b, True, reason


# ============================================================
# STEP 4 — read names for all fights
# ============================================================

def find_all_names(final_fights: list[dict],
                   video_path: str,
                   ocr_reader) -> list[dict]:
    results = []

    for fight in final_fights:
        fight_number  = fight["fight_number"]
        start_seconds = fight.get("start_seconds")
        end_seconds   = fight.get("end_seconds")

        # Extra read one second into the fight, only if we have a start
        if start_seconds is not None:
            mid_seconds = start_seconds + OCR_EXTRA_OFFSET_SECONDS
            # Don't let mid spill past the end
            if end_seconds is not None:
                mid_seconds = min(mid_seconds, end_seconds)
        else:
            mid_seconds = None

        print(f"\n{'='*60}")
        print(f"[Step 4] Fight {fight_number}/{len(final_fights)} | "
              f"{format_time(start_seconds)} → {format_time(end_seconds)}")
        print(f"  Reading names at t1={format_time(start_seconds)}, "
              f"t2={format_time(mid_seconds)}, "
              f"t3={format_time(end_seconds)}:")

        a1, b1 = read_names_at_timestamp(video_path, start_seconds, ocr_reader)
        a2, b2 = read_names_at_timestamp(video_path, mid_seconds,   ocr_reader)
        a3, b3 = read_names_at_timestamp(video_path, end_seconds,   ocr_reader)

        readings_a = [a1, a2, a3]
        readings_b = [b1, b2, b3]

        print(f"  Raw readings — A: {readings_a} | B: {readings_b}")

        fighter1, fighter2, singletons_a, singletons_b, names_flagged, name_flag_reason = \
            resolve_fighter_names(readings_a, readings_b)

        print(f"  → fighter1='{fighter1 or '?'}' | "
              f"fighter2='{fighter2 or '?'}' | "
              f"flagged={names_flagged}")
        if names_flagged:
            print(f"  → reason: {name_flag_reason}")
            if singletons_a:
                print(f"  → uncertain A readings: {singletons_a}")
            if singletons_b:
                print(f"  → uncertain B readings: {singletons_b}")

        enriched = dict(fight)
        enriched["fighter1"]      = fighter1
        enriched["fighter2"]      = fighter2
        enriched["names_flagged"] = names_flagged
        if names_flagged:
            enriched["name_flag_reason"] = name_flag_reason
            uncertain_names = singletons_a + singletons_b
            if uncertain_names:
                enriched["uncertain_names"] = uncertain_names

        results.append(enriched)

    names_flagged_count = sum(1 for r in results if r["names_flagged"])
    print(f"\n[Step 4] Done — {len(results)} fights | "
          f"{names_flagged_count} names flagged.")
    return results

# ============================================================
# FILE DISCOVERY
# ============================================================

def discover_videos(input_folder: str) -> list[tuple[str, str]]:
    folder = Path(input_folder)
    videos = []

    for entry in sorted(folder.iterdir()):
        if entry.is_file() and entry.suffix.lower() in VIDEO_EXTENSIONS:
            videos.append((entry.stem, str(entry)))

    if not videos:
        print("[Discovery] No video files found.")
    else:
        print(f"[Discovery] Found {len(videos)} video(s): "
              + ", ".join(s for s, _ in videos))

    return videos

# ============================================================
# MASTER PIPELINE
# ============================================================

def run_pipeline(stem: str, video_path: str,
                 output_folder: str,
                 whisper_model,
                 ocr_reader) -> None:

    print(f"\n{'#'*70}")
    print(f"# Processing: {stem}")
    print(f"#   video : {video_path}")
    print(f"{'#'*70}")

    os.makedirs(TEMP_DIR, exist_ok=True)
    extracted_audio_path = os.path.join(TEMP_DIR, f"{stem}_audio.wav")

    ok = extract_audio_from_video(video_path, extracted_audio_path)
    if not ok:
        print(f"[Pipeline] Failed to extract audio for {stem} — skipping.")
        return

    print(f"Loading audio into memory...")
    full_audio, sr = librosa.load(extracted_audio_path, sr=TARGET_SR, mono=True)
    print(f"Audio loaded. Duration: {format_time(len(full_audio) / sr)}")

    try:
        os.remove(extracted_audio_path)
    except OSError:
        pass

    # Step 1 — find endings
    endings = find_endings(video_path)

    # Step 1b — adjust endings where a replay was shown
    endings = adjust_endings_for_replays(endings, video_path, ocr_reader)

    if not endings:
        print(f"[Pipeline] No endings found for {stem} — skipping.")
        return

    # Step 2 — provisional starts from announcer
    provisional_starts = find_provisional_starts(
        endings, full_audio, sr, whisper_model
    )

    # Step 3 — finalise starts from energy analysis
    final_fights = find_all_starts(provisional_starts, full_audio, sr)

    # Step 4 — read fighter names via OCR
    named_fights = find_all_names(final_fights, video_path, ocr_reader)

    # Write output
    os.makedirs(output_folder, exist_ok=True)
    out_path = os.path.join(output_folder, f"{stem}_fights.json")

    fights_out = []
    for f in named_fights:
        entry = {
            "fight_number":  f["fight_number"],
            "fighter1":      f["fighter1"],
            "fighter2":      f["fighter2"],
            "start_hms":     f["start_hms"],
            "end_hms":       f["end_hms"],
            "start_seconds": f["start_seconds"],
            "end_seconds":   f["end_seconds"],
            "flagged":       f["flagged"] or f["names_flagged"],
        }

        if f["flagged"]:
            entry["timing_flag_reason"] = f.get("flag_reason")
            if f.get("alt_peak_seconds"):
                entry["alt_peak_seconds"] = f["alt_peak_seconds"]

        if f["names_flagged"]:
            entry["name_flag_reason"] = f.get("name_flag_reason")
            if f.get("uncertain_names"):
                entry["uncertain_names"] = f["uncertain_names"]

        fights_out.append(entry)

    output = {
        "total_fights": len(named_fights),
        "fights":       fights_out,
    }

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False, cls=NumpyEncoder)

    flagged_timing = [f for f in named_fights if f["flagged"]]
    flagged_names  = [f for f in named_fights if f["names_flagged"]]

    print(f"\n[Pipeline] Output written to: {out_path}")
    print(f"[Pipeline] {len(named_fights)} fights | "
          f"{len(flagged_timing)} timing-flagged | "
          f"{len(flagged_names)} name-flagged")

    if flagged_timing:
        print("[Pipeline] Timing flags:")
        for f in flagged_timing:
            print(f"  Fight {f['fight_number']:>3} | "
                  f"end={f['end_hms']} | {f.get('flag_reason')}")

    if flagged_names:
        print("[Pipeline] Name flags:")
        for f in flagged_names:
            print(f"  Fight {f['fight_number']:>3} | "
                  f"{f.get('name_flag_reason')}")

    try:
        os.rmdir(TEMP_DIR)
    except OSError:
        pass

# ============================================================
# ENTRY POINT
# ============================================================

def main():
    videos = discover_videos(INPUT_FOLDER)

    if not videos:
        print("No videos found. Exiting.")
        return

    device  = "cuda" if torch.cuda.is_available() else "cpu"
    compute = "float16" if device == "cuda" else "int8"
    print(f"\nLoading Whisper model '{WHISPER_MODEL}' on {device} "
          f"(compute={compute})...")
    whisper_model = WhisperModel(WHISPER_MODEL, device=device,
                                 compute_type=compute)
    print("Whisper loaded.")

    print("\nLoading EasyOCR (ja + en)...")
    ocr_reader = easyocr.Reader(["ja", "en"], gpu=(device == "cuda"))
    print("EasyOCR loaded.\n")

    for stem, video_path in videos:
        run_pipeline(stem, video_path, OUTPUT_FOLDER, whisper_model, ocr_reader)

    print(f"\n{'='*70}")
    print("All files processed.")


if __name__ == "__main__":
    main()