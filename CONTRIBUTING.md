# Contributing to TrueFalse

## Setup

```bash
cd backend
uv sync
uv run pytest            # all tests run offline, no API keys needed
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```

CI runs the same checks on every push and pull request.

## How the code is organized

Each pipeline stage is its own module with a small interface:

| Stage | Module | Interface |
|---|---|---|
| Ingestion | `app/ingestion/` | `Messenger`, `JobQueue` (`ingestion/base.py`) |
| Media processing | `app/modality/` | `SpeechToText`, `ImageReader`, `TextToSpeech` (`modality/base.py`) |
| LLM | `app/core/llm.py` | `LLMClient` |
| Research sources | `app/research/sources/` | `Source` (`sources/base.py`) |

The pipeline code only uses these interfaces. Real implementations are connected in
`app/core/container.py`, and tests use the fakes in `backend/tests/fakes.py`.

## Adding a provider (voice, OCR, TTS, search, …)

1. Implement the Protocol in a new file, e.g. `app/modality/providers/google_stt.py`.
2. Read secrets from `Settings` (`app/core/config.py`) and document every new env var in `.env.example`.
3. Route all network calls through `app/core/http.py`, or the provider SDK's own timeout and retry options. Never call the network without a timeout.
4. Register the provider in `app/core/container.py` behind a config switch, e.g. `STT_PROVIDER=whisper|google`.
5. Add a fake to `tests/fakes.py` and add tests that run offline. If you parse API responses, test the parser against saved sample responses.

## Rules

- Never fabricate a verdict. Every non-"Unverified" verdict must cite retrieved evidence (`verify/judges.py: enforce_core_rule`).
- Prompts live in `app/prompts/*.md`. To change one, add a new version (`judge.v2.md`) instead of editing the old one.
- Don't log message text or user identifiers. Only log anonymized metrics.
- Delete raw media once it has been processed.
