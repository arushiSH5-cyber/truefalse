# Progress

## Done

### Phase 1: Skeleton (2026-10-10)
- Repo layout for every pipeline stage (`backend/app/*`), plus `web/`, `extension/` and `eval/` placeholders
- FastAPI app with `/health` (liveness) and `/health/ready` (Postgres + Redis checks; 503 when degraded)
- Structured JSON logging (structlog), with a trace ID per request through `x-request-id` and request latency
- Settings via pydantic-settings; secrets are `SecretStr`; `.env.example` documents every key
- arq worker (`app.worker.WorkerSettings`) with a smoke-test job
- Docker Compose: api, worker, postgres (pgvector/pgvector:pg16, `vector` extension enabled on init), redis
- GitHub Actions CI: ruff lint + format check, mypy (strict), pytest on Python 3.12 using uv
- 6 offline tests pass

## Next: Phase 2, text MVP on Telegram
- `Submission` / `ProcessedContent` / `Claim` / `Evidence` / `Verdict` Pydantic models
- Interfaces + fakes: LLM client, Telegram, Semantic Scholar, PubMed, Google Fact Check, web search
- Language ID (fastText lid-218) with an English pivot for research; reply in the detected language
- Claim extraction prompt (`prompts/claim_extract.v1.md`), research agent with capped tool calls, a first verify step
- Telegram webhook + "Checking…" acknowledgment
- Alembic migrations and the first tables (submissions metadata, feedback)
- Pick a web search provider for `SEARCH_API_KEY` (to decide)

## Blockers / notes
- Docker, uv and Python 3.12 are not installed on the dev Mac yet. Phase 1 was checked with
  a Python 3.14 venv (ruff, mypy, pytest all pass). `docker compose up` has **not** been run
  locally yet.
- `uv.lock` is not committed yet. Generate it with `uv lock` once uv is installed.
- API keys needed for Phase 2: ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, GOOGLE_FACTCHECK_API_KEY, SEARCH_API_KEY.
