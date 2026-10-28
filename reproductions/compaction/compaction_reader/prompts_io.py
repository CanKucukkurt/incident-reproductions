"""Loading prompt/template files. No prompt text lives in Python."""

from __future__ import annotations

from pathlib import Path

from .errors import ConfigError


def read_prompt(prompts_dir: Path, *parts: str) -> str:
    p = Path(prompts_dir).joinpath(*parts)
    if not p.is_file():
        raise ConfigError(f"prompt file not found: {p}")
    return p.read_text(encoding="utf-8")


def render(template: str, **fields: str) -> str:
    """Minimal ``{name}`` substitution; ``{{`` and ``}}`` are literal braces."""
    out = template.replace("{{", "\x00").replace("}}", "\x01")
    for k, v in fields.items():
        out = out.replace("{" + k + "}", v)
    return out.replace("\x00", "{").replace("\x01", "}")


def slug(s: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
