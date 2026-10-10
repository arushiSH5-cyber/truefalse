# Evaluation datasets

| File | What | Size |
|---|---|---|
| `health_cures.jsonl` | Fake cures, unsafe home treatments, anti-medical advice, superstitions presented as fact, plus true health statements as controls | 38 |
| `multilingual_claims.jsonl` | The same claims across 15 languages and 10 scripts, including romanized and code-mixed text (Hinglish, Spanglish, Tanglish) | 30 |

Fields: `expected` is the main label; `acceptable` lists every verdict we count as correct (e.g. "unverified" is acceptable when public evidence is thin). `basis` notes the public-health source the label relies on.

**Labels were written by the authors** from well-established public positions (WHO myth-busters, CDC/NHS/FDA guidance, standard first aid). Have them reviewed by a second person before publishing results. Benchmark datasets (X-FACT, MultiClaim, FEVER) are loaded by the Phase 8 harness from local files.
