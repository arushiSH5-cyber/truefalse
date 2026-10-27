# Progress

Repo: https://github.com/arushiSH5-cyber/truefalse

## Done

### Phase 1: Skeleton (2026-10-10)
- Repo layout for every pipeline stage, Docker Compose (api, worker, postgres+pgvector, redis), CI (ruff, mypy strict, pytest on 3.12 with uv)
- `/health` and `/health/ready` endpoints, structured JSON logs with trace IDs, env-based settings

### Phase 2: Text MVP on Telegram (2026-10-10)
- **Models** (`core/models.py`): Submission, Claim, Extraction, Evidence, ClaimJudgement, ComposedReply, CheckReport
- **LLM** (`core/llm.py`): an `LLMClient` interface. The Anthropic implementation uses structured outputs (`beta.messages.parse`), a manual tool loop with a tool-call cap, a time budget and an iteration hard stop, plus server-side refusal fallbacks and token accounting. Model: `ANTHROPIC_MODEL` (default `claude-opus-5-5`).
- **Language** (any language): the claim-extraction step returns a BCP-47 code, the script, and a romanized/code-mixed flag. Research uses an English pivot, and the reply comes back in the user's language and script.
- **Claim extraction** (`claims/extract.py`): atomic claims with original text + English, topic tags (health/superstition/election/emergency/science/other), and a harm flag
- **Research agent** (`research/agent.py`): four tools (Google Fact Check, Semantic Scholar, PubMed, Brave web search). Evidence gets stable IDs (E1…), is deduplicated, and is tiered by domain (`research/tiers.py`). Queries are PII-redacted before they leave the system.
- **Verify** (`verify/judges.py`): one judge with a citation requirement. The core rule is enforced in code: no valid citation means Unverified, and tier-3-only evidence can never give High confidence. The overall verdict is the most severe across claims.
- **Safety** (`safety/`): PII redaction; health notes ("not a doctor", plus a danger warning for harmful false claims); election and emergency notes; warnings for satire, mixed language and old evidence
- **Respond** (`respond/compose.py`): the LLM writes only the translatable text, and code inserts the emoji, titles and URLs, so links cannot be invented. If composing fails, it falls back to English.
- **Telegram**: webhook `/webhooks/telegram` checks the secret token. It sends an instant localized "Checking…" acknowledgment and queues an arq job; the worker replies and logs anonymized metrics (per-stage latency, tokens, cost). There's also a `scripts/telegram_poll.py` dev runner that needs no Redis or webhook.
- **Privacy**: user IDs are HMAC-hashed with a salt, and message text is never logged
- **Prompts**: `app/prompts/*.v1.md` (extract, research, judge, compose)
- **Extensibility**: `modality/base.py` interfaces (SpeechToText, ImageReader, TextToSpeech) and CONTRIBUTING.md for collaborators
- **Tests**: 48 offline tests (pipeline flows, core rule, tool budget, LLM tool loop with a stubbed SDK, source parsers, HTTP retries, Telegram parsing and webhook, prompts, safety)

### Phase 3: Fact-check index (2026-10-10)
- **Schema**: `factchecks` table (Alembic `0001`) with a pgvector `vector(768)` column + HNSW cosine index, and a generated `tsvector` ('simple' config, works for every language) + GIN index
- **Parsing** (`matching/claimreview.py`): schema.org ClaimReview from DataFeed/JSON-LD files (Data Commons format) and Fact Check API results; dedupes by review URL
- **Embeddings** (`matching/embeddings.py`): `Embedder` interface; `intfloat/multilingual-e5-base` via sentence-transformers (optional `ml` extra, lazy-loaded, runs off the event loop)
- **Hybrid search** (`matching/factcheck_index.py`): dense (max cosine over original + English phrasings) + sparse (BM25 / `ts_rank_cd`), fused with Reciprocal Rank Fusion. Postgres and in-memory implementations share the same logic.
- **Tokenizer** (`matching/bm25.py`): Unicode-category based, so it handles combining marks in Devanagari/Tamil/Bengali/Arabic, with character bigrams for Chinese/Japanese/Thai/Khmer/Myanmar
- **Pipeline**: a new `match` stage runs before research. Strong matches (cosine ≥ `MATCH_STRONG_THRESHOLD`) seed the evidence store, and when every claim matches, the research budget drops to 2 confirmation searches.
- **Ingestion**: `scripts/ingest_factchecks.py --file <DataFeed json or URL>` / `--api queries.txt --languages en,es,hi`
- **Tests**: cross-language matching (Spanish, Hindi, Chinese, Swahili code-mix → English fact-checks) with a deterministic concept embedder; a Postgres integration test that runs in CI against pgvector; an optional `ml`-marked test with the real E5 model

### Phase 4: Trust layer (2026-10-10)
- **Judge panel** (`verify/judges.py`): 3 independent judges (balanced `judge.v1`, skeptic `judge_skeptic.v1`, context `judge_context.v1`) run concurrently. They differ by prompt and effort level, because Opus 5.5 rejects sampling temperature. The panel survives one judge failing; fewer than 2 valid judges means Unverified.
- **Decision rules**: the core rule is applied to every vote and again to the final decision. TRUE vs FALSE/MISLEADING disagreement means Unverified ("sources disagree"); a FALSE+MISLEADING split resolves to the milder MISLEADING; a cited fact-check that contradicts the verdict means Unverified.
- **Calibration** (`verify/calibration.py`): a logistic model over agreement, best source tier, source count, fact-check consensus and judge confidence. Probability < 0.5 means Unverified; ≥ 0.65 is Medium, ≥ 0.85 is High. `Calibrator.fit` and `save` are ready for Phase 8; `expected_calibration_error` is included.
- **Rating normalization** (`verify/ratings.py`): fact-checker ratings in ~15 languages (False/Falso/Faux/झूठ/كاذب/虚假/Hoaks…) map to verdicts, and conflicting ratings are detected
- **Grounding check** (`safety/grounding_check.py`): a deterministic invented-number filter plus an LLM sentence-by-sentence support check (by index). Unsupported sentences are dropped; if nothing survives or the check fails, a template built only from evidence metadata is used. Multi-script sentence splitting covers `。 । ؟`.
- **Warnings**: "sources disagree" is added. Metrics now log agreement, probability and grounded ratio per claim.
- **Tests**: 97 passing (ratings in many languages, calibration fit/ECE, panel voting edge cases, judge failure, grounding drop/fallback)

### Phase 5: Multimodal (2026-10-10)
- **Images/screenshots** (`modality/image.py`, `media.py`): Claude vision OCR + neutral description + concrete editing/AI-generation cues (`image_read.v1`). Images are EXIF-rotated, downscaled to ≤1568 px and recompressed before upload.
- **Seen before** (`modality/hashes.py`): a 64-bit perceptual hash (pHash, Hamming ≤ 8) records how often an image was checked and its last verdict. Only the hash is stored, never the image (Postgres table `image_hashes`, migration `0002`).
- **Voice notes/audio** (`modality/audio.py`): `FasterWhisperSTT` (multilingual, detects language). Long audio is split into ~60 s chunks, and a cheap LLM pass (`segment_filter.v1`) keeps only claim-bearing chunks (≤ 8), with timestamps.
- **Video** (`modality/video.py`): ffmpeg subprocesses with hard timeouts extract 16 kHz audio and 4 evenly spaced keyframes, which are analyzed in one vision call.
- **Links** (`modality/links.py`): reels, shorts, TikToks, podcasts etc. via yt-dlp (allow-listed public media hosts, no cookies or logins, duration and size caps; short clips keep video for keyframes); X posts via the public oEmbed endpoint; articles via trafilatura or a built-in HTML extractor
- **SSRF protection**: http(s) only, ports 80/443, no credentials in URLs, every redirect hop re-resolved, and private/loopback/link-local/metadata addresses refused; HTML capped at 2 MB
- **Privacy**: every request uses a temp dir that is always deleted, including on errors (tested)
- **Pipeline**: a new `modality` stage; media warnings (possibly edited, possibly AI-generated, auto-transcribed, part failed to open, seen before with its earlier verdict); progress message "Looking at your media…" localized; Telegram photos/voice/audio/video/video notes/image-video-audio documents
- **Tests**: 120 passing, including real ffmpeg on a generated video, a pHash robustness test, SSRF rejection cases, a redirect-to-metadata attack, article extraction, oEmbed, and long-audio chunk selection

### Phase 6: Every language (2026-10-10)
- **Script detection** (`language/scripts.py`): Unicode-based detection of 40+ writing systems (counting combining marks, so Indic text isn't undercounted), mixed-script detection, and romanization detection (e.g. Hindi or Tamil in Latin letters). It backs up the LLM's romanized/code-mixed flag.
- **fastText LID** (`language/detect.py`, optional `lid` extra): NLLB/lid.176 labels mapped to ISO codes and passed to the extractor as a hint only
- **Language switch** (`language/names.py`): ~60 languages recognized by English name, autonym, or names in other major languages ("Español", "inglés", "हिंदी", "العربية", "中文", "Kiswahili"). `/language` resets to auto-detect, and the acknowledgment uses the chosen language.
- **Preferences** (`core/prefs.py`): Redis, keyed only by the user hash (reply language, voice replies, welcomed, digest opt-in)
- **First-time welcome**, translated into the user's language (`respond/translate.py`, cached), before their first answer
- **Voice out** (`respond/tts.py`): gTTS → OGG/Opus via ffmpeg. Voice in gets voice out; `/voice` turns it on for every answer; unsupported languages fall back to text silently.
- **Per-language accuracy** (`language/accuracy.py`): measured macro-F1 per language (written by the eval harness), with a conservative default list. Lower-accuracy languages get a user-facing warning.
- **Test sets** (`eval/datasets/`): `health_cures.jsonl` (38 items: fake cures, unsafe treatments, superstitions, true controls; 20+ flagged harmful) and `multilingual_claims.jsonl` (30 items, 15 languages, 10 scripts, romanized and code-mixed)
- **Tests**: 182 passing. CI now installs ffmpeg so the video tests run there too.

### Phase 7: WhatsApp + PWA (2026-10-10)
- **WhatsApp Cloud API** (`ingestion/whatsapp.py`, `api/whatsapp.py`): GET verify-token handshake; HMAC-SHA256 signature check on the raw body; batched webhooks; text, image/sticker with caption, voice/audio, video, image-video-audio documents and button replies; status updates ignored. The client handles text replies (threaded with `context`, split at 4096 chars), media download (streamed, 25 MB cap) and voice replies (upload, then send audio). Graph API version is configurable (default v26.0, per Meta's changelog).
- **Multi-platform worker**: a `RoutingMessenger` and per-platform media fetchers (Telegram, WhatsApp, web), each enabled only when configured
- **Web API** (`api/web.py`): `POST /api/check` (text/link/file upload, anonymous `X-Client-Id`, `Accept-Language` hint, 20 MB cap, type check, Redis rate limit per client), `GET /api/check/{id}` (status, progress, structured result), `GET /api/check/{id}/card.png`; CORS limited to `WEB_ORIGINS`
- **Job store** (`core/jobs.py`): Redis with TTLs; uploads are deleted as soon as the worker reads them; results expire after 24 h; public results carry no user data
- **Share card** (`respond/share_card.py`): a polite 1080×1080 PNG (verdict band, claim, explanation, top source), with Noto fonts per script (CJK, Arabic, Indic…) and RTL support via libraqm in Docker
- **PWA** (`web/`, Next.js 14 + Tailwind): check form (text, link, photo, voice, video), result page that polls with progress, verdict colors, warnings, sources behind "More", browser text-to-speech Listen button, share-card button (Web Share API with download fallback), installable manifest with a **Web Share Target** (`/share` forwards multipart to the API), and a minimal offline-shell service worker; ~90 KB first-load JS
- **CI**: a new `web` job (npm ci, tsc, lint, build)
- **Tests**: 196 backend tests passing (WhatsApp parsing/signature/handshake/client, web API validation and rate limit, upload deletion, web job end-to-end, share cards in Latin/Devanagari/Arabic/Han)

### Proposal upgrade, M1–M5 (2026-10-22 to 2026-10-23)
Brought the repo in line with the project proposal on the `proposal-upgrade` branch. Details,
limits and file locations per item are in [GAP_ANALYSIS.md](GAP_ANALYSIS.md) §6.
- **M1 Text:** claims with who/did/where/when; judges on different models (Opus + Sonnet + Haiku
  by default, optional OpenAI/Gemini); Brave News + GDELT news search; fact-check lookups in
  English and the message language guaranteed in code; tier lists as data; `needs_human_review`;
  fixed a bug where negated ratings ("Unwahr", "No es cierto") were read as True.
- **M2 Images + audio:** Whisper no longer forced into the phone's language, code-switched voice
  notes, readable tall screenshots, PyAV pin after a breaking release, dead code removed.
- **M3 Video + AI content:** scene-cut key frames with per-frame on-screen text; new `ai_detect`
  package (C2PA, metadata, optional classifiers, reverse image search, weak text signal).
- **M4 Platforms + languages:** public-preview fallback for Instagram/Facebook/TikTok links,
  status messages in any language, two-line reason + max 3 sources + forwardable correction,
  7-day verdict cache for repeated forwards.
- **M5 Evaluation (was Phase 8):** `backend/eval/run.py` compares full pipeline, single judge and
  fact-check lookup on X-FACT, MultiClaim and own forwards; AI detection scored separately.

### Finish (2026-10-27)
- README rewritten: current status, pipeline, proposal-module map, API keys, AI detection, eval
- Docker: the backend image used uv 0.5, which can't read the current `uv.lock`; now uv 0.13 with
  `--frozen`. The `web` service no longer overwrites the host's `web/node_modules` and `.next`.
- Worker: if startup fails (e.g. `ANTHROPIC_API_KEY` missing), shutdown no longer hides the real
  error behind a `KeyError`.
- **Verified locally with `docker compose up --build`** (no API keys): health and readiness
  (Postgres + Redis), migrations, web form → `/api/check` → result page polling, the share
  target redirect, upload type checks, the result page with a sample result (verdict, review
  flag, correction, sources, AI checks) and the share-card PNG rendered in Docker.
- 343 backend tests pass (2 skipped), ruff and strict mypy clean, web type-check/lint/build clean.

## Later (not in the proposal)
- Chrome extension (`extension/` is an empty placeholder)
- Opt-in weekly digest (the preference fields already exist)
- Thumbs up/down, "Report a mistake", and a human review queue (the `needs_human_review`
  flag is set and logged; nothing collects the flagged answers yet)
- Web app labels ("Check it", "Checking…") are English; the answers themselves are translated

## Blockers / notes
- **Not yet run against real APIs.** Every external call is tested only against fakes and saved
  sample responses. The first live run needs `ANTHROPIC_API_KEY` (and ideally
  `GOOGLE_FACTCHECK_API_KEY`, a Brave `SEARCH_API_KEY` and a bot token). Without the Anthropic
  key the worker stops at startup with "ANTHROPIC_API_KEY is not set".
- No eval numbers yet: run `backend/eval` with keys. The built-in eval labels were written by the
  authors from public-health sources; get a second reviewer before publishing results.
- yt-dlp site support changes often; keep it updated. Instagram/Facebook post pages often show a
  login wall to servers, so those links may only get the preview or fail politely.
- The SSRF check resolves DNS before httpx connects (a small DNS-rebinding window remains; a
  pinned-IP transport would close it).
- The real E5 model test (`-m ml`) has not been run (needs `uv sync --extra ml`, ~2 GB).
  `MATCH_STRONG_THRESHOLD=0.88` is a starting guess; tune it with the eval.
- The service worker did not register in the Claude app's built-in browser ("unknown error
  when fetching the script", although `/sw.js` is served correctly). Check installability once
  in desktop Chrome or on an Android phone.
