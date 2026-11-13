# Changelog

## 0.1.2 — 2026-11-13

- README is now fully bilingual (English and 中文).
- License: MIT.

## 0.1.1 — 2026-11-13

- `npm start` opens the local page automatically; set `SONGFORGE_OPEN=0` to disable.
- Added the macOS `SongForge.command` double-click launcher.

## 0.1.0 — 2026-11-13

First public release.

- Single-page local studio: English lyrics + a natural-language brief.
- Any OpenAI-compatible chat endpoint; key stays in page memory and is proxied by the local Node process only.
- Strict arrangement JSON with section bars, chords, lyric-to-note melody, instrument patterns and mix settings.
- Validation for bars, note ranges, overlapping vocal notes, lyric coverage and CHIMERA syllable counts; one automatic model retry.
- SCAMP arrangement → MIDI stems → FluidSynth render.
- CHIMERA lead vocal and optional harmony, one note per real lyric syllable.
- FFmpeg vocal EQ, short room ambience, limiter and 192 kbps MP3 / 48 kHz WAV export.
- Bilingual English / Chinese interface.
- Mix balance controls for vocal, harmony and band.
- Two checked-in preview examples: male and female lead.
