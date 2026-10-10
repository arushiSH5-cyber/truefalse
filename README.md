# TrueFalse

A multilingual, multimodal AI misinformation checker.
Authors: **Arushi Nirmal** and **Arpanjeet Singh** (San José State University).

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

Phase 1 (skeleton) is done. See [PROGRESS.md](PROGRESS.md) for the phase plan.

## Layout

```
backend/app/
  api/        FastAPI routes, bot webhooks
  ingestion/  platform adapters -> common Submission model
  modality/   text, image, audio, video, link processing
  claims/     claim extraction + normalization
  matching/   fact-check index, hybrid search
  research/   research agent + source clients
  verify/     judge panel, calibration
  safety/     grounding check, topic rules, PII redaction
  respond/    compose, translate, TTS, share cards
  core/       config, logging, db, queue
  prompts/    versioned LLM prompts (*.md)
backend/tests/   unit tests (run offline)
backend/eval/    benchmark harness
web/             Next.js PWA (Phase 7)
extension/       Chrome extension (Phase 9)
```

## Quick start (Docker)

```bash
cp .env.example .env        # fill in keys as you need them
docker compose up --build
curl localhost:8000/health
curl localhost:8000/health/ready   # checks Postgres + Redis
```

Services: `api` (FastAPI on :8000), `worker` (arq), `postgres` (with pgvector), `redis`.

## Local development (no Docker)

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```

## Engineering rules

- All secrets come from environment variables. `.env.example` documents each one.
- Every external API sits behind an interface that has a fake for tests, so tests run offline.
- Prompts live in `app/prompts/*.md` and are versioned.
- Logs are structured JSON with a request trace ID (`x-request-id`).
- Every network call has a timeout and retries, and falls back gracefully if it fails.
- Only content that users send is processed. Private groups are never scraped.
