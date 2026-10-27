# TrueFalse

A multilingual, multimodal AI misinformation checker.
Authors: Arushi Nirmal (San José State University).

Forward a suspicious message, screenshot, voice note, video or link, and TrueFalse
pulls out the factual claims and checks them against published fact-checks and trusted
sources. It replies in your language with:

- a verdict: **True** · **Misleading** · **False** · **Unverified**
- a short, plain-language explanation
- linked sources
- a confidence level with clear warnings

**Core rule:** TrueFalse never gives a verdict unless it has at least one cited source
that it actually retrieved. If the evidence is thin or the judges disagree, the answer
is *Unverified*.

## Status

Everything in the project proposal is built and tested offline (342 backend tests, ruff, strict
mypy, and a production build of the web app in CI):

- **Inputs:** text, screenshots and memes, voice notes, videos, and links (Instagram, X, YouTube,
  TikTok, Facebook, articles), on **Telegram**, **WhatsApp** and the **web app**.
- **Claims:** split into atomic claims with *who / did / where / when*, in any language.
- **Evidence:** Google Fact Check (ClaimReview from IFCN fact-checkers), news search (Brave News
  + GDELT), web search, Semantic Scholar and PubMed, plus an optional local fact-check index.
- **Verdict:** 2–3 judges on **different models** must cite evidence; disagreement gives
  *Unverified* and a `needs_human_review` flag instead of a guess.
- **AI-content checks:** Content Credentials (C2PA), file metadata, optional deepfake /
  synthetic-voice classifiers and reverse image search. Weak signals are never shown alone.
- **Reply:** verdict, two-line reason, up to 3 sources, and a short correction people can
  forward, in the user's language. Repeated forwards are answered from a 7-day verdict cache.
- **Evaluation:** `backend/eval/` compares the full pipeline, a single LLM judge and a
  fact-check lookup on X-FACT, MultiClaim and your own labeled forwards.

**Not run against the live APIs yet.** Every external service is tested against fakes and saved
sample responses, so the first run with real keys may surface integration issues. No accuracy
numbers exist until the eval is run with keys. See [GAP_ANALYSIS.md](GAP_ANALYSIS.md) for the
proposal-by-proposal status and [PROGRESS.md](PROGRESS.md) for the build history.

## How a check works

```
message / photo / voice / video / link
  ─► modality: OCR, speech-to-text, key frames + on-screen text, link download or preview
  ─► AI-content checks (credentials, metadata, optional classifiers, reverse image search)
  ─► detect language + extract claims (who / did / where / when)
  ─► verdict cache ─► fact-check index ─► research agent (fact-checks, news, web, papers)
  ─► judge panel on 2–3 models (must cite evidence ids) ─► calibration + core rule
  ─► grounding check + safety notes
  ─► reply in the user's language, with source links added by code
```

Sources are tiered (`backend/app/research/tiers.json`): 1 = peer-reviewed and official bodies,
2 = news wires and fact-checkers, 3 = other. Tier-3-only evidence never gets High confidence.

## Proposal modules → code

| Proposal | Code |
|---|---|
| `ingest` | `app/ingestion/` (Telegram, WhatsApp, web) + `app/modality/` (image, audio, video, links) |
| `claims` | `app/claims/` |
| `retrieval` | `app/matching/` (verdict cache, fact-check index) + `app/research/` (agent and sources) |
| `verdict` | `app/verify/` (judge panel, ratings, calibration) + `app/safety/` (grounding check) |
| `ai_detect` | `app/ai_detect/` |
| `delivery` | `app/respond/` (compose, translate, voice, share card) + `web/` |
| `eval` | `backend/eval/` |

## Layout

```
backend/app/
  api/        FastAPI routes: health, Telegram + WhatsApp webhooks, web /api/check
  ingestion/  platform adapters -> common Submission model, localized status messages
  modality/   text, image, audio, video, link processing
  ai_detect/  C2PA, metadata, classifiers, reverse image search, weak text signal
  claims/     claim extraction + normalization
  language/   script detection, language switch, per-language accuracy
  matching/   verdict cache, fact-check index, hybrid search
  research/   research agent + source clients
  verify/     judge panel, rating normalization, calibration
  safety/     grounding check, topic rules, PII redaction
  respond/    compose, translate, TTS, share cards
  core/       config, LLM providers, logging, db, queue, jobs
  prompts/    versioned LLM prompts (*.md)
backend/tests/   unit tests (run offline)
backend/eval/    evaluation harness (see backend/eval/README.md)
web/             Next.js PWA
```

## API keys

All keys go in `.env` (copy `.env.example`; every variable is explained there). Only the first
one is required; everything else switches a feature on.

| Key | Needed for |
|---|---|
| `ANTHROPIC_API_KEY` | **Required.** Claims, research, judges, replies, image reading |
| `TELEGRAM_BOT_TOKEN` (+ `TELEGRAM_WEBHOOK_SECRET`) | Telegram bot |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN` | WhatsApp bot |
| `GOOGLE_FACTCHECK_API_KEY` | Published fact-checks (strongly recommended) |
| `SEARCH_API_KEY` (Brave) | Web + news search. GDELT news works without a key |
| `OPENAI_API_KEY`, `GEMINI_API_KEY` | Judges from other vendors (`JUDGE_MODELS`) |
| `SIGHTENGINE_API_USER`/`_SECRET`, `HIVE_API_KEY` | Deepfake / AI-image classifiers (sends media to a third party) |
| `GOOGLE_VISION_API_KEY` | Reverse image search for recycled photos |
| `SEMANTIC_SCHOLAR_API_KEY`, `NCBI_API_KEY` | Higher rate limits only |

## Quick start (Docker)

```bash
cp .env.example .env        # fill in keys as you need them
docker compose up --build
curl localhost:8000/health
curl localhost:8000/health/ready   # checks Postgres + Redis
```

Services: `api` (FastAPI on :8000), `worker` (arq), `web` (the PWA on :3000), `postgres`
(with pgvector), `redis`. Open http://localhost:3000 to use the web app.

Database migrations: `docker compose run --rm api alembic upgrade head`

The worker image has no heavy extras by default, so text, photos and article links work but
voice notes, videos and reels do not. Build it with the media and AI-detection extras:

```bash
WORKER_EXTRAS="--extra media --extra ai_detect" docker compose build worker
```

### Fact-check index (optional)

```bash
cd backend
uv sync --extra ml                    # sentence-transformers (large download)
uv run alembic upgrade head
uv run python -m scripts.ingest_factchecks --file path/to/claimreview_feed.json
# then set FACTCHECK_INDEX_ENABLED=true
```

Get ClaimReview data from the [Data Commons fact-check download page](https://datacommons.org/factcheck/download).
For Docker, build the worker with the ML runtime: `WORKER_EXTRAS="--extra ml" docker compose build worker`.

## Local development (no Docker)

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
cd backend
uv sync --extra media --extra ai_detect   # same extras as CI; plain `uv sync` also works
uv run uvicorn app.main:app --reload       # API on :8000 (needs Redis for the web API and bots)
uv run arq app.worker.WorkerSettings       # worker, in a second terminal
uv run pytest                              # offline, no keys needed
uv run ruff check . && uv run ruff format --check .
uv run mypy app scripts eval
```

Voice notes and videos also need `ffmpeg` on the PATH.

## Try the Telegram bot locally

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Put `ANTHROPIC_API_KEY` and `TELEGRAM_BOT_TOKEN` in `.env`. The other source keys are optional; Semantic Scholar and PubMed work without a key.
3. Run the long-polling dev runner. It needs no webhook, Redis or worker:

   ```bash
   cd backend
   uv run python -m scripts.telegram_poll
   ```

4. Message your bot.

In production, use the webhook instead: run `docker compose up`, expose `/webhooks/telegram` over
HTTPS, and call Telegram's `setWebhook` with `secret_token=$TELEGRAM_WEBHOOK_SECRET`.

## WhatsApp

1. Create a Meta app with the WhatsApp product. Note the phone number ID, a permanent access token and the app secret.
2. Set `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_APP_SECRET` and a random `WHATSAPP_VERIFY_TOKEN`.
3. In the webhook settings, use callback URL `https://<your-host>/webhooks/whatsapp` and the same verify token, then subscribe to `messages`.

Every POST is checked against `X-Hub-Signature-256`. Users can send text, photos, voice notes, videos, or links.

## Web app (PWA) and share target

```bash
cd web
cp .env.example .env.local
npm install
npm run dev        # http://localhost:3000
```

The PWA is installable. Once installed on Android or ChromeOS, it shows up in the system **Share** sheet, so people can share a post, reel, photo or link from WhatsApp, Instagram, YouTube, TikTok, X or any other app straight into TrueFalse. Results show the verdict, a short explanation, sources behind "More", a **Listen** button (browser speech) and a **Share correction card** (PNG rendered by the API in the user's script).

## Languages and voice

- **Any language** works: the claim extractor detects the language (including romanized and code-mixed text), research runs through an English pivot, and replies come back in the user's language and script.
- **Switch language** by sending just its name, in any language: `Español`, `हिंदी`, `العربية`, `Kiswahili`, `中文`… Send `/language` to go back to auto-detect.
- **Voice**: a voice note gets a spoken reply. `/voice` turns spoken replies on for every answer. Needs `uv sync --extra voice` and ffmpeg.
- **Status messages** ("Checking…", errors, settings) come from a small table for common
  languages and are translated on the fly (fast model, cached) for every other language.
- **Less-tested languages** get a warning. The list comes from measured accuracy (`app/language/language_accuracy.json`, written by the eval harness) or falls back to a conservative default.
- Optional fast language-ID pre-check: `uv sync --extra lid` and set `LID_MODEL_PATH` to a fastText model (e.g. NLLB's `lid218e.bin`).

## AI-content detection

Runs on photos, video frames and audio before the claims are checked (`app/ai_detect/`).

- **Free, local, on by default:** C2PA Content Credentials (`uv sync --extra ai_detect`) and
  file metadata (IPTC "trained algorithmic media", EXIF generator tags, Stable Diffusion /
  ComfyUI parameters). Messaging apps usually strip both, so they help most with web uploads.
- **Optional, by key:** Sightengine or Hive deepfake / AI-image classifiers, Google Cloud Vision
  reverse image search (finds older copies of a "new" photo), and local Hugging Face image or
  synthetic-voice models (`AI_IMAGE_MODEL`, `AI_VOICE_MODEL`).
- **Weak only:** the vision model's own guess and the optional AI-written-text signal
  (`AI_TEXT_SIGNAL`) are logged and combined, but never shown to users on their own.

Missing credentials are reported as "no information", never as proof that something is real.
Invisible watermarks other than C2PA (such as SynthID) are not checked: there is no public
detector for them.

## Evaluation

```bash
cd backend
uv run python -m eval.run --dataset builtin --dry-run        # no API calls
uv run python -m eval.run --dataset builtin --dataset own --limit 50
```

Compares `full` (the pipeline), `single_judge` (one LLM, no search) and `lookup_only`
(fact-check database only) on accuracy, macro-F1 and false-alarm rate, per dataset and per
language. It prints a cost estimate and asks before spending money. Datasets, options and the
separate AI-detection scorer are described in [backend/eval/README.md](backend/eval/README.md).

## Engineering rules

- All secrets come from environment variables. `.env.example` documents each one.
- Every external API sits behind an interface that has a fake for tests, so tests run offline.
- Prompts live in `app/prompts/*.md` and are versioned.
- Logs are structured JSON with a request trace ID (`x-request-id`).
- Every network call has a timeout and retries, and falls back gracefully if it fails.
- Only content that users send is processed. Private groups are never scraped.
