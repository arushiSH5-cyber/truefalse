"""Loads versioned prompts from app/prompts/*.md and fills {{placeholders}}."""

import re
from functools import cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class PromptError(Exception):
    pass


@cache
def load(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise PromptError(f"prompt not found: {name}")
    return path.read_text(encoding="utf-8")


def placeholders(name: str) -> set[str]:
    return set(_PLACEHOLDER.findall(load(name)))


def render(name: str, **values: str) -> str:
    missing = placeholders(name) - values.keys()
    if missing:
        raise PromptError(f"prompt {name} missing values: {sorted(missing)}")
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], load(name))
