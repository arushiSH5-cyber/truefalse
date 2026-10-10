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

## Next: Phase 3, fact-check index
- Ingestion script for ClaimReview data into Postgres/pgvector (Alembic migrations start here)
- Multilingual embeddings (LaBSE or multilingual-e5) plus BM25 hybrid search; cross-language matching tests
- Plug the index in ahead of the research agent (a strong match skips most of the research)

## Deferred (planned in later phases)
- Per-user state: first-time welcome in the detected language, language switching ("Español"), opt-in digest. These need a users table and come with Phase 3 migrations or Phase 9.
- Thumbs up/down, "Report a mistake", review queue (Phase 9)
- Three-judge panel, calibration, disagreement detection, grounding check (Phase 4)
- fastText lid-218 as a fast language pre-check (Phase 6)

## Blockers / notes
- **Not yet run against real APIs.** Every external call is tested only against fakes and saved sample responses. The first live run needs `ANTHROPIC_API_KEY` + `TELEGRAM_BOT_TOKEN` (and ideally `GOOGLE_FACTCHECK_API_KEY` and a Brave `SEARCH_API_KEY`).
- Docker and uv are not installed on the dev Mac, so `docker compose up` has not been run locally. CI covers lint, types and tests on Python 3.12.
- `uv.lock` is not committed yet. Run `uv lock` once uv is installed.
