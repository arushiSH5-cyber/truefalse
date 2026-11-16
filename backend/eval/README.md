# Evaluation

Compares three systems on labeled claims, and scores AI-content detection separately.

| System | What it does |
|---|---|
| `full` | The TrueFalse pipeline: claim extraction, research, judge panel, grounding |
| `single_judge` | One LLM call per claim with no search (the main model, `ANTHROPIC_MODEL`) |
| `lookup_only` | Fact-check database lookup only (Google Fact Check Tools API + local index); publishers' ratings decide, no hit = Unverified |

**Metrics:** accuracy (verdict in the item's acceptable set), macro-F1 over true/misleading/false,
false-alarm rate (true claims called False or Misleading), Unverified rate, cost and latency
per item. All overall, per dataset, and per language.

**Answer-key hiding:** X-FACT claims come from fact-check sites. Every system drops evidence
from the item's own `site`, otherwise it could score by finding the answer. The verdict cache
is off during evaluation.

## Datasets

| `--dataset` | Source | Notes |
|---|---|---|
| `builtin` | `datasets/health_cures.jsonl`, `datasets/multilingual_claims.jsonl` | 68 items written by the authors; see `datasets/README.md` |
| `own` | `datasets/own_forwards/*.jsonl` | Your own labeled forwards; see that folder's README. Git-ignored. |
| `xfact:PATH` | [X-FACT](https://github.com/utahnlp/x-fact), `data/x-fact/test.all.tsv` (also `ood.tsv`, `zeroshot.tsv`) | 25 languages. "complicated" and "other" labels are skipped. |
| `multiclaim:DIR` | [MultiClaim](https://zenodo.org/records/7737983) folder with `posts.csv` (access on request) | Only false/misleading posts (Facebook labels): measures how many are caught; no false-alarm rate. |
| `jsonl:PATH` | Any file in the JSONL format | |

## Run

From `backend/`, with keys in `.env` (the same ones the bots use):

```bash
uv run python -m eval.run --dataset builtin --dry-run
uv run python -m eval.run --dataset builtin --dataset own --limit 50
uv run python -m eval.run --dataset xfact:~/x-fact/data/x-fact/test.all.tsv --limit 300 --write-language-accuracy
```

- `--dry-run` makes no API calls (random verdicts) to check datasets and reports.
- `--limit` samples evenly across dataset and language (default 300, `0` = all). `--seed` fixes the sample.
- A rough cost is printed first and the run asks before spending money (`--yes` skips the question).
  With the default models, `full` is roughly $0.30 per claim, `single_judge` about $0.01, and
  `lookup_only` close to nothing. The real cost per item is in the report.
- Results go to `eval/results/<timestamp>/` (git-ignored): `items.jsonl`, `predictions.jsonl`,
  `report.json`, `report.md`. Re-running with the same `--out` resumes an interrupted run;
  `--report-only --out DIR` rebuilds the report.
- `--write-language-accuracy` writes the full system's per-language macro-F1 to
  `app/language/language_accuracy.json`, which decides the "less tested in your language"
  warning (languages with at least 30 examples; see `app/language/accuracy.py`).

## AI-content detection

```bash
uv run python -m eval.ai_detect path/to/media --out eval/results/ai-media.json
```

Put files in `path/to/media/ai/` and `path/to/media/real/` (images, video and audio can be
mixed). It runs the detectors configured in `.env` and reports precision, recall, F1 and the
false-alarm rate of the warning users would see, plus how often each detector fired on AI and
on real files. Images are sent to third-party detectors only if their keys are set.
