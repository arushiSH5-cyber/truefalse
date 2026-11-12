# Your own labeled forwards

Put real forwards you have checked by hand here, one JSON object per line, in any number of
`*.jsonl` files (see `template.jsonl`). They are evaluated with `--dataset own`.

Fields:

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Any unique id |
| `language` | yes | BCP-47 code of the message (`hi`, `es`, `sw`, `hi-Latn` for romanized Hindi…) |
| `text` | yes | The message as it was forwarded |
| `expected` | yes | `true`, `misleading`, `false` or `unverified` |
| `acceptable` | no | Every verdict you'd count as right, e.g. `["false", "misleading"]` |
| `site` | no | Domain of the fact-check you used as the answer key; hidden from the systems |
| `source` | no | Link to that fact-check, for your own records |

**Privacy:** forwards often contain names, phone numbers and private group chatter. Remove
personal details before saving them here. Files in this folder other than the README and the
template are git-ignored, so they stay on your machine unless you decide otherwise.
