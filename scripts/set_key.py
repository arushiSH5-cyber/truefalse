#!/usr/bin/env python3
"""Move API keys from the macOS clipboard into .env without showing them.

    python3 scripts/set_key.py            fills empty settings only
    python3 scripts/set_key.py --replace  also replaces keys that are already set

Accepts a bare Gemini or Anthropic key, or NAME=value lines. Key values are never printed, logged,
or passed as command arguments.
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAMES = (
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "SEARCH_API_KEY",
    "GOOGLE_FACTCHECK_API_KEY",
    "SEMANTIC_SCHOLAR_API_KEY",
    "NCBI_API_KEY",
)
BARE = {
    "GEMINI_API_KEY": re.compile(
        r"(?<![\w-])(AIza[\w-]{35}|AQ\.[\w.-]{20,})(?![\w-])", re.ASCII
    ),
    "ANTHROPIC_API_KEY": re.compile(r"(?<![\w-])(sk-ant-[\w-]{20,})(?![\w-])", re.ASCII),
}


def find_keys(text):
    found = {}
    for name in NAMES:
        match = re.search(
            rf"(?<!\w){name}[ \t]*=[ \t]*[\"']?([A-Za-z0-9_.\-]{{16,}})", text
        )
        if match:
            found[name] = match.group(1)
    for name, pattern in BARE.items():
        match = pattern.search(text)
        if name in found or not match:
            continue
        # A Google key already claimed by another name is not also the Gemini key.
        if match.group(1) not in found.values():
            found[name] = match.group(1)
    return found


def save(env, keys, replace=False):
    lines = env.read_text().splitlines()
    report = []
    for name, value in keys.items():
        at = [i for i, line in enumerate(lines) if line.startswith(f"{name}=")]
        current = lines[at[-1]].split("=", 1)[1].split(" #")[0].strip() if at else ""
        if current and not replace:
            report.append(f"{name}: already set, left alone (use --replace to change it)")
            continue
        if at:
            lines[at[-1]] = f"{name}={value}"
        else:
            lines.append(f"{name}={value}")
        report.append(f"{name}: saved")
    fd, tmp = tempfile.mkstemp(dir=env.parent, prefix=".env.")  # created 0600; .env.* is ignored
    with os.fdopen(fd, "w") as out:
        out.write("\n".join(lines) + "\n")
    os.replace(tmp, env)
    return report


def main():
    env = ROOT / ".env"
    if not env.exists():
        sys.exit("No .env yet. Run: cp .env.example .env")
    if subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", ".env"]).returncode:
        sys.exit("Stopped: .env is not ignored by git, so a key there could leak.")
    text = subprocess.run(["/usr/bin/pbpaste"], capture_output=True, text=True).stdout
    keys = find_keys(text)
    if not keys:
        sys.exit("No API key found on the clipboard. Copy the key again, then rerun this.")
    print("\n".join(save(env, keys, replace="--replace" in sys.argv[1:])))
    print("Done. Nothing was displayed. Copy something else to clear the key from your clipboard.")


if __name__ == "__main__":
    main()
