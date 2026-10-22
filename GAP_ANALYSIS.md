# Gap analysis: TrueFalse repo vs. `TrueFalse_Proposal.pdf`

Date: 2026-10-22 · Branch: `proposal-upgrade` · Baseline: `main` @ `9757634` (Phase 7)

Baseline health: **196 tests pass (2 skipped), ruff clean, mypy strict clean.**

Note: the proposal is not in the repo root. I read `~/Downloads/TrueFalse_Proposal.pdf`.
There is no `.tex` on this machine.

The repo is well ahead of a typical starting point. Phases 1–7 already cover most of the
proposal's input side. The main gaps are **claim structure, truly independent judges (different
models), news search, AI-content detection, a text verdict cache, and the evaluation harness**.

---

## 1. Feature status

Legend: ✅ done · 🟡 partly done · ❌ missing

### Text pipeline

| Proposal feature | Status | Where / what is missing |
|---|---|---|
| Claim splitter with structured JSON output | 🟡 | `claims/extract.py` uses `beta.messages.parse` into Pydantic `Extraction`. Claims are atomic but have no **{who, did, where, when}** fields: only `original`, `english`, `topic`, `potentially_harmful`. |
| Google Fact Check Tools API | ✅ | `research/sources/factcheck_api.py`, with `languageCode`. |
| "Global IFCN network" | 🟡 | No IFCN API exists. Google Fact Check already aggregates ClaimReview from most IFCN signatories, and the optional pgvector index ingests the Data Commons ClaimReview feed. Missing: an IFCN-signatory domain list used for tiering and site-restricted search. |
| News search | ❌ | Only Brave **web** search. No news endpoint and no date-aware news source. |
| Cross-language evidence search | 🟡 | The research agent is told to query in English and in the original language. Nothing makes sure that happens, and there is no search in the languages of the place the claim is about. |
| 2–3 LLM judges, independent | 🟡 | `verify/judges.py` runs 3 judges, but they are **the same model** (`ANTHROPIC_MODEL`) with 3 prompts. Same-model judges make correlated mistakes, so this falls short of the proposal's "several strong LLMs". |
| Verdicts True / False / Misleading / Unverified + confidence + citations | ✅ | Core rule enforced in code, calibrated confidence. |
| No evidence → Unverified | ✅ | `judge()` short-circuits; `enforce_core_rule`. |
| Disagreement flagged, not guessed | 🟡 | Disagreement becomes Unverified with a warning. There is no explicit **`needs_human_review`** flag in `CheckReport` and no review queue or export. |

### Images and audio

| Feature | Status | Notes |
|---|---|---|
| OCR for screenshots/memes | ✅ | Claude vision (`modality/image.py`, `image_read.v1`): verbatim text in the original script. |
| Speech-to-text (Whisper) for voice notes | ✅ | `FasterWhisperSTT`, chunking, LLM filter for chunks that contain claims. Not yet run on real audio (PROGRESS.md). |

### Video and AI-content detection

| Feature | Status | Notes |
|---|---|---|
| Video transcript | ✅ | ffmpeg audio → Whisper. |
| Sampled key frames | 🟡 | 4 evenly spaced frames, no scene-change sampling. Short clips with fast cuts get thin coverage. |
| On-screen OCR | 🟡 | Frames go through one vision call that returns a single merged `text_in_image`. No per-frame text with timestamps. |
| C2PA / content credentials | ❌ | |
| Watermark check | ❌ | See the practicality notes in §4. |
| Deepfake classifier on frames | ❌ | Only the vision LLM's `possibly_ai_generated` boolean, which is eyeballing and not a classifier. |
| Synthetic-voice check | ❌ | |
| Reverse image search (recycled photos) | 🟡 | Only a local pHash "seen before" store for images sent to us. No web reverse search. |
| AI-text detection as a weak signal only | ❌ | |
| `ai_detect` module | ❌ | Does not exist. |

### Platforms and languages

| Feature | Status | Notes |
|---|---|---|
| Telegram bot | ✅ | Webhook + dev poller. |
| WhatsApp bot | ✅ | Cloud API, signature check. |
| Links: Instagram, X, YouTube, TikTok, Facebook | 🟡 | All are in yt-dlp's allow-list, and X text goes through oEmbed. **Instagram and Facebook often need a login for yt-dlp.** By policy we never pass cookies, so many of those links will fail. There is no metadata/oEmbed fallback. |
| Web app (upload or paste link) | ✅ | Next.js PWA + `/api/check`, share target. |
| Auto-detect language, reply in it, mixed/transliterated | ✅ | LLM detection + script heuristics + optional fastText hint. |
| No fixed language list | 🟡 | Core flow works for any language, but several fixed lists remain (§2). |
| Reply card: verdict, 2-line reason, 2–3 sources, shareable correction | 🟡 | Explanation is 2–3 sentences, up to **4** sources, and the share card is a **PNG**. There is no short text correction message ready to forward. |
| Cache verdicts for repeated forwards (claim text + verdict only) | ❌ | Only image pHash. Every text forward is re-checked in full. |

### Evaluation

| Feature | Status | Notes |
|---|---|---|
| Eval script: full pipeline vs single LLM judge vs fact-check lookup | ❌ | `backend/eval/` holds only two small hand-made datasets (38 + 30). There is no harness. |
| X-Fact, MultiClaim loaders | ❌ | |
| Folder for own labeled forwards | ❌ | |
| Accuracy, macro-F1, false-alarm rate | ❌ | (`language/accuracy.py` reads a JSON that nothing writes yet.) |
| AI-content detection scored separately | ❌ | |

### Proposal's module layout vs. repo

The proposal asks for `ingest, claims, retrieval, verdict, ai_detect, delivery, eval`. The repo has
`ingestion + modality, claims, matching + research, verify, respond, eval`. **I will not rename
existing packages.** That would touch every import and test for no behavior change. I will add the
new `ai_detect` package and document the mapping in the README. If you want the exact names, I can
do a pure rename as a separate commit at the end.

---

## 2. Things hardcoded to particular languages or regions

| Location | Issue | Proposed fix |
|---|---|---|
| `verify/ratings.py` | Keyword table for ~15 languages. Unknown languages give `None`, so fact-check consensus is silently ignored. **Also buggy, see §3.1.** | Keep the fast table for common ratings; add an LLM fallback (structured JSON, cached) that maps any-language ratings. |
| `research/tiers.py` | Tier-1/2 domain lists lean toward US/UK/EU/India (`mohfw.gov.in`, `eci.gov.in`, `boomlive.in`, …). Most countries' health ministries, election bodies and fact-checkers fall to tier 3 unless they use a listed gov suffix. | Make the lists data files (`tiers.yaml`), add the full IFCN signatory list, and generalise official suffixes (`.gov.*`, `.gob.*`, `.go.*`, `.gouv.*`, `.govt.*`, `.gv.*`). |
| `ingestion/common.py` `CHECKING` / `WORKING_ON_MEDIA` | 16 / 10 languages, and everything else gets English. `FAILURE` and `TEXT_ONLY` are **English only**. | Keep the tables as a fast path; otherwise use the existing cached `Translator`, keyed by the platform language hint. |
| `language/accuracy.py` `WELL_SUPPORTED` | Fixed list of 21 languages decides the "less tested" warning until eval data exists. | Fine as a default. Eval will now write `language_accuracy.json`, so this becomes measured. |
| `language/names.py` | ~60 language names for the "switch language" command. | Add an LLM fallback when a short message looks like a language name but isn't in the table. |
| `respond/tts.py` (gTTS) | Voice replies only in gTTS languages. Already falls back to text silently. | Leave it. Document it. |
| `safety/topic_rules.py` | English canonical text, translated at compose time. | OK. |

---

## 3. Bugs, dead code, weak spots

### 3.1 Bug: negated fact-check ratings are read as TRUE (confirmed)

`normalize_rating` matches substrings, and the TRUE needles (`wahr`, `vero`, `cierto`, `doğru`, `सही`) also appear inside negations:

```
Unwahr        -> true      (German "untrue")
Non vero      -> true      (Italian "not true")
No es cierto  -> true      (Spanish "not true")
doğru değil   -> true      (Turkish "not true")
सही नहीं       -> true      (Hindi "not correct")
```

This feeds `factcheck_consensus`. A real fact-check saying "false" can be counted as **supporting** a TRUE verdict, or can veto a correct FALSE verdict. I'll fix it in milestone 1 with negation handling, a regression test, and the LLM fallback above.

### 3.2 Other issues

1. **Judges are not independent models**, so "disagreement" undercounts real uncertainty (§1).
2. **Dead code:** `core/models.ProcessedContent` (with `media_flags`) is never used; `modality/process.ProcessedContent` replaced it. `ClaudeImageReader` and the `ImageReader`/`OCRResult` interface are not wired anywhere.
3. **The LLM's `possibly_ai_generated` reaches users as a warning** ("may have been made with AI"). It comes from one vision guess with no classifier behind it, so false alarms are likely. It should become one weak input to `ai_detect`, not a warning by itself.
4. **No text cache.** The same viral forward costs a full research + 3-judge run every time.
5. **Instagram/Facebook link fetching** will often fail without login (above).
6. **Video sampling:** 4 frames in one call; on-screen text is merged across frames.
7. **No `needs_human_review` signal** for the bot, web or eval to act on.
8. **Docs are stale:** the README says "Phases 1–2 are done". PROGRESS.md's "Deferred" lists Phase 4/6 items that are already done.
9. **Never run against live APIs** (PROGRESS.md). All tests use fakes. The first live run will likely surface integration issues, especially yt-dlp and WhatsApp media.
10. `uv sync` with the current uv rewrites `backend/uv.lock` (format drift). Harmless; I reverted it.
11. SSRF DNS-rebinding window (already noted in PROGRESS.md). Out of scope here.

---

## 4. Practicality notes (where the proposal needs a substitute)

| Proposal item | Problem | Closest practical alternative |
|---|---|---|
| "Several strong LLMs" as judges | Needs keys for other providers (cost + accounts). | A provider-neutral `LLMClient` with **Anthropic, OpenAI and Google Gemini** adapters, each enabled by its key. With only an Anthropic key, the panel uses **different Claude models** (e.g. Opus 5.5 + Sonnet 5.5 + Haiku 5.5): better than one model with 3 prompts, but still one vendor. The report records which models voted. |
| "Global IFCN network" | No public IFCN search API. | Google Fact Check (aggregates IFCN ClaimReview) + Data Commons feed in the index + IFCN signatory list for tiering/site search. |
| News search | | **Brave News Search** (same key as web) + **GDELT DOC 2.0** (free, no key, 65+ languages, date-filterable). |
| Watermark detection | Google's SynthID detector has no general public API. Other vendors' invisible watermarks can't be read by third parties. | **C2PA manifest check** (`c2pa-python`) + **IPTC/XMP `DigitalSourceType = trainedAlgorithmicMedia`** metadata. Report "no credentials found" as *no information*, never as "real". |
| Deepfake classifier | Free open models (FaceForensics++-style) generalise poorly to new generators. Good commercial APIs (Hive, Sightengine, Reality Defender) cost money. | A pluggable `FrameClassifier`: optional open Hugging Face model (`ai_detect` extra) and an optional **Sightengine** or **Hive** adapter by key. Output is a score with a "weak signal" label, never a verdict. |
| Synthetic-voice detection | Same problem; open anti-spoofing models (AASIST-type) are weak on modern TTS. | Same pluggable pattern, optional open model, scored separately in eval, shown only above a high threshold. |
| Reverse image search | Google Lens has no API; Bing Visual Search was retired. TinEye is paid. | **Google Cloud Vision Web Detection** (pages with matching images, earliest dates; ~$3.50 / 1000 images, uses a Google Cloud key). Optional SerpAPI adapter. |
| AI-text detection | Detectors are unreliable, especially non-English. | One cheap LLM signal, labeled "weak" in output, **not shown to users** by default. Logged for eval only. |
| Instagram / Facebook links | Login walls. | Try yt-dlp; on failure, use public `og:` metadata (title/description/thumbnail) and run the thumbnail through image analysis; tell the user if we could only read the preview. Meta's official oEmbed needs an app token: optional by key. |
| X-Fact dataset | Public (GitHub). 7 labels. | Map: true/mostly true → True; partly true/mostly false → Misleading; false → False; unverifiable/other → Unverified. |
| MultiClaim dataset | **Access is gated** (Zenodo request + agreement). It's a *claim-retrieval* set (post ↔ fact-check), not verdict-labeled. | Loader expects you to download it. Two tasks: retrieval recall@k for the lookup baseline, and verdict accuracy using the linked fact-check's rating (normalized) as the label. |
| Running full eval | Full pipeline ≈ research + 3 judges per claim. Thousands of items at Opus prices is expensive. | Stratified sample by language (default 300, `--limit`), resumable JSONL results, cost printed before running, and a `--dry-run` with fakes. |

---

## 5. Plan (waiting for your OK before code changes)

Each milestone ends with tests, ruff and mypy passing and one or more small commits on `proposal-upgrade`.

**M1 — Text pipeline**
1. Fix the ratings negation bug (+ tests) and add an LLM fallback for unknown ratings.
2. Claim schema: add `who`, `did`, `where`, `when` (nullable) to `Claim`; update the `claim_extract` prompt to v2; validate. Research and judges use these fields for better queries (place → also search in that place's languages).
3. Multi-provider LLM: `OpenAILLM` and `GeminiLLM` adapters behind the existing `LLMClient` protocol (structured JSON validated with Pydantic, one retry on validation failure). The judge panel becomes a list of `(provider, model, prompt)` from config; it defaults to different Claude models if only an Anthropic key is set.
4. Retrieval: add Brave News + GDELT sources; make sure every claim gets at least one original-language and one English fact-check query (code-guaranteed, not just prompted); tier lists → data file + IFCN list + generic official suffixes.
5. Verdict: add `needs_human_review: bool` + reason to `ClaimDecision`/`CheckReport` (set on judge disagreement, conflicting fact-checks, or fewer than 2 valid judges).

**M2 — Images + audio**
6. Already working. Wire up or delete the unused `ImageReader`/`ClaudeImageReader` and the dead `core.models.ProcessedContent`; add a local Tesseract OCR fallback only if you want offline OCR (I'd skip it).

**M3 — Video + AI detection**
7. Video: scene-change + even sampling (cap 8), per-frame OCR with timestamps.
8. New `app/ai_detect/` package: `c2pa.py`, `metadata.py` (IPTC/XMP), `frames.py` (pluggable classifier), `voice.py`, `reverse_image.py` (Cloud Vision web detection), `text.py` (weak). One `AIReport` with per-signal scores. User-facing wording only for strong signals (C2PA says AI, reverse search finds older copies). Replace the bare LLM `possibly_ai_generated` warning.

**M4 — Platforms + languages**
9. Instagram/Facebook/TikTok og-metadata fallback + thumbnail analysis; tests per platform URL shape.
10. Verdict cache: Redis key = hash of normalized claim (English) + language-independent embedding bucket when the index is on; stores only claim text + verdict + source URLs + timestamp, TTL 7 days, never user ids or message text.
11. Reply card: 2-line reason, max 3 sources, and a short **forwardable correction message** in the user's language (text, alongside the PNG).
12. Remove fixed-language fallbacks: translate status/failure strings via the cached translator.

**M5 — Evaluation**
13. `backend/eval/run.py` with three systems: `full`, `single_judge` (one LLM judging the claim alone, as in the proposal's baseline), `lookup_only` (Fact Check API + index, rating → verdict). Loaders: X-Fact, MultiClaim, `eval/datasets/own_forwards/*.jsonl` (+ template/README). Metrics: accuracy, macro-F1, false-alarm rate (True claims labeled False/Misleading), Unverified rate, per-language macro-F1 (writes `language_accuracy.json`). Separate `ai_detect` scorer over a labeled media folder. Offline smoke test with fakes.

**Finish**
14. README rewrite (what it does, setup, keys, bots, web, eval, module map), `.env.example` for every new key, PROGRESS.md update.

### Decisions I need from you
- **Judge models:** do you have (or want to get) **OpenAI** and/or **Gemini** keys, or should the panel default to Opus + Sonnet + Haiku?
- **Paid detectors:** OK to add optional adapters for Google Cloud Vision (reverse image) and Sightengine/Hive (deepfake), used only if a key is set?
- **Module renames:** keep current package names (recommended) or rename to the proposal's names at the end?
