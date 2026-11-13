# SongForge

A small local music laboratory. Write an English lyric, describe the feeling, press **Generate Song**, and listen to a finished track.

一间小型本地音乐实验室。写一段英文歌词，描述你想要的感觉，点击生成，然后听一首完整的歌。

[English](#english) · [中文](#中文)

---

## English

### What it is

SongForge asks an OpenAI-compatible model for a complete arrangement — BPM, key, form, chords, melody notes, lyric-to-note mapping, instrument tracks and section patterns. A local Node process then:

1. validates the arrangement and the lyric/syllable mapping;
2. uses **SCAMP** to write the band parts to MIDI;
3. renders those MIDI parts to audio with **FluidSynth** and a SoundFont;
4. uses **CHIMERA** to sing the lead (and an optional harmony) from real lyric syllables;
5. mixes with **FFmpeg** — vocal EQ, short room ambience, adjustable vocal/band levels and a final limiter;
6. writes a 48 kHz WAV, a 192 kbps MP3 and the arrangement JSON.

No React, no database, no accounts, no cloud service. Node is the web server; Python is only an internal renderer.

The interface is bilingual (English / 中文) and runs entirely on your machine.

### Preview

Two previews of the same original song are included:

- [`output/paper-lanterns/`](output/paper-lanterns/) — CHIMERA male lead
- [`output/paper-lanterns-female/`](output/paper-lanterns-female/) — CHIMERA female lead

```text
output/paper-lanterns/
├── arrangement.json      # the exact score, reusable for an offline re-render
└── song.mp3              # 192 kbps preview master
```

The repository keeps only MP3 + score for the previews, to stay light. A real render also writes:

```text
output/<song>/
├── arrangement.json
├── lead.wav
├── harmony.wav
├── instrumental.wav
├── song.wav
└── song.mp3
```

It is a 2:23 English song at 104 BPM in D major, with 30 lead lines and 12 harmony lines. The default mix is vocal **+2 dB**, harmony **−12 dB**, band **−1.5 dB**.

### Requirements

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

### Install and run

```bash
git clone https://github.com/AlbertYang514/songforge.git
cd songforge
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm start
```

`npm start` opens <http://127.0.0.1:4173> in the browser automatically. Set `SONGFORGE_OPEN=0` to disable that.

On macOS you can also double-click **`SongForge.command`**. If the server is already running it simply opens the page; otherwise it starts the server first.

`server.js` automatically uses `.venv/bin/python` when it exists. Otherwise it uses `python3`.

### Using the page

1. Paste multi-line English lyrics.
2. Describe the arrangement in natural language. Mention instruments, energy, verse/chorus contrast and vocal character.
3. Choose a male or female CHIMERA lead. Set vocal, harmony and band levels if needed.
4. Open **API Settings**:
   - **Base URL** — any OpenAI-compatible endpoint, e.g. `https://api.openai.com/v1` or a self-hosted gateway.
   - **API key** — stays in this tab's memory. It is sent to the local Node process for the request only. It is never written to localStorage, a file, a log or the generated output. Refresh and it is gone.
   - **Model** — free text, e.g. `gpt-4.1-mini`, `deepseek-chat`, or a local model name.
   - **Test connection** — sends one tiny chat request and shows the real error if it fails.
5. Press **Generate Song**. The page streams stage/progress messages while the local renderer works.
6. Play the master, download MP3/WAV/instrumental, or open the arrangement JSON.

There is a **use a sample** link that loads the lyric and brief used for the included example.

### Environment variables

All paths can be overridden with environment variables; secrets are never stored in code.

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

### Re-render without an LLM

The saved arrangement is the score. To rebuild the audio entirely offline:

```bash
.venv/bin/python render_song.py output/paper-lanterns
```

Add `--validate` to check the JSON, bar coverage and CHIMERA syllable counts without rendering:

```bash
.venv/bin/python render_song.py output/paper-lanterns --validate
```

This is also the quickest way to test the toolchain before configuring an API key.

### How the audio is made

- **SCAMP** turns the JSON's chords and section patterns into separate MIDI stems: piano, guitar, bass, strings, texture, drums. Patterns change by section, with deterministic humanisation and fills at section ends.
- **FluidSynth** renders every MIDI stem through a SoundFont. MIDI is never passed off as audio.
- **CHIMERA** sings one note per syllable from the lyric line, so the words and melody come from the same validated mapping. Missing lyric notes are a hard error.
- **FFmpeg** mixes the band, applies a static instrumental lift, then places the vocal according to the mix controls:
  - lead default **+2 dB**, harmony **−12 dB**, band **−1.5 dB** (relative to the arranged band);
  - high-pass and gentle EQ to remove rumble/mud and soften CHIMERA's mechanical top end;
  - short multi-tap room ambience rather than a dry, robotic voice;
  - a final limiter with a peak ceiling near −1 dBFS and MP3/WAV export.

### Arrangement JSON

The model returns (and the UI displays) a score like this:

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

### Security and scope

- The server binds to `127.0.0.1` by default and rejects non-local `Host`/`Origin` requests.
- It is a local tool. **Do not expose this port to the internet** — it can proxy requests with a key supplied by the caller.
- There is no account system, database, job persistence or queue. Jobs live in memory; audio stays under `output/`.
- Generated files and API keys are never mixed. Keys are not logged.
- The system prompt forbids imitating named artists or reproducing existing lyrics. User references to an artist are translated into general musical traits.
- See [SECURITY.md](SECURITY.md) for the local-only safety model.

### Troubleshooting

- **CHIMERA not found** — set `CHIMERA_BIN` to the built CLI.
- **No SoundFont** — set `SONGFORGE_SF2` to a `.sf2` file. SCAMP's `Merlin.sf2` is usually in `site-packages/scamp/soundfonts/`.
- **FluidSynth not found** — install `fluid-synth`, or set `FLUIDSYNTH_BIN`.
- **SCAMP import error** — the Node process is using the wrong Python. Install `requirements.txt` in `.venv` or set `SONGFORGE_PYTHON`.
- **API 404** — Base URL usually ends in `/v1`, not `/v1/chat/completions`. Both forms are accepted.
- **Bad model JSON after two tries** — the error panel includes the validation reasons. Try a stronger instruction-following model, or a slightly simpler brief.
- Intermediate MIDI, stems, CHIMERA note JSON and logs remain in `output/<song>/work/` after a failed render.

### License

MIT. See [LICENSE](LICENSE).

CHIMERA, SCAMP, FluidSynth, FFmpeg, the SoundFont and the model API are external tools and keep their own licenses. None of them are bundled here.

---

## 中文

### 是什么

SongForge 会把歌词和自然语言描述交给一个 OpenAI-compatible 模型，生成完整的编曲 JSON：BPM、调性、段落、和弦、主唱旋律、歌词与音符的对应、乐器轨道和段落 pattern。随后本机 Node 进程会：

1. 校验编曲、段落拍数、音域和人声歌词/音节对应；
2. 用 **SCAMP** 把伴奏写成 MIDI 分轨；
3. 用 **FluidSynth** 和 SoundFont 把 MIDI 渲染成真实音频；
4. 用 **CHIMERA** 根据真实歌词音节合成主唱和可选和声；
5. 用 **FFmpeg** 做人声 EQ、短混响、主唱/伴奏比例和最终限幅；
6. 输出 48 kHz WAV、192 kbps MP3 和编曲 JSON。

没有 React、数据库、账号或云服务。Node 是网页服务，Python 只是内部渲染器。界面支持中英文切换，全部在本机运行。

### 示例

仓库里有两版同一首原创歌曲的预览：

- [`output/paper-lanterns/`](output/paper-lanterns/) — CHIMERA 男声主唱
- [`output/paper-lanterns-female/`](output/paper-lanterns-female/) — CHIMERA 女声主唱

仓库只保留预览 MP3 和编曲 JSON，避免仓库过大。实际生成时还会得到：

```text
output/<song>/
├── arrangement.json
├── lead.wav
├── harmony.wav
├── instrumental.wav
├── song.wav
└── song.mp3
```

示例是 104 BPM、D 大调、2:23 的英文歌，30 行主唱、12 行和声。默认混音：主唱 **+2 dB**、和声 **−12 dB**、伴奏 **−1.5 dB**。

### 依赖

- Node.js 20 或更新
- Python 3.11–3.13，安装 SCAMP 和 mido
- FFmpeg / FFprobe
- FluidSynth
- CHIMERA 的 `chimera` CLI
- 一个 GM SoundFont（SCAMP 自带 `Merlin.sf2`，程序会自动查找）

macOS + Homebrew：

```bash
brew install node python@3.13 ffmpeg fluid-synth
```

CHIMERA 需要单独编译。默认查找路径：

```text
~/chimera-music/chimera-vocal-synth/target/release/chimera
```

### 安装与运行

```bash
git clone https://github.com/AlbertYang514/songforge.git
cd songforge
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm start
```

`npm start` 会自动在浏览器打开 <http://127.0.0.1:4173>。不想自动打开就设置 `SONGFORGE_OPEN=0`。

macOS 上也可以直接双击 **`SongForge.command`**：服务没开就启动服务，已经开着就只打开页面。

`server.js` 会优先使用 `.venv/bin/python`，没有则使用 `python3`。

### 使用

1. 粘贴多行英文歌词。
2. 用自然语言描述编曲：乐器、情绪、主歌/副歌对比、人声质感。
3. 选择男声或女声 CHIMERA 主唱，按需调整主唱、和声、伴奏音量。
4. 展开 **API 设置**：
   - **Base URL**：任意 OpenAI-compatible 地址，例如 `https://api.openai.com/v1` 或自建网关。
   - **API Key**：只保存在当前标签页内存里，只会发给本机 Node 进程用于本次请求；不写入源码、localStorage、日志或生成文件。刷新即消失。
   - **模型**：自由填写，例如 `gpt-4.1-mini`、`deepseek-chat` 或本地模型名。
   - **测试连接**：发一个很小的请求，失败时显示具体错误。
5. 点击 **生成歌曲**。页面会实时显示阶段和进度。
6. 播放成品，下载 MP3/WAV/伴奏，或打开编曲 JSON。

**使用示例** 按钮会填入内置示例的歌词和描述。

### 环境变量

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | 保持本地访问 |
| `PORT` | `4173` | 网页端口 |
| `SONGFORGE_PYTHON` | `.venv/bin/python`，否则 `python3` | 带 SCAMP 的 Python |
| `CHIMERA_BIN` | 见上 | CHIMERA 可执行文件 |
| `FLUIDSYNTH_BIN` | `PATH` 中的 `fluidsynth` | MIDI 渲染器 |
| `SONGFORGE_SF2` | 自动查找 | SoundFont 文件 |
| `FFMPEG_BIN` / `FFPROBE_BIN` | `ffmpeg` / `ffprobe` | 音频工具 |
| `SONGFORGE_DEFAULT_BASE_URL` | `https://api.openai.com/v1` | 页面默认 Base URL |
| `SONGFORGE_DEFAULT_MODEL` | `gpt-4.1-mini` | 页面默认模型 |
| `SONGFORGE_OPEN` | 未设置（开启） | 设为 `0` 时 `npm start` 不自动开浏览器 |

### 不调用 LLM 重新渲染

编曲 JSON 就是乐谱，可以直接离线重渲：

```bash
.venv/bin/python render_song.py output/paper-lanterns
```

只校验、不渲染：

```bash
.venv/bin/python render_song.py output/paper-lanterns --validate
```

配置 API Key 之前，可以先用这个方式检查本地工具链。

### 音频流程

- **SCAMP** 把和弦和段落 pattern 写成钢琴、吉他、贝斯、弦乐、texture、鼓等 MIDI 分轨；pattern 会随段落变化，段落末尾有 fill。
- **FluidSynth** 用 SoundFont 渲染每一轨。MIDI 不会被直接当作音频。
- **CHIMERA** 每个音节唱一个音符，歌词和旋律来自同一份已校验的映射；缺歌词音符会直接报错。
- **FFmpeg** 混合伴奏并按滑块放置人声：默认主唱 **+2 dB**、和声 **−12 dB**、伴奏 **−1.5 dB**；做人声高通、适度 EQ、短房间混响，最后限幅到约 −1 dBFS，输出 WAV/MP3。

### 编曲 JSON

字段与英文部分示例一致：

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

会校验：段落小节、每小节一个和弦、音符音域、人声时序、轨道 pattern、歌词是否完整覆盖，以及 CHIMERA 对每行歌词的音节数。格式不合格会自动带错误重试一次。

### 安全与边界

- 默认只监听 `127.0.0.1`，并拒绝非本机的 `Host` / `Origin` 请求。
- 这是本机工具。**不要把端口暴露到公网**：它会使用调用方提供的 Key 转发模型请求。
- 没有账号、数据库、任务持久化或队列。任务只在内存里，音频写在 `output/`。
- API Key 不会进入生成文件，也不会写日志。
- System Prompt 禁止模仿具体艺人或复制现有歌词；用户提到的艺人风格会被翻译成通用音乐特征。
- 更多说明见 [SECURITY.md](SECURITY.md)。

### 常见问题

- **找不到 CHIMERA** — 设置 `CHIMERA_BIN` 指向编译好的 CLI。
- **没有 SoundFont** — 设置 `SONGFORGE_SF2`。SCAMP 的 `Merlin.sf2` 通常在 `site-packages/scamp/soundfonts/`。
- **找不到 FluidSynth** — 安装 `fluid-synth`，或设置 `FLUIDSYNTH_BIN`。
- **SCAMP 导入错误** — Node 用错了 Python。在 `.venv` 安装 `requirements.txt`，或设置 `SONGFORGE_PYTHON`。
- **API 404** — Base URL 通常以 `/v1` 结尾，不是 `/v1/chat/completions`；两种写法都支持。
- **模型连续两次 JSON 校验失败** — 错误面板会列出原因。换一个指令跟随更好的模型，或把需求写简单一点。
- 渲染失败时，MIDI、分轨、CHIMERA 音符 JSON 和日志会留在 `output/<song>/work/`，方便排查。

### 许可证

MIT，见 [LICENSE](LICENSE)。

CHIMERA、SCAMP、FluidSynth、FFmpeg、SoundFont 和模型 API 都是外部工具，遵循各自许可证；本仓库不打包它们。
