#!/usr/bin/env node
/**
 * SongForge — a tiny local music lab.
 *
 * Node owns the page, the OpenAI-compatible conversation and the job state.
 * Python is only an internal renderer invoked for SCAMP/CHIMERA/FFmpeg work.
 * There is no database, account or queue: jobs live in this process only.
 */
"use strict";

const http = require("http");
const fs = require("fs");
const fsp = require("fs/promises");
const path = require("path");
const os = require("os");
const { spawn, execFile } = require("child_process");
const crypto = require("crypto");

const ROOT = __dirname;
const OUTPUT = path.join(ROOT, "output");
const HOST = process.env.HOST || "127.0.0.1";
const PORT = Number(process.env.PORT || 4173);
const PYTHON = process.env.SONGFORGE_PYTHON ||
  (fs.existsSync(path.join(ROOT, ".venv", "bin", "python")) ? path.join(ROOT, ".venv", "bin", "python") : "python3");
const CHIMERA = process.env.CHIMERA_BIN ||
  path.join(os.homedir(), "chimera-music/chimera-vocal-synth/target/release/chimera");
const RENDER = path.join(ROOT, "render_song.py");
const DEFAULT_BASE = process.env.SONGFORGE_DEFAULT_BASE_URL || "https://api.openai.com/v1";
const DEFAULT_MODEL = process.env.SONGFORGE_DEFAULT_MODEL || "gpt-4.1-mini";

const jobs = new Map();
const AUDIO_FILES = new Set(["song.mp3", "song.wav", "instrumental.wav", "lead.wav", "harmony.wav", "arrangement.json"]);
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".mp3": "audio/mpeg",
  ".wav": "audio/wav",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".svg": "image/svg+xml",
};

const SYSTEM_PROMPT = `You are SongForge's composer, arranger and lyricist. You write original, singable English pop songs and return a complete, playable arrangement as strict JSON. You never imitate a named artist or reproduce existing lyrics; translate any artist reference into general musical traits. Your job is not to give advice — it is to write the actual notes.

MUSICAL BRIEF
- Write a memorable pop song with a clear verse/pre-chorus/chorus contrast, natural English word stress, a strong chorus hook, and a bridge or final lift.
- Use deliberate harmonic motion (not a four-chord loop for the whole song). Change voicings, bass motion and rhythmic density between sections.
- Leave space. Do not stack instruments that have nothing to do. 3–6 accompaniment tracks is plenty.
- The vocal is the lead. Write a melody with a singable range (roughly MIDI 48–76), stepwise motion, a few leaps at emotional peaks, and rests between phrases. Avoid one pitch repeated mechanically.
- Drums and bass must create a pulse that evolves; fills at section ends are welcome.
- Every input lyric line must be used at least once and in order. You may repeat whole lines for a chorus, but never invent or alter words. The text of each sung line must be exactly one input line.
- Each vocal line's syllable count is supplied below. Supply exactly that many melody notes for the line, one note per syllable. Do not add melisma notes without syllables.
- The title should be short and evocative.
- Target roughly 44–64 bars (about 2–3 minutes), 4/4, BPM 76–132.

OUTPUT: Return ONLY one JSON object, no markdown, no comments, no prose. Shape:

{
  "title": "Paper Lanterns",
  "bpm": 104,
  "key": "D major",
  "time_signature": "4/4",
  "voice": "female",
  "sections": [
    {"name": "Intro", "bars": 4, "energy": 0.2, "description": "soft keys, no drums"},
    {"name": "Verse 1", "bars": 8, "energy": 0.45},
    {"name": "Pre-Chorus 1", "bars": 4, "energy": 0.62},
    {"name": "Chorus 1", "bars": 8, "energy": 0.85},
    {"name": "Verse 2", "bars": 8, "energy": 0.5},
    {"name": "Pre-Chorus 2", "bars": 4, "energy": 0.68},
    {"name": "Chorus 2", "bars": 8, "energy": 0.92},
    {"name": "Bridge", "bars": 4, "energy": 0.55},
    {"name": "Final Chorus", "bars": 8, "energy": 1.0},
    {"name": "Outro", "bars": 4, "energy": 0.25}
  ],
  "chords": [
    {"bar": 0, "symbol": "Dadd9", "notes": [50, 57, 62, 64]},
    {"bar": 1, "symbol": "A/C#", "notes": [49, 57, 61, 64]}
  ],
  "vocal": {
    "lead": [
      {"bar": 4, "beat": 0, "text": "Streetlights hum on an empty street",
       "notes": [
         {"beat": 0, "duration": 0.5, "midi": 57},
         {"beat": 0.5, "duration": 0.5, "midi": 59}
       ]}
    ],
    "harmony": [
      {"bar": 16, "beat": 0, "text": "Paper lanterns in the rain",
       "notes": [{"beat": 0, "duration": 1, "midi": 64}]}
    ]
  },
  "tracks": [
    {"name": "Piano", "role": "chords", "instrument": "piano", "program": 0, "gain_db": -10,
     "patterns": {"Intro": "broken_8", "Verse 1": "block", "Chorus 1": "stabs_8", "Outro": "block"}},
    {"name": "Bass", "role": "bass", "instrument": "bass", "program": 33, "gain_db": -6,
     "patterns": {"Intro": "none", "Verse 1": "root_quarter", "Chorus 1": "syncopated"}},
    {"name": "Drums", "role": "drums", "instrument": "drums", "program": 0, "gain_db": -4,
     "patterns": {"Intro": "none", "Verse 1": "pop_verse", "Chorus 1": "pop_chorus"}}
  ],
  "mix": {"vocal_gain_db": 2, "harmony_gain_db": -12, "instrumental_gain_db": -1.5}
}

HARD RULES
- "sections[].bars" are consecutive; chords must contain exactly one entry for every bar, starting at bar 0. Total bars = sum of section bars.
- Chord notes are sounding MIDI pitches (root included), 3–6 notes, MIDI 24–96. Write real voicings.
- vocal line bar is 0-based; beat is 0–3.99 within that bar. Note beats are relative to the line start; durations are in beats. Notes must not overlap and must stay inside the song.
- Each sung line must include every word of the chosen input line in order. Poetic contractions/apostrophes are allowed only if they appear in the input line.
- Use only these pattern names:
  chords: block, broken_8, arpeggio_8, stabs_8, pulse_8, sustained, none
  bass: root_quarter, root_eighth, octave_8, walking, syncopated, none
  drums: pop_verse, pop_chorus, half_time, build, outro, none
  texture: motif, arp_high, sustained, none
- Pattern maps use exact section names. Missing sections mean "none".
- Include at least one drums track and one bass track. "role" is one of chords, bass, drums, texture.
- Do not use empty tracks. Do not output NaN, comments or trailing commas.`;

function json(res, status, data, headers = {}) {
  const body = JSON.stringify(data);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    "Cache-Control": "no-store",
    ...headers,
  });
  res.end(body);
}

function errorMessage(error) {
  return error && error.message ? String(error.message).slice(0, 1200) : String(error).slice(0, 1200);
}

async function readBody(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 2_000_000) throw new Error("Request body is too large");
    chunks.push(chunk);
  }
  if (!chunks.length) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new Error("Request body must be valid JSON");
  }
}

function localOnly(req) {
  const host = String(req.headers.host || "");
  const hostname = host.split(":")[0];
  if (hostname !== "127.0.0.1" && hostname !== "localhost" && hostname !== "[::1]") return false;
  const origin = req.headers.origin;
  if (origin) {
    try {
      const u = new URL(origin);
      if (!["127.0.0.1", "localhost", "[::1]"].includes(u.hostname)) return false;
    } catch {
      return false;
    }
  }
  return true;
}

function cleanBase(value) {
  const raw = String(value || "").trim();
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("Base URL is not a valid URL");
  }
  if (!["http:", "https:"].includes(url.protocol)) throw new Error("Base URL must use http:// or https://");
  if (url.username || url.password) throw new Error("Do not put credentials in the Base URL");
  return raw.replace(/\/+$/, "");
}

function chatEndpoint(base) {
  const clean = cleanBase(base);
  return clean.endsWith("/chat/completions") ? clean : `${clean}/chat/completions`;
}

async function callChat({ baseURL, apiKey, model, messages, maxTokens = 9000, temperature = 0.75 }) {
  if (!apiKey) throw new Error("An API key is required");
  if (!model) throw new Error("A model name is required");
  const endpoint = chatEndpoint(baseURL);
  let response;
  try {
    response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({ model, messages, temperature, max_tokens: maxTokens }),
      signal: AbortSignal.timeout(180_000),
    });
  } catch (error) {
    if (error && error.name === "TimeoutError") throw new Error("The model API timed out after 180 seconds");
    throw new Error(`Could not reach the model API at ${endpoint}: ${errorMessage(error)}`);
  }
  const raw = await response.text();
  if (!response.ok) {
    let detail = raw.slice(0, 500);
    try {
      const parsed = JSON.parse(raw);
      detail = parsed.error?.message || parsed.message || detail;
    } catch {}
    throw new Error(`API ${response.status} ${response.statusText}: ${detail}`);
  }
  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    throw new Error("The model API returned a non-JSON response");
  }
  const content = data.choices?.[0]?.message?.content;
  if (typeof content !== "string" || !content.trim()) {
    throw new Error("The model API returned an empty message");
  }
  return { content, model: data.model || model };
}

function extractJson(text) {
  let s = String(text).trim();
  s = s.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "");
  const first = s.indexOf("{");
  const last = s.lastIndexOf("}");
  if (first >= 0 && last > first) s = s.slice(first, last + 1);
  return JSON.parse(s);
}

function parseLyrics(text) {
  const lines = String(text || "")
    .split(/\r?\n/)
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter(Boolean);
  if (lines.length < 2) throw new Error("Add at least two lines of lyrics");
  if (lines.length > 48) throw new Error("Keep the lyrics under 48 lines for this small lab");
  if (lines.join(" ").length > 4000) throw new Error("Lyrics are too long");
  return lines;
}

function normaliseLine(text) {
  return String(text)
    .toLowerCase()
    .replace(/[’‘]/g, "'")
    .replace(/[^a-z0-9' ]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function syllableCommand(line) {
  return new Promise((resolve, reject) => {
    execFile(CHIMERA, ["syllables", line], { timeout: 20_000, maxBuffer: 1_000_000 }, (error, stdout, stderr) => {
      if (error) return reject(new Error(`CHIMERA syllable lookup failed: ${(stderr || error.message).trim()}`));
      try {
        const data = JSON.parse(stdout);
        resolve(data.length);
      } catch {
        reject(new Error("CHIMERA returned an unreadable syllable list"));
      }
    });
  });
}

function basicValidation(arr, inputLines) {
  const errors = [];
  if (!arr || typeof arr !== "object" || Array.isArray(arr)) return ["Response must be a JSON object"];
  if (!String(arr.title || "").trim()) errors.push("title is required");
  const bpm = Number(arr.bpm);
  if (!(bpm >= 40 && bpm <= 190)) errors.push("bpm must be between 40 and 190");
  if ((arr.time_signature || "4/4") !== "4/4") errors.push("time_signature must be 4/4");
  if (!Array.isArray(arr.sections) || !arr.sections.length) {
    errors.push("sections must be a non-empty array");
  }
  let bars = 0;
  const names = new Set();
  if (Array.isArray(arr.sections)) {
    for (const [i, sec] of arr.sections.entries()) {
      if (!String(sec?.name || "").trim()) errors.push(`section ${i + 1} needs a name`);
      if (!Number.isInteger(sec?.bars) || sec.bars < 1) errors.push(`section ${sec?.name || i + 1} needs integer bars`);
      else bars += sec.bars;
      if (names.has(sec?.name)) errors.push(`duplicate section name ${sec?.name}`);
      names.add(sec?.name);
      if (sec?.energy != null && !(Number(sec.energy) >= 0 && Number(sec.energy) <= 1)) errors.push(`section ${sec.name} energy must be 0..1`);
    }
  }
  if (bars < 16) errors.push("arrangement is shorter than 16 bars");
  if (bars > 96) errors.push("arrangement is longer than 96 bars");
  if (!Array.isArray(arr.chords)) {
    errors.push("chords must be an array");
  } else {
    const seen = new Set();
    for (const c of arr.chords) {
      if (!Number.isInteger(c?.bar) || c.bar < 0 || c.bar >= bars) errors.push(`chord bar ${c?.bar} is out of range`);
      if (seen.has(c?.bar)) errors.push(`duplicate chord for bar ${c?.bar}`);
      seen.add(c?.bar);
      if (!Array.isArray(c?.notes) || c.notes.length < 3 || c.notes.length > 6) errors.push(`chord at bar ${c?.bar} needs 3-6 notes`);
      else if (c.notes.some((n) => !Number.isFinite(Number(n)) || n < 24 || n > 96)) errors.push(`chord at bar ${c?.bar} has out-of-range notes`);
    }
    if (seen.size !== bars) errors.push(`chords must cover exactly ${bars} bars (got ${seen.size})`);
  }
  const vocal = arr.vocal;
  const lead = vocal?.lead;
  if (!Array.isArray(lead) || !lead.length) errors.push("vocal.lead must be a non-empty array");
  const all = [...(lead || []), ...(Array.isArray(vocal?.harmony) ? vocal.harmony : [])];
  for (const [i, line] of all.entries()) {
    if (!String(line?.text || "").trim()) errors.push(`vocal line ${i + 1} has no text`);
    if (!Number.isInteger(line?.bar) || line.bar < 0 || line.bar >= bars) errors.push(`vocal line ${i + 1} has an invalid bar`);
    if (!Array.isArray(line?.notes) || !line.notes.length) errors.push(`vocal line ${i + 1} has no notes`);
    else {
      let last = -Infinity;
      for (const [j, note] of line.notes.entries()) {
        if (!Number.isFinite(Number(note?.beat)) || !Number.isFinite(Number(note?.duration)) || !Number.isFinite(Number(note?.midi))) {
          errors.push(`vocal line ${i + 1} note ${j + 1} has invalid fields`);
          continue;
        }
        if (Number(note.beat) < last - 1e-6) errors.push(`vocal line ${i + 1} notes overlap`);
        last = Number(note.beat);
        if (Number(note.duration) <= 0) errors.push(`vocal line ${i + 1} note ${j + 1} has no duration`);
        if (Number(note.midi) < 36 || Number(note.midi) > 88) errors.push(`vocal line ${i + 1} note ${j + 1} midi outside 36-88`);
      }
    }
  }
  // lyric coverage: output lines may repeat input lines, but every input line appears in order
  const inputNorm = inputLines.map(normaliseLine);
  const inputSet = new Set(inputNorm);
  const outputNorm = (lead || []).map((line) => normaliseLine(line?.text));
  for (const line of outputNorm) {
    if (!inputSet.has(line)) errors.push(`vocal line not present in the submitted lyrics: "${line}"`);
  }
  let pointer = 0;
  for (const line of outputNorm) {
    if (line === inputNorm[pointer]) pointer += 1;
    if (pointer === inputNorm.length) break;
  }
  if (pointer < inputNorm.length) errors.push(`not every submitted lyric line appears in order; missing "${inputLines[pointer]}"`);
  // tracks
  const roles = new Set(["chords", "bass", "drums", "texture"]);
  const patterns = {
    chords: new Set(["block", "broken_8", "arpeggio_8", "stabs_8", "pulse_8", "sustained", "none"]),
    bass: new Set(["root_quarter", "root_eighth", "octave_8", "walking", "syncopated", "none"]),
    drums: new Set(["pop_verse", "pop_chorus", "half_time", "build", "outro", "none"]),
    texture: new Set(["motif", "arp_high", "sustained", "none"]),
  };
  if (!Array.isArray(arr.tracks) || !arr.tracks.length) errors.push("tracks must be a non-empty array");
  else {
    for (const [i, track] of arr.tracks.entries()) {
      if (!String(track?.name || "").trim()) errors.push(`track ${i + 1} needs a name`);
      if (!roles.has(track?.role)) errors.push(`track ${track?.name || i + 1} has unknown role`);
      const map = track?.patterns;
      if (!map || typeof map !== "object") errors.push(`track ${track?.name || i + 1} needs a patterns object`);
      else {
        for (const [sec, pat] of Object.entries(map)) {
          if (!names.has(sec)) errors.push(`track ${track.name} pattern references unknown section ${sec}`);
          if (patterns[track.role] && !patterns[track.role].has(pat)) errors.push(`track ${track.name} has invalid pattern ${pat}`);
        }
      }
      if (!Number.isFinite(Number(track?.program)) || Number(track.program) < 0 || Number(track.program) > 127) errors.push(`track ${track.name} program must be 0-127`);
    }
    if (!arr.tracks.some((t) => t?.role === "drums")) errors.push("add a drums track");
    if (!arr.tracks.some((t) => t?.role === "bass")) errors.push("add a bass track");
  }
  return errors;
}

function renderValidation(jobDir) {
  return new Promise((resolve) => {
    execFile(PYTHON, [RENDER, jobDir, "--validate"], { cwd: ROOT, timeout: 90_000, maxBuffer: 4_000_000 }, (error, stdout, stderr) => {
      try {
        const parsed = JSON.parse(String(stdout).trim().split("\n").filter(Boolean).pop());
        resolve({ ok: Boolean(parsed.ok), errors: parsed.errors || [] });
      } catch {
        resolve({ ok: false, errors: [`Renderer validation could not run: ${(stderr || error?.message || "unknown error").slice(0, 800)}`] });
      }
    });
  });
}

function slugify(title) {
  return String(title)
    .normalize("NFKD")
    .replace(/[^\x00-\x7F]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60) || "song";
}

async function uniqueDir(title) {
  await fsp.mkdir(OUTPUT, { recursive: true });
  const base = slugify(title);
  let candidate = base;
  let n = 2;
  while (fs.existsSync(path.join(OUTPUT, candidate))) candidate = `${base}-${n++}`;
  const dir = path.join(OUTPUT, candidate);
  await fsp.mkdir(dir, { recursive: false });
  return { dir, slug: candidate };
}

function mixValue(value, min, max, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(min, Math.min(max, n)) : fallback;
}

async function generateArrangement({ lyrics, style, api, voice, mix, job }) {
  const lines = parseLyrics(lyrics);
  if (!CHIMERA || !fs.existsSync(CHIMERA)) throw new Error(`CHIMERA not found at ${CHIMERA}. Set CHIMERA_BIN.`);
  job.stage = "Reading the lyric";
  job.message = "Counting syllables with CHIMERA";
  job.progress = 6;
  const counts = await Promise.all(lines.map((line) => syllableCommand(line)));
  const brief = lines.map((line, i) => `${i + 1}. [${counts[i]} syllables] ${line}`).join("\n");
  const baseUser = `LYRICS (keep every line, in order; use whole lines only; repeat a line only when it helps the hook):\n${brief}\n\nMUSICAL FEELING:\n${style}\n\nLEAD VOICE: ${voice}\n\nWrite the complete arrangement. The sum of section bars must equal the number of chord entries. Target 44-64 bars. Return JSON only.`;
  const messages = [
    { role: "system", content: SYSTEM_PROMPT },
    { role: "user", content: baseUser },
  ];

  job.stage = "Composing";
  job.message = "Asking the model for a full arrangement";
  job.progress = 12;
  let lastErrors = [];
  let lastContent = "";
  for (let attempt = 0; attempt < 2; attempt++) {
    const prompt = attempt === 0
      ? messages
      : [
          messages[0],
          {
            role: "user",
            content: `${baseUser}\n\nYOUR PREVIOUS RESPONSE FAILED VALIDATION:\n- ${lastErrors.slice(0, 12).join("\n- ")}\n\nFix those problems and return the complete corrected JSON object only. Do not explain.`,
          },
        ];
    job.progress = attempt === 0 ? 14 : 24;
    job.message = attempt === 0 ? "The model is writing the song" : "Fixing the arrangement with the model";
    const result = await callChat({ ...api, messages: prompt });
    lastContent = result.content;
    let arr;
    try {
      arr = extractJson(result.content);
    } catch (error) {
      lastErrors = [`The response was not valid JSON: ${errorMessage(error)}`];
      continue;
    }
    const structural = basicValidation(arr, lines);
    if (structural.length) {
      lastErrors = structural;
      continue;
    }
    const { dir, slug } = await uniqueDir(arr.title);
    arr.voice = voice;
    // The page's mix controls are authoritative; the model only writes the music.
    arr.mix = {
      vocal_gain_db: mixValue(mix?.vocal_gain_db, -18, 6, 2),
      harmony_gain_db: mixValue(mix?.harmony_gain_db, -24, -6, -12),
      instrumental_gain_db: mixValue(mix?.instrumental_gain_db, -12, 6, -1.5),
    };
    await fsp.writeFile(path.join(dir, "arrangement.json"), JSON.stringify(arr, null, 2));
    job.slug = slug;
    job.dir = dir;
    job.arrangement = arr;
    job.stage = "Checking the score";
    job.message = "Verifying syllables, bars and note ranges";
    job.progress = 30;
    const deep = await renderValidation(dir);
    if (deep.ok) return { arr, dir, slug };
    lastErrors = deep.errors;
    await fsp.rm(dir, { recursive: true, force: true });
    job.slug = null;
    job.dir = null;
  }
  const detail = lastErrors.length ? lastErrors.slice(0, 8).join(" | ") : "The model did not return a usable arrangement";
  throw new Error(`The arrangement failed validation after two attempts: ${detail}\n\nLast response started with: ${lastContent.slice(0, 300)}`);
}

function updateProgressFromLine(job, line) {
  const match = line.match(/^PROGRESS\s+(\d+)\s+(.*)$/);
  if (match) {
    job.progress = Math.max(job.progress, Math.min(99, Number(match[1])));
    job.message = match[2].trim();
    const stage = job.progress >= 92 ? "Mastering" : job.progress >= 80 ? "Singing" : job.progress >= 72 ? "Mixing" : job.progress >= 38 ? "Performing" : "Rendering";
    job.stage = stage;
  }
}

async function runRender(job) {
  job.status = "running";
  job.stage = "Performing";
  job.message = "SCAMP is writing the band parts";
  job.progress = 36;
  await new Promise((resolve, reject) => {
    const child = spawn(PYTHON, [RENDER, job.dir], {
      cwd: ROOT,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error("Rendering timed out after 12 minutes"));
    }, 12 * 60_000);
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
      const lines = stdout.split("\n");
      stdout = lines.pop() || "";
      for (const line of lines) {
        updateProgressFromLine(job, line);
        if (line.startsWith("ERROR ")) stderr += line + "\n";
      }
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
      if (stderr.length > 20_000) stderr = stderr.slice(-20_000);
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(new Error(`Could not start the renderer: ${errorMessage(error)}`));
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        const clean = stderr.split("\n").filter((l) => l.startsWith("ERROR ") || /error/i.test(l)).slice(-4).join(" ");
        return reject(new Error(clean.replace(/^ERROR\s+/gm, "") || `Renderer exited with code ${code}`));
      }
      resolve();
    });
  });
  const required = ["arrangement.json", "song.wav", "song.mp3", "instrumental.wav", "lead.wav"];
  for (const file of required) {
    if (!fs.existsSync(path.join(job.dir, file))) throw new Error(`Renderer finished without ${file}`);
  }
  const reportPath = path.join(job.dir, "work", "render_report.json");
  let report = {};
  try { report = JSON.parse(await fsp.readFile(reportPath, "utf8")); } catch {}
  const arr = job.arrangement;
  job.status = "done";
  job.stage = "Master ready";
  job.message = "Your song is ready";
  job.progress = 100;
  job.result = {
    title: arr.title,
    slug: job.slug,
    bpm: arr.bpm,
    key: arr.key,
    duration: report?.files?.["song.wav"]?.duration || Number(arr.sections.reduce((a, s) => a + s.bars, 0)) * 4 * 60 / Number(arr.bpm),
    vocalNotes: Number(report?.vocal_notes || arr.vocal?.lead?.length || 0),
    harmonyNotes: Number(report?.harmony_notes || arr.vocal?.harmony?.length || 0),
    peakDb: report?.files?.["song.wav"]?.peak_db ?? null,
    mix: arr.mix,
    files: {
      mp3: `/output/${encodeURIComponent(job.slug)}/song.mp3`,
      wav: `/output/${encodeURIComponent(job.slug)}/song.wav`,
      instrumental: `/output/${encodeURIComponent(job.slug)}/instrumental.wav`,
      arrangement: `/output/${encodeURIComponent(job.slug)}/arrangement.json`,
      harmony: fs.existsSync(path.join(job.dir, "harmony.wav")) ? `/output/${encodeURIComponent(job.slug)}/harmony.wav` : null,
    },
  };
}

async function startJob(params) {
  const job = {
    id: crypto.randomBytes(8).toString("hex"),
    status: "running",
    stage: "Preparing",
    message: "Opening the lab",
    progress: 2,
    createdAt: Date.now(),
    result: null,
    error: null,
    slug: null,
    dir: null,
    arrangement: null,
  };
  jobs.set(job.id, job);
  // keep the key only in this closure; never store it on the job or write it down
  const api = {
    baseURL: params.baseURL,
    apiKey: params.apiKey,
    model: params.model,
  };
  (async () => {
    try {
      const made = await generateArrangement({
        lyrics: params.lyrics,
        style: params.style,
        api,
        voice: params.voice,
        mix: params.mix,
        job,
      });
      job.arrangement = made.arr;
      job.dir = made.dir;
      job.slug = made.slug;
      await runRender(job);
    } catch (error) {
      job.status = "error";
      job.error = errorMessage(error);
      job.message = "The session stopped";
      job.stage = "Error";
      if (job.dir && !job.result) {
        // keep the directory and its arrangement + any partial stems for debugging
      }
    } finally {
      api.apiKey = "";
    }
  })();
  return job;
}

function publicJob(job) {
  return {
    id: job.id,
    status: job.status,
    stage: job.stage,
    message: job.message,
    progress: job.progress,
    error: job.error,
    result: job.result,
  };
}

async function serveFile(req, res, filePath, download) {
  let stat;
  try { stat = await fsp.stat(filePath); } catch { return json(res, 404, { error: "File not found" }); }
  if (!stat.isFile()) return json(res, 404, { error: "File not found" });
  const ext = path.extname(filePath).toLowerCase();
  const headers = {
    "Content-Type": MIME[ext] || "application/octet-stream",
    "Accept-Ranges": "bytes",
    "Cache-Control": "no-cache",
  };
  if (download) headers["Content-Disposition"] = `attachment; filename="${path.basename(filePath)}"`;
  const range = req.headers.range;
  if (range && /^bytes=\d*-\d*$/.test(range) && stat.size) {
    const [startRaw, endRaw] = range.replace("bytes=", "").split("-");
    const start = startRaw ? Number(startRaw) : 0;
    const end = endRaw ? Math.min(Number(endRaw), stat.size - 1) : stat.size - 1;
    if (start > end || start >= stat.size) {
      res.writeHead(416, { "Content-Range": `bytes */${stat.size}` });
      return res.end();
    }
    headers["Content-Range"] = `bytes ${start}-${end}/${stat.size}`;
    headers["Content-Length"] = end - start + 1;
    res.writeHead(206, headers);
    fs.createReadStream(filePath, { start, end }).pipe(res);
    return;
  }
  headers["Content-Length"] = stat.size;
  res.writeHead(200, headers);
  fs.createReadStream(filePath).pipe(res);
}

const server = http.createServer(async (req, res) => {
  try {
    if (!localOnly(req)) return json(res, 403, { error: "SongForge only accepts requests from this machine (127.0.0.1)." });
    const url = new URL(req.url, `http://${req.headers.host}`);
    if (req.method === "GET" && (url.pathname === "/" || url.pathname === "/index.html")) {
      return serveFile(req, res, path.join(ROOT, "index.html"), false);
    }
    if (req.method === "GET" && url.pathname === "/api/config") {
      return json(res, 200, { defaultBaseURL: DEFAULT_BASE, defaultModel: DEFAULT_MODEL, python: PYTHON, chimera: CHIMERA });
    }
    if (req.method === "POST" && url.pathname === "/api/test") {
      const body = await readBody(req);
      const started = Date.now();
      try {
        const result = await callChat({
          baseURL: body.baseURL,
          apiKey: body.apiKey,
          model: body.model,
          messages: [{ role: "user", content: "Reply with exactly the word: OK" }],
          maxTokens: 20,
          temperature: 0,
        });
        return json(res, 200, { ok: true, latencyMs: Date.now() - started, model: result.model, reply: result.content.trim().slice(0, 80) });
      } catch (error) {
        return json(res, 502, { ok: false, error: errorMessage(error) });
      }
    }
    if (req.method === "POST" && url.pathname === "/api/generate") {
      const body = await readBody(req);
      let lines;
      try { lines = parseLyrics(body.lyrics); } catch (error) { return json(res, 400, { error: errorMessage(error) }); }
      if (!String(body.style || "").trim()) return json(res, 400, { error: "Describe the musical feeling you want" });
      if (String(body.style).length > 1500) return json(res, 400, { error: "Keep the musical description under 1500 characters" });
      if (!body.apiKey) return json(res, 400, { error: "Add an API key in API Settings" });
      try { cleanBase(body.baseURL); } catch (error) { return json(res, 400, { error: errorMessage(error) }); }
      if (!String(body.model || "").trim()) return json(res, 400, { error: "Add a model name in API Settings" });
      const voice = body.voice === "male" ? "male" : "female";
      const job = await startJob({
        lyrics: lines.join("\n"),
        style: String(body.style).trim(),
        baseURL: body.baseURL,
        apiKey: String(body.apiKey),
        model: String(body.model).trim(),
        voice,
        mix: body.mix,
      });
      return json(res, 202, { jobId: job.id });
    }
    const jobMatch = url.pathname.match(/^\/api\/jobs\/([a-f0-9]{16})$/);
    if (req.method === "GET" && jobMatch) {
      const job = jobs.get(jobMatch[1]);
      if (!job) return json(res, 404, { error: "Job not found (the server may have restarted)" });
      return json(res, 200, publicJob(job));
    }
    const outMatch = url.pathname.match(/^\/output\/([a-z0-9-]+)\/([a-zA-Z0-9_.-]+)$/);
    if (req.method === "GET" && outMatch) {
      const [, slug, file] = outMatch;
      if (!AUDIO_FILES.has(file) || !/^[a-z0-9-]+$/.test(slug)) return json(res, 404, { error: "File not found" });
      return serveFile(req, res, path.join(OUTPUT, slug, file), url.searchParams.get("download") === "1");
    }
    return json(res, 404, { error: "Not found" });
  } catch (error) {
    return json(res, 500, { error: errorMessage(error) });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`SongForge listening at http://${HOST}:${PORT}`);
  console.log(`Python renderer: ${PYTHON}`);
  console.log(`CHIMERA: ${CHIMERA}`);
  console.log("Local use only. Do not expose this port to the internet.");
});

// expire old in-memory job records; rendered files stay on disk
setInterval(() => {
  const cutoff = Date.now() - 6 * 60 * 60 * 1000;
  for (const [id, job] of jobs) if (job.createdAt < cutoff) jobs.delete(id);
}, 30 * 60 * 1000).unref();
