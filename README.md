# SongForge

A small local music laboratory. Write an English lyric, describe the feeling, press **Generate Song**, and listen to a finished track.

SongForge asks an OpenAI-compatible model for a complete arrangement (BPM, key, form, chords, melody notes, lyric-to-note mapping, instrument tracks and section patterns). The server then:

1. validates the arrangement and the lyric/syllable mapping;
2. uses **SCAMP** to write the band parts to MIDI;
3. renders those MIDI parts to audio with **FluidSynth** and a SoundFont;
4. uses **CHIMERA** to sing the lead (and an optional harmony) from real lyric syllables;
5. mixes with **FFmpeg** — vocal EQ, short room ambience, adjustable vocal/band levels and a final limiter;
6. writes a WAV, a 192 kbps MP3 and the arrangement JSON.

No React, no database, no accounts, no cloud service. Node is the web server; Python is only an internal renderer.

## Example output

The repository includes two previews of the same original song:

- [`output/paper-lanterns/`](output/paper-lanterns/) — CHIMERA male lead
- [`output/paper-lanterns-female/`](output/paper-lanterns-female/) — CHIMERA female lead

```text
output/paper-lanterns/
├── arrangement.json      # the exact score, reusable for an offline re-render
└── song.mp3              # 192 kbps preview master
```

The previews are checked in as MP3 + score only, to keep the repository light. A real render writes the full set locally:

```text
output/<song>/
├── arrangement.json
├── lead.wav
├── harmony.wav
├── instrumental.wav
├── song.wav
└── song.mp3
```

It is a 2:23 English song at 104 BPM in D major, with 30 lead lines and 12 harmony lines. The default mix is vocal +2 dB, harmony −12 dB, band −1.5 dB.

## Requirements

- Node.js 20 or newer
- Python 3.11–3.13 with SCAMP and mido
- FFmpeg / FFprobe
- FluidSynth
- CHIMERA's `chimera` CLI
- A GM SoundFont (SCAMP ships `Merlin.sf2`; the app finds it automatically)

On macOS with Homebrew:

```bash
brew install node python@3.13 ffmpeg fluid-synth
```

CHIMERA must be built separately. Its default expected location is:

```text
~/chimera-music/chimera-vocal-synth/target/release/chimera
```

## Install and run

```bash
cd songforge
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm start
```

`npm start` opens <http://127.0.0.1:4173> in the browser automatically. Set `SONGFORGE_OPEN=0` to disable that.

On macOS you can also double-click `SongForge.command`. If the server is already running it simply opens the page; otherwise it starts the server first. The launcher is a convenience wrapper, not a second app.

`server.js` automatically uses `.venv/bin/python` when it exists. Otherwise it uses `python3`.

## Using the page

1. Paste multi-line English lyrics.
2. Describe the arrangement in natural language. Mention instruments, energy, verse/chorus contrast, vocal character.
3. Open **API Settings**:
   - **Base URL** — any OpenAI-compatible endpoint, e.g. `https://api.openai.com/v1` or a self-hosted gateway.
   - **API key** — stays in this tab's memory. It is sent to the local Node process for the request only. It is never written to localStorage, a file, a log or the generated output. Refresh and it is gone.
   - **Model** — free text, e.g. `gpt-4.1-mini`, `deepseek-chat`, or a local model name.
   - **Test connection** — sends one tiny chat request and shows the real error if it fails.
4. Press **Generate Song**. The page streams stage/progress messages while the local renderer works.
5. Play the master, download MP3/WAV/instrumental, or open the arrangement JSON.

There is a **use a sample** link that loads the lyric and brief used for the included example.

## Configuration

All paths can be overridden with environment variables; nothing secret is stored in code.

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | Keep this local |
| `PORT` | `4173` | Web port |
| `SONGFORGE_PYTHON` | `.venv/bin/python`, else `python3` | Python with SCAMP |
| `CHIMERA_BIN` | see above | CHIMERA executable |
| `FLUIDSYNTH_BIN` | `fluidsynth` on `PATH` | MIDI renderer |
| `SONGFORGE_SF2` | automatic search | SoundFont file |
| `FFMPEG_BIN` / `FFPROBE_BIN` | `ffmpeg` / `ffprobe` | Audio tools |
| `SONGFORGE_DEFAULT_BASE_URL` | `https://api.openai.com/v1` | Page default |
| `SONGFORGE_DEFAULT_MODEL` | `gpt-4.1-mini` | Page default |
| `SONGFORGE_OPEN` | unset (enabled) | Set to `0` to stop `npm start` opening a browser |

Example:

```bash
CHIMERA_BIN="$HOME/chimera-music/chimera-vocal-synth/target/release/chimera" \
SONGFORGE_SF2="/path/to/Merlin.sf2" \
npm start
```

## Re-render without an LLM

The saved arrangement is the score. To rebuild the audio entirely offline:

```bash
.venv/bin/python render_song.py output/paper-lanterns
```

Add `--validate` to check the JSON, bar coverage and CHIMERA syllable counts without rendering:

```bash
.venv/bin/python render_song.py output/paper-lanterns --validate
```

This is also the quickest way to test the toolchain before configuring an API key.

## How the audio is made

- **SCAMP** turns the JSON's chords and section patterns into separate MIDI stems: piano, guitar, bass, strings, texture, drums. Patterns change by section, with deterministic humanisation and fills at section ends.
- **FluidSynth** renders every MIDI stem through a SoundFont. MIDI is never passed off as audio.
- **CHIMERA** sings one note per syllable from the lyric line, so the words and melody come from the same validated mapping. Missing lyric notes are a hard error.
- **FFmpeg** mixes the band, applies a static instrumental lift, then places the vocal underneath:
  - lead default **+2 dB**, harmony **−12 dB**, band **−1.5 dB** (relative to the arranged band); use the **Mix balance** sliders before generating, or edit `arrangement.json`'s `mix` block for an offline re-render;
  - high-pass and gentle EQ to remove rumble/mud and soften CHIMERA's mechanical top end;
  - short multi-tap room ambience rather than a dry, robotic voice;
  - a final limiter with a peak ceiling near −1 dBFS and MP3/WAV export.

The default balance puts the voice slightly above the band reference while keeping the drums and bass clear. The **Mix balance** sliders make that adjustable; lowering a flawed CHIMERA render is never treated as a fix for a broken synthesis.

## Arrangement JSON

The model returns (and the UI displays) a score like:

```json
{
  "title": "Paper Lanterns",
  "bpm": 104,
  "key": "D major",
  "time_signature": "4/4",
  "sections": [{"name": "Verse 1", "bars": 8, "energy": 0.4}],
  "chords": [{"bar": 4, "symbol": "D", "notes": [50, 57, 62, 66]}],
  "vocal": {
    "lead": [{
      "bar": 4, "beat": 0, "text": "Streetlights hum on an empty street",
      "notes": [{"beat": 0, "duration": 0.5, "midi": 57}]
    }],
    "harmony": []
  },
  "tracks": [{
    "name": "Piano", "role": "chords", "program": 0, "gain_db": -10,
    "patterns": {"Verse 1": "block", "Chorus 1": "stabs_8"}
  }],
  "mix": {"vocal_gain_db": 2, "harmony_gain_db": -12, "instrumental_gain_db": -1.5}
}
```

Validation checks section bars, one chord per bar, note ranges, vocal timing, track patterns, exact lyric coverage and CHIMERA's syllable count for every sung line. A bad model response is retried once with the validation errors attached.

## Security / scope

- The server binds to `127.0.0.1` by default and rejects non-local `Host`/`Origin` requests.
- It is a local tool. **Do not expose this port to the internet** — it can proxy requests with a key supplied by the caller.
- There is no account system, database, job persistence or queue. Jobs live in memory; audio stays under `output/`.
- Generated files and API keys are never mixed. Keys are not logged.
- The system prompt forbids imitating named artists or reproducing existing lyrics. User references to an artist are translated into general musical traits.

## Troubleshooting

- **CHIMERA not found** — set `CHIMERA_BIN` to the built CLI.
- **No SoundFont** — set `SONGFORGE_SF2` to a `.sf2` file. SCAMP's `Merlin.sf2` is usually in `site-packages/scamp/soundfonts/`.
- **FluidSynth not found** — install `fluid-synth`, or set `FLUIDSYNTH_BIN`.
- **SCAMP import error** — the Node process is using the wrong Python. Install `requirements.txt` in `.venv` or set `SONGFORGE_PYTHON`.
- **API 404** — Base URL usually ends in `/v1`, not `/v1/chat/completions`. Both forms are accepted.
- **Bad model JSON after two tries** — the error panel includes the validation reasons. Try a stronger instruction-following model, or a slightly simpler brief.
- Intermediate MIDI, stems, CHIMERA note JSON and logs remain in `output/<song>/work/` after a failed render.


## License

MIT. See [LICENSE](LICENSE).

CHIMERA, SCAMP, FluidSynth, FFmpeg, the SoundFont and the model API are external tools and keep their own licenses. None of them are bundled here.
