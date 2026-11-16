# TrueFalse

TrueFalse checks whether a forwarded message is true. Send it text, a screenshot, a voice
note, a video or a link, in any language, and it replies in your language with:

- a verdict: 🟢 True · 🟠 Misleading · 🔴 False · ⚪ Unverified
- a short explanation
- links to the sources it used

It never gives a verdict without a real source. If the evidence is thin or unclear, the answer
is Unverified.

## Where you can use it

- Telegram bot
- WhatsApp bot
- Web app (installable on your phone)
- Chrome extension (right-click any text, link or image)

## How it works

1. Pulls the factual claims out of the message
2. Searches fact-checkers, news, PubMed and trusted sites
3. Three AI judges check each claim against the sources
4. Writes a short answer in the user's language

## Run it

You need Docker and an Anthropic API key.

```bash
cp .env.example .env    # add ANTHROPIC_API_KEY
docker compose up --build
```

- API: http://localhost:8000
- Web app: http://localhost:3000
- Chrome extension: load the `extension/` folder at `chrome://extensions` (Developer mode)

## Tests

```bash
cd backend && uv sync && uv run pytest
```

## Built with

Python, FastAPI, Postgres + pgvector, Redis, LLM APIs, Next.js, Tailwind

## Project layout

| Folder | What's in it |
|---|---|
| `backend/` | API, worker, fact-checking pipeline, tests, evaluation |
| `web/` | Next.js web app |
| `extension/` | Chrome extension |
| `infra/` | Database setup |

More detail: [PROGRESS.md](PROGRESS.md) (what's built) and [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT, made by Arushi Nirmal
