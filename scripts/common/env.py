"""Minimil `.env` loader (stdlib only, no new dependency).

- Reads `ROOT/.env` (gitignored) and fills only variables that are NOT
  already set in the process environment — explicit env always wins.
- Supports `KEY=value`, `KEY="quoted value"`, `export KEY=value`, `#` comments.
- Never prints values; callers must log names only.
"""
from __future__ import annotations

import os
from pathlib import Path


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse dotenv text into a dict. Pure function (testable, no I/O)."""
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name.startswith("export "):
            name = name[len("export "):].strip()
        if not name or not (name[0].isalpha() or name[0] == "_"):
            continue
        if not all(c.isalnum() or c == "_" for c in name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        out[name] = value
    return out


def load_dotenv(path: str | Path | None = None) -> list[str]:
    """Load `.env` into `os.environ` (missing keys only). Returns loaded names."""
    from scripts.common.config import ROOT

    dotenv_path = Path(path) if path else ROOT / ".env"
    try:
        text = dotenv_path.read_text(encoding="utf-8")
    except OSError:
        return []
    loaded: list[str] = []
    for name, value in parse_dotenv(text).items():
        if name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded
