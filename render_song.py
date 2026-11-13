#!/usr/bin/env python3
"""Internal renderer for SongForge.

Node owns the web app and the LLM conversation. This file is deliberately a
small, replaceable worker: arrangement JSON in, stems + master out. It is not a
web service and has no CLI beyond this one job.

Pipeline: SCAMP -> MIDI stems -> FluidSynth -> instrumental
          CHIMERA -> lead/harmony -> FFmpeg EQ/reverb/limiter -> WAV + MP3
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

from scamp.performance import Performance, PerformancePart, PerformanceNote

ROOT = Path(__file__).resolve().parent
CHIMERA = Path(os.environ.get("CHIMERA_BIN", Path.home() / "chimera-music/chimera-vocal-synth/target/release/chimera")).expanduser()
FLUIDSYNTH = os.environ.get("FLUIDSYNTH_BIN") or shutil.which("fluidsynth") or "/opt/homebrew/bin/fluidsynth"
FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or str(Path(FFMPEG).with_name("ffprobe"))

CHORD_PATTERNS = {"block", "broken_8", "arpeggio_8", "stabs_8", "pulse_8", "sustained", "none"}
BASS_PATTERNS = {"root_quarter", "root_eighth", "octave_8", "walking", "syncopated", "none"}
DRUM_PATTERNS = {"pop_verse", "pop_chorus", "half_time", "build", "outro", "none"}
TEXTURE_PATTERNS = {"motif", "arp_high", "sustained", "none"}
ROLE_DEFAULTS = {"chords": -10.0, "bass": -6.0, "drums": -4.0, "texture": -16.0}
ROLE_EQ = {
    "chords": "highpass=f=90",
    "bass": "highpass=f=32,lowpass=f=1500",
    "drums": "highpass=f=28",
    "texture": "highpass=f=380,lowpass=f=11500",
}
DRUM = {"kick": 36, "snare": 38, "side": 37, "hat": 42, "open": 46, "crash": 49, "tom": 45, "floor": 43}


def progress(pct: int, message: str) -> None:
    print(f"PROGRESS {pct} {message}", flush=True)


def fail(message: str) -> None:
    print(f"ERROR {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def run(cmd: list[str], *, what: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        fail(f"{what}: executable not found ({cmd[0]}). Set the matching environment variable.")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip().splitlines()
        fail(f"{what} failed: {detail[-1] if detail else exc.returncode}")


def load_arrangement(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        fail(f"arrangement.json is not valid JSON: {exc}")
    if not isinstance(data, dict):
        fail("arrangement.json must be a JSON object")
    return data


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def section_map(data: dict) -> tuple[list[dict], dict[int, dict], int]:
    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        fail("sections must be a non-empty list")
    out, by_bar, cursor = [], {}, 0
    for sec in sections:
        name = str(sec.get("name", "")).strip()
        bars = sec.get("bars")
        if not name or not isinstance(bars, int) or bars < 1:
            fail("each section needs a name and a positive integer `bars`")
        if cursor + bars > 96:
            fail("song is longer than 96 bars; keep the arrangement under four minutes")
        s = dict(sec)
        s["start_bar"] = cursor
        s["bars"] = bars
        s["energy"] = float(sec.get("energy", 0.5))
        out.append(s)
        for b in range(cursor, cursor + bars):
            by_bar[b] = s
        cursor += bars
    if cursor < 16:
        fail("song is too short; use at least 16 bars")
    return out, by_bar, cursor


def chord_list(data: dict, bars: int) -> dict[int, dict]:
    raw = data.get("chords")
    if not isinstance(raw, list):
        fail("chords must be a list with one entry per bar")
    chords = {}
    for item in raw:
        bar = item.get("bar")
        notes = item.get("notes")
        if not isinstance(bar, int) or not 0 <= bar < bars:
            fail(f"chord has an invalid bar: {bar!r}")
        if bar in chords:
            fail(f"more than one chord for bar {bar}")
        if not isinstance(notes, list) or not 3 <= len(notes) <= 6:
            fail(f"chord at bar {bar} needs 3-6 MIDI notes")
        notes = [int(n) for n in notes]
        if any(n < 24 or n > 96 for n in notes):
            fail(f"chord at bar {bar} has notes outside MIDI 24-96")
        chords[bar] = {"bar": bar, "symbol": str(item.get("symbol") or "?"), "notes": notes}
    missing = [b for b in range(bars) if b not in chords]
    if missing:
        fail(f"chords missing for bars: {missing[:8]}")
    return chords


def chimera_syllables(text: str) -> list[dict]:
    if not CHIMERA.exists():
        fail(f"CHIMERA not found at {CHIMERA}. Set CHIMERA_BIN to the chimera executable.")
    try:
        raw = subprocess.check_output([str(CHIMERA), "syllables", text], text=True, stderr=subprocess.STDOUT, timeout=30)
        return json.loads(raw)
    except subprocess.CalledProcessError as exc:
        fail(f"CHIMERA could not parse lyric line {text!r}: {(exc.output or '').strip()}")
    except Exception as exc:
        fail(f"CHIMERA syllable lookup failed for {text!r}: {exc}")


def validate_vocal(lines, label: str, chords: dict[int, dict], bars: int) -> list[str]:
    problems = []
    if not isinstance(lines, list):
        return [f"vocal.{label} must be a list"]
    if label == "lead" and not lines:
        return ["vocal.lead is empty; the song needs a sung lyric"]
    last_bar = -1
    for i, line in enumerate(lines):
        text = str(line.get("text", "")).strip()
        bar, beat = line.get("bar"), float(line.get("beat", 0))
        notes = line.get("notes")
        if not text:
            problems.append(f"lead line {i + 1} has no text")
            continue
        if not isinstance(bar, int) or not 0 <= bar < bars:
            problems.append(f"lead line {i + 1} has an invalid bar")
            continue
        if bar < last_bar:
            problems.append(f"lead line {i + 1} is out of order")
        last_bar = bar
        if beat < 0 or beat >= 4:
            problems.append(f"lead line {i + 1} beat must be within the bar")
        if not isinstance(notes, list) or not notes:
            problems.append(f"lead line {i + 1} has no melody notes")
            continue
        syl = chimera_syllables(text)
        if len(notes) != len(syl):
            problems.append(
                f"line {i + 1} {text!r}: {len(notes)} notes for {len(syl)} syllables "
                f"({' '.join(s['text'] for s in syl)})"
            )
            continue
        cursor = -1e9
        for j, note in enumerate(notes):
            try:
                nb = float(note.get("beat", 0))
                dur = float(note.get("duration", 0))
                midi = int(note.get("midi", 0))
            except Exception:
                problems.append(f"line {i + 1} note {j + 1} has invalid fields")
                continue
            if midi < 36 or midi > 88:
                problems.append(f"line {i + 1} note {j + 1} midi {midi} outside 36-88")
            if dur <= 0 or dur > 8:
                problems.append(f"line {i + 1} note {j + 1} has invalid duration")
            if nb < cursor - 1e-6:
                problems.append(f"line {i + 1} notes overlap or are unsorted")
            cursor = nb
            absolute = bar * 4 + beat + nb
            if absolute + dur > bars * 4 + 1e-6:
                problems.append(f"line {i + 1} note {j + 1} runs past the end of the song")
    # chords must exist under every vocal line
    bars_used = sorted({int(line['bar']) for line in lines if isinstance(line.get('bar'), int) and line.get('bar') in chords})
    if not bars_used and lines:
        problems.append("vocal lines do not sit on any chord bar")
    return problems


def validate(data: dict, deep: bool = True) -> list[str]:
    problems = []
    if not str(data.get("title", "")).strip():
        problems.append("title is required")
    try:
        bpm = float(data.get("bpm"))
    except Exception:
        bpm = 0
    if not 40 <= bpm <= 190:
        problems.append("bpm must be between 40 and 190")
    if str(data.get("time_signature", "4/4")) != "4/4":
        problems.append("only 4/4 is supported")
    sections, by_bar, bars = section_map(data)
    chords = chord_list(data, bars)
    vocal = data.get("vocal")
    if not isinstance(vocal, dict):
        problems.append("vocal must contain `lead` (and optionally `harmony`)")
        return problems
    problems += validate_vocal(vocal.get("lead", []), "lead", chords, bars)
    if vocal.get("harmony"):
        problems += validate_vocal(vocal["harmony"], "harmony", chords, bars)
    tracks = data.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        problems.append("tracks must be a non-empty list")
    else:
        seen = set()
        for i, track in enumerate(tracks):
            name = str(track.get("name", "")).strip()
            role = track.get("role")
            if not name:
                problems.append(f"track {i + 1} needs a name")
            if role not in ROLE_DEFAULTS:
                problems.append(f"track {name or i + 1} has unknown role {role!r}")
                continue
            patterns = track.get("patterns", {})
            if not isinstance(patterns, dict):
                problems.append(f"track {name} patterns must be an object")
                continue
            for sec in sections:
                pat = patterns.get(sec["name"], "none")
                allowed = {"chords": CHORD_PATTERNS, "bass": BASS_PATTERNS, "drums": DRUM_PATTERNS, "texture": TEXTURE_PATTERNS}[role]
                if pat not in allowed:
                    problems.append(f"track {name}: pattern {pat!r} is not valid for {role} (use one of {sorted(allowed)})")
            if not 0 <= int(track.get("program", 0)) <= 127:
                problems.append(f"track {name} program must be 0-127")
            if name in seen:
                problems.append(f"duplicate track name {name}")
            seen.add(name)
    if not any(t.get("role") == "drums" for t in tracks or []):
        problems.append("add a drums track so the arrangement has a pulse")
    if not any(t.get("role") == "bass" for t in tracks or []):
        problems.append("add a bass track; the low end needs a defined root")
    return problems


# ---------------------------------------------------------------------------
# musical helpers
# ---------------------------------------------------------------------------
def fit_chord(notes: list[int], lo: int = 48, hi: int = 84) -> list[int]:
    out = sorted(notes)
    while min(out) < lo:
        out = [n + 12 for n in out]
    while max(out) > hi:
        out = [n - 12 for n in out]
    return out


def bass_root(notes: list[int]) -> int:
    n = min(notes)
    while n < 36:
        n += 12
    while n > 47:
        n -= 12
    return n


def sec_at(by_bar: dict[int, dict], bar: int) -> dict:
    return by_bar[min(bar, max(by_bar))]


def pattern_for(track: dict, section: dict) -> str:
    return track.get("patterns", {}).get(section["name"], "none")


class Writer:
    def __init__(self, slug: str):
        self.rng = random.Random(slug)
        self.events: list[tuple[float, float, int, float]] = []

    def add(self, start: float, dur: float, midi, vol: float, humanize: float = 0.0):
        if isinstance(midi, (list, tuple)):
            for n in midi:
                self.add(start, dur, int(n), vol, humanize)
            return
        jitter = self.rng.uniform(-humanize, humanize) if humanize else 0.0
        vel = max(0.05, min(1.0, vol + (self.rng.uniform(-0.025, 0.025) if humanize else 0)))
        self.events.append((start + jitter, dur, int(midi), vel))


def chord_events(w: Writer, bar: int, chord: dict, pattern: str, energy: float, last: bool):
    b = bar * 4
    ch = fit_chord(chord["notes"])
    top = max(ch)
    e = 0.72 + 0.35 * energy
    if pattern == "block":
        w.add(b, 3.8, ch, 0.36 * e)
        if bar % 4 == 3:
            w.add(b + 2.5, 0.9, top + 12, 0.16 * e)
    elif pattern == "broken_8":
        order = [0, 2, 1, 3, 2, 1, 3, 2]
        for k, i in enumerate(order):
            w.add(b + k * 0.5, 0.48, ch[i % len(ch)], (0.30 if k % 2 == 0 else 0.22) * e, 0.008)
        if last:
            w.add(b + 3.5, 0.5, top + 12, 0.18 * e)
    elif pattern == "arpeggio_8":
        seq = ch + [n + 12 for n in reversed(ch)]
        seq = seq if bar % 2 == 0 else list(reversed(seq))
        for k in range(8):
            w.add(b + k * 0.5, 0.46, seq[k % len(seq)], (0.29 if k % 2 == 0 else 0.21) * e, 0.006)
    elif pattern == "stabs_8":
        for offset, dur, vol in ((0.0, 0.65, 0.44), (1.5, 0.35, 0.30), (2.5, 0.35, 0.34), (3.25, 0.55, 0.30)):
            w.add(b + offset, dur, ch, vol * e, 0.005)
        if bar % 4 == 1:
            w.add(b + 3.75, 0.25, top + 12, 0.22 * e)
    elif pattern == "pulse_8":
        for k in range(8):
            inv = ch if k % 2 == 0 else ch[1:] + [ch[0] + 12]
            w.add(b + k * 0.5, 0.32, inv, (0.27 if k % 4 == 0 else 0.18) * e, 0.004)
    elif pattern == "sustained":
        w.add(b, 3.95, ch, 0.31 * e, 0.004)
    # "none" writes nothing


def bass_events(w: Writer, bar: int, chord: dict, pattern: str, energy: float, next_root: int | None):
    b = bar * 4
    root = bass_root(chord["notes"])
    fifth = root + 7
    octave = root + 12
    third = root + 4
    e = 0.72 + 0.35 * energy
    if pattern == "root_quarter":
        for k in range(4):
            n = root if k < 3 else (fifth if bar % 4 == 3 else root)
            w.add(b + k, 0.82, n, (0.56 if k == 0 else 0.45) * e, 0.006)
        if bar % 8 == 7:
            w.add(b + 3.5, 0.4, octave, 0.40 * e)
    elif pattern == "root_eighth":
        for k in range(8):
            n = root if k % 4 != 3 else (fifth if k < 4 else octave)
            w.add(b + k * 0.5, 0.42, n, (0.55 if k % 2 == 0 else 0.38) * e, 0.005)
    elif pattern == "octave_8":
        for k in range(8):
            n = root if k % 2 == 0 else octave
            w.add(b + k * 0.5, 0.42, n, (0.50 if k % 2 == 0 else 0.36) * e, 0.004)
    elif pattern == "walking":
        notes = [root, fifth, octave, next_root if next_root is not None else third]
        for k, n in enumerate(notes):
            w.add(b + k, 0.86, n, (0.54 if k == 0 else 0.42) * e, 0.007)
    elif pattern == "syncopated":
        for offset, n, dur in ((0.0, root, 0.6), (0.75, root, 0.35), (1.5, fifth, 0.4), (2.0, root, 0.45), (2.75, octave, 0.35), (3.25, fifth, 0.55)):
            w.add(b + offset, dur, n, 0.48 * e, 0.005)


def drum_events(w: Writer, bar: int, pattern: str, energy: float, last: bool, section_start: bool):
    b = bar * 4
    e = 0.66 + 0.50 * energy
    if pattern == "none":
        return
    if pattern in ("pop_verse", "pop_chorus", "outro"):
        kicks = [0, 2] if pattern != "pop_chorus" else [0, 1.5, 2.5]
        for k in kicks:
            w.add(b + k, 0.25, DRUM["kick"], 0.82 * e)
        for k in (1, 3):
            w.add(b + k, 0.25, DRUM["snare"], 0.60 * e)
        for k in range(8):
            w.add(b + k * 0.5, 0.12, DRUM["hat"], (0.24 if k % 2 else 0.32) * e, 0.004)
        if pattern == "pop_chorus":
            w.add(b + 3.5, 0.30, DRUM["open"], 0.30 * e)
            if section_start:
                w.add(b, 0.8, DRUM["crash"], 0.42 * e)
        if pattern == "outro":
            w.add(b + 2, 0.4, DRUM["side"], 0.20 * e)
    elif pattern == "half_time":
        w.add(b, 0.3, DRUM["kick"], 0.78 * e)
        w.add(b + 2.5, 0.3, DRUM["kick"], 0.62 * e)
        w.add(b + 2, 0.35, DRUM["snare"], 0.62 * e)
        for k in range(4):
            w.add(b + k, 0.25, DRUM["hat"], 0.25 * e)
    elif pattern == "build":
        for k in range(4):
            w.add(b + k, 0.25, DRUM["kick"], 0.65 * e)
        if bar % 2 == 1:
            for k in range(8):
                w.add(b + k * 0.5, 0.12, DRUM["snare"], (0.18 + 0.045 * k) * e)
        for k in range(4):
            w.add(b + k, 0.15, DRUM["hat"], 0.20 * e)
    if last and pattern != "half_time":
        # a short, readable fill instead of looping the same bar forever
        for k in range(4):
            w.add(b + 3 + k * 0.25, 0.12, DRUM["snare"], (0.28 + k * 0.06) * e)
        w.add(b + 3.5, 0.2, DRUM["tom"], 0.55 * e)
        w.add(b + 3.75, 0.2, DRUM["floor"], 0.50 * e)


def texture_events(w: Writer, bar: int, chord: dict, pattern: str, energy: float):
    b = bar * 4
    ch = fit_chord(chord["notes"], 60, 88)
    highs = [n + 12 for n in ch]
    e = 0.55 + 0.45 * energy
    if pattern == "motif":
        seq = highs[-3:] + [highs[0] + 12, highs[1] + 12, highs[-1] + 12]
        seq = seq if bar % 2 == 0 else list(reversed(seq))
        for k in range(4):
            w.add(b + 1 + k * 0.75, 0.62, seq[k % len(seq)], (0.20 if k else 0.26) * e, 0.005)
    elif pattern == "arp_high":
        for k in range(8):
            w.add(b + k * 0.5, 0.40, highs[k % len(highs)], (0.18 if k % 2 else 0.24) * e, 0.004)
    elif pattern == "sustained":
        w.add(b, 3.9, [highs[0], highs[-1]], 0.20 * e, 0.004)


# ---------------------------------------------------------------------------
# MIDI + audio
# ---------------------------------------------------------------------------
def export_midi(part: PerformancePart, path: Path, bpm: float, program: int, drums: bool):
    perf = Performance([part])
    perf.export_to_midi_file(str(path))
    import mido
    mf = mido.MidiFile(str(path))
    for tr in mf.tracks:
        tr[:] = [m for m in tr if m.type != "set_tempo"]
    mf.tracks[0].insert(0, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    channel = 9 if drums else 0
    for tr in mf.tracks:
        new = []
        for msg in tr:
            if msg.type in ("note_on", "note_off", "control_change", "pitchwheel"):
                new.append(msg.copy(channel=channel))
            elif msg.type == "program_change":
                continue
            else:
                new.append(msg)
        if drums:
            new.insert(1, mido.Message("program_change", channel=9, program=0, time=0))
        else:
            new.insert(1, mido.Message("program_change", channel=0, program=int(program), time=0))
        tr.messages = new
    mf.save(str(path))


def render_stem(part: PerformancePart, name: str, program: int, drums: bool, midi_dir: Path,
                stem_dir: Path, bpm: float, sf2: Path) -> Path | None:
    if part.end_beat <= 0:
        return None
    mid = midi_dir / f"{name}.mid"
    wav = stem_dir / f"{name}.wav"
    export_midi(part, mid, bpm, program, drums)
    run([
        str(FLUIDSYNTH), "-ni", "-g", "0.55", "-r", "48000", "-F", str(wav),
        "-o", "synth.reverb.active=0", "-o", "synth.chorus.active=0",
        str(sf2), str(mid),
    ], what=f"FluidSynth ({name})")
    return wav


def find_sf2() -> Path:
    env = os.environ.get("SONGFORGE_SF2")
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return p
        fail(f"SONGFORGE_SF2 does not exist: {p}")
    candidates = [
        ROOT / "assets" / "Merlin.sf2",
        Path.home() / "Documents/Default Project/somewhere-in-between/.venv/lib/python3.13/site-packages/scamp/soundfonts/Merlin.sf2",
        Path("/usr/local/share/soundfonts/default.sf2"),
        Path("/opt/homebrew/share/fluidsynth/FluidR3_GM.sf2"),
    ]
    try:
        import scamp
        candidates.insert(0, Path(scamp.__file__).resolve().parent / "soundfonts" / "Merlin.sf2")
    except Exception:
        pass
    for p in candidates:
        if p.exists():
            return p
    fail("No SoundFont found. Set SONGFORGE_SF2 to a .sf2 file (SCAMP ships Merlin.sf2).")


def build_instrumental(tracks: list[dict], sections: list[dict], by_bar: dict, chords: dict, bars: int,
                       bpm: float, out: Path, work: Path, sf2: Path) -> float:
    total_seconds = bars * 4 * 60.0 / bpm
    midi_dir, stem_dir = work / "midi", work / "stems"
    midi_dir.mkdir(parents=True, exist_ok=True)
    stem_dir.mkdir(parents=True, exist_ok=True)
    stems: list[tuple[Path, float, str]] = []
    for track in tracks:
        role = track["role"]
        w = Writer(f"{out.name}-{track['name']}")
        for bar in range(bars):
            section = by_bar[bar]
            pattern = pattern_for(track, section)
            chord = chords[bar]
            last = bar == section["start_bar"] + section["bars"] - 1
            start = bar == section["start_bar"]
            if role == "chords":
                chord_events(w, bar, chord, pattern, section["energy"], last)
            elif role == "bass":
                nxt = bass_root(chords[bar + 1]["notes"]) if bar + 1 < bars else None
                bass_events(w, bar, chord, pattern, section["energy"], nxt)
            elif role == "drums":
                drum_events(w, bar, pattern, section["energy"], last, start)
            elif role == "texture":
                texture_events(w, bar, chord, pattern, section["energy"])
        if not w.events:
            continue
        part = PerformancePart(name=track["name"])
        for start, dur, midi, vel in w.events:
            part.add_note(PerformanceNote(
                start_beat=start, length=dur, pitch=midi, volume=vel,
                properties={}, velocity=None,
            ))
        wav = render_stem(part, track["name"].replace(" ", "_"), int(track.get("program", 0)),
                          role == "drums", midi_dir, stem_dir, bpm, sf2)
        if wav:
            gain = float(track.get("gain_db", ROLE_DEFAULTS[role]))
            stems.append((wav, gain, ROLE_EQ.get(role, "")))
    if not stems:
        fail("no playable instrumental stems were generated")

    progress(72, "Balancing the band")
    cmd = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y"]
    for wav, _, _ in stems:
        cmd += ["-i", str(wav)]
    filters = []
    for i, (_, gain, eq) in enumerate(stems):
        chain = [f"volume={10 ** (gain / 20.0):.5f}"]
        if eq:
            chain.append(eq)
        filters.append(f"[{i}:a]{','.join(chain)}[s{i}]")
    mix_inputs = "".join(f"[s{i}]" for i in range(len(stems)))
    filters.append(
        f"{mix_inputs}amix=inputs={len(stems)}:normalize=0:duration=longest,"
        f"apad=whole_dur={total_seconds:.3f},atrim=0:{total_seconds:.3f},"
        f"alimiter=limit=0.95,aresample=48000[out]"
    )
    cmd += ["-filter_complex", ";".join(filters), "-map", "[out]", "-ac", "2",
            "-c:a", "pcm_s16le", str(out)]
    run(cmd, what="instrumental mix")
    return total_seconds


# ---------------------------------------------------------------------------
# vocals
# ---------------------------------------------------------------------------
def vocal_document(arr: dict, lines: list[dict], body: str, vibrato: float, syl_cache: dict) -> dict:
    bpm = float(arr["bpm"])
    events = []
    syllables = []
    for i, line in enumerate(lines):
        text = line["text"]
        if text not in syl_cache:
            syl_cache[text] = chimera_syllables(text)
        syl = syl_cache[text]
        notes = line["notes"]
        if len(syl) != len(notes):
            fail(f"line {i + 1} has {len(notes)} notes for {len(syl)} syllables")
        base = int(line["bar"]) * 4 + float(line.get("beat", 0))
        for note, s in zip(notes, syl):
            events.append((base + float(note["beat"]), float(note["duration"]), int(note["midi"])))
            syllables.append(s)
    events.sort(key=lambda e: e[0])
    notes_json = []
    cursor = 0.0
    for start, dur, midi in events:
        if start < cursor - 1e-4:
            fail("vocal notes overlap; give each syllable a distinct onset")
        if start > cursor + 1e-4:
            notes_json.append([0, round(start - cursor, 6)])
        notes_json.append([midi, round(dur, 6)])
        cursor = start + dur
    total_beats = sum(s["bars"] for s in arr["sections"]) * 4
    notes_json.append([0, max(0.5, total_beats - cursor + 0.2)])
    return {
        "bpm": bpm,
        "t0": 0.0,
        "vibrato": vibrato,
        "lyrics": " ".join(s["text"] for s in syllables),
        "notes": notes_json,
        "syllables": syllables,
        "body": body,
    }


def render_vocal(arr: dict, lines: list[dict], body: str, out: Path, work: Path, label: str, vibrato: float,
                 syl_cache: dict) -> Path | None:
    if not lines:
        return None
    doc = vocal_document(arr, lines, body, vibrato, syl_cache)
    src = work / f"vocal_{label}.json"
    src.write_text(json.dumps(doc, indent=1))
    run([str(CHIMERA), "render", "notes", str(src), str(out), body], what=f"CHIMERA {label}")
    return out


# ---------------------------------------------------------------------------
# master
# ---------------------------------------------------------------------------
def master(arr: dict, instrumental: Path, lead: Path, harmony: Path | None, out: Path, duration: float):
    mix = arr.get("mix") or {}
    # Defaults are intentionally vocal-forward compared with the first lab mix:
    # CHIMERA is quiet in context, so −6 dB reads as present without burying the band.
    lead_db = max(-18.0, min(6.0, float(mix.get("vocal_gain_db", 2.0))))
    harm_db = max(-24.0, min(-6.0, float(mix.get("harmony_gain_db", -12.0))))
    inst_offset_db = max(-12.0, min(6.0, float(mix.get("instrumental_gain_db", -1.5))))
    # Lift the band to a healthy peak, then let the user offset it. This keeps the
    # fixed vocal gain meaningful even when a model writes a quiet arrangement.
    inst_peak = analyse(instrumental).get("peak_db")
    auto_inst_db = max(-4.0, min(14.0, -6.0 - inst_peak)) if inst_peak is not None else 0.0
    instrumental_gain_db = auto_inst_db + inst_offset_db
    lead_gain = 10 ** (lead_db / 20.0)
    harm_gain = 10 ** (harm_db / 20.0)
    inst_gain = 10 ** (instrumental_gain_db / 20.0)
    cmd = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(instrumental), "-i", str(lead)]
    if harmony:
        cmd += ["-i", str(harmony)]
    lead_chain = (
        "highpass=f=105,lowpass=f=10800,"
        "equalizer=f=240:t=q:w=1.1:g=-3.2,"
        "equalizer=f=900:t=q:w=1.0:g=-1.0,"
        "equalizer=f=3200:t=q:w=1.2:g=2.2,"
        "equalizer=f=7600:t=q:w=1.0:g=-1.8,"
        "acompressor=threshold=0.12:ratio=3:attack=8:release=130:makeup=1.0,"
        "aecho=0.8:0.55:54|112|210:0.16|0.09|0.045,"
        f"volume={lead_gain:.5f}"
    )
    filters = [
        f"[0:a]volume={inst_gain:.5f}[inst]",
        f"[1:a]{lead_chain}[lead]",
    ]
    if harmony:
        harm_chain = (
            "highpass=f=150,lowpass=f=9000,"
            "equalizer=f=300:t=q:w=1.2:g=-3.0,"
            "equalizer=f=2800:t=q:w=1.2:g=1.2,"
            "acompressor=threshold=0.15:ratio=2.5:attack=10:release=150,"
            "aecho=0.8:0.5:70|140:0.12|0.07,"
            f"volume={harm_gain:.5f},adelay=14|22"
        )
        filters.append(f"[2:a]{harm_chain}[harm]")
        mix_inputs = "[inst][lead][harm]amix=inputs=3:normalize=0:duration=first"
    else:
        mix_inputs = "[inst][lead]amix=inputs=2:normalize=0:duration=first"
    filters.append(
        f"{mix_inputs},"
        f"alimiter=level_in=1:limit=0.891:attack=5:release=90,"
        f"loudnorm=I=-15:TP=-1.5:LRA=10,"
        f"alimiter=limit=0.94,aresample=48000[out]"
    )
    cmd += ["-filter_complex", ";".join(filters), "-map", "[out]", "-t", f"{duration:.3f}",
            "-ac", "2", "-c:a", "pcm_s16le", str(out)]
    run(cmd, what="final mix")


def analyse(path: Path) -> dict:
    probe = run([str(FFPROBE), "-v", "error", "-show_entries",
                 "format=duration,size", "-of", "json", str(path)], what="ffprobe")
    try:
        fmt = json.loads(probe.stdout)["format"]
    except Exception:
        fmt = {}
    vol = run([str(FFMPEG), "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
              what="peak analysis")
    peak = None
    for line in (vol.stderr or "").splitlines():
        if "max_volume:" in line:
            try:
                peak = float(line.split("max_volume:")[1].strip().split()[0])
            except Exception:
                pass
    return {"duration": float(fmt.get("duration", 0)), "bytes": int(fmt.get("size", 0)), "peak_db": peak}


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_dir")
    ap.add_argument("--validate", action="store_true")
    args = ap.parse_args()
    job = Path(args.job_dir).resolve()
    arrangement_path = job / "arrangement.json"
    if not arrangement_path.exists():
        fail(f"missing {arrangement_path}")
    arr = load_arrangement(arrangement_path)
    problems = validate(arr, deep=True)
    if problems:
        if args.validate:
            print(json.dumps({"ok": False, "errors": problems}, ensure_ascii=False))
            raise SystemExit(2)
        fail("arrangement validation failed: " + " | ".join(problems[:6]))
    if args.validate:
        print(json.dumps({"ok": True, "errors": []}, ensure_ascii=False))
        return

    if not CHIMERA.exists():
        fail(f"CHIMERA not found at {CHIMERA}. Set CHIMERA_BIN.")
    if not Path(FLUIDSYNTH).exists():
        fail(f"FluidSynth not found at {FLUIDSYNTH}. Set FLUIDSYNTH_BIN.")
    if not Path(FFMPEG).exists():
        fail(f"FFmpeg not found at {FFMPEG}. Set FFMPEG_BIN.")
    sf2 = find_sf2()

    sections, by_bar, bars = section_map(arr)
    chords = chord_list(arr, bars)
    work = job / "work"
    work.mkdir(parents=True, exist_ok=True)
    bpm = float(arr["bpm"])
    duration = bars * 4 * 60.0 / bpm
    progress(38, "Scoring the arrangement")
    instrumental = job / "instrumental.wav"
    build_instrumental(arr["tracks"], sections, by_bar, chords, bars, bpm, instrumental, work, sf2)

    progress(80, "Teaching CHIMERA the melody")
    vocal = arr["vocal"]
    lead = job / "lead.wav"
    syl_cache: dict[str, list[dict]] = {}
    render_vocal(arr, vocal.get("lead", []), arr.get("voice", "female"), lead, work, "lead", 27.0, syl_cache)
    harmony_lines = vocal.get("harmony") or []
    harmony = None
    if harmony_lines:
        progress(86, "Adding a quiet harmony")
        harmony = job / "harmony.wav"
        render_vocal(arr, harmony_lines, arr.get("harmony_voice", arr.get("voice", "female")),
                     harmony, work, "harmony", 22.0, syl_cache)

    progress(92, "Mixing, EQ and limiting")
    song_wav = job / "song.wav"
    master(arr, instrumental, lead, harmony, song_wav, duration)

    progress(97, "Encoding the MP3")
    song_mp3 = job / "song.mp3"
    run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-i", str(song_wav),
         "-codec:a", "libmp3lame", "-b:a", "192k", str(song_mp3)], what="MP3 encode")

    report = {
        "title": arr.get("title"),
        "bpm": bpm,
        "key": arr.get("key"),
        "bars": bars,
        "sections": [{"name": s["name"], "start_bar": s["start_bar"], "bars": s["bars"]} for s in sections],
        "vocal_notes": sum(len(line.get("notes", [])) for line in vocal.get("lead", [])),
        "harmony_notes": sum(len(line.get("notes", [])) for line in harmony_lines),
        "files": {p.name: analyse(p) for p in (lead, harmony, instrumental, song_wav, song_mp3) if p and p.exists()},
    }
    (work / "render_report.json").write_text(json.dumps(report, indent=2))
    progress(100, "Master ready")
    print(json.dumps({"ok": True, "duration": report["files"].get("song.wav", {}).get("duration"), "report": report}, ensure_ascii=False))


if __name__ == "__main__":
    main()
