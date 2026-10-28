"""Versioned prompt reconstructions, with no answers or continuation prefills."""

import json
from pathlib import Path
from urllib.parse import urlsplit

PROMPT_FILE = Path(__file__).with_name("prompts") / "incident.json"
DEFAULT_PROMPT = "incident_browser_citations"


def load_prompts():
    return json.loads(PROMPT_FILE.read_text(encoding="utf-8"))


def render_prompts(map_url, prompt_ids):
    parts = urlsplit(map_url)
    if (parts.scheme not in ("http", "https") or not parts.hostname
            or parts.username is not None or parts.password is not None
            or any(c.isspace() for c in map_url)):
        raise ValueError("map_url must be an HTTP(S) URL without credentials or whitespace")
    catalog = load_prompts()
    variants = {p["id"]: p for p in catalog["variants"]}
    selected = list(variants) if prompt_ids == ["all"] else prompt_ids
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("Choose at least one prompt, without duplicates")
    rendered = []
    for prompt_id in selected:
        if prompt_id not in variants:
            raise ValueError("Unknown prompt: " + prompt_id)
        variant = variants[prompt_id]
        rendered.append({
            "id": prompt_id,
            "description": variant["description"],
            "messages": [
                {"role": "system", "content": variant["system"]},
                {"role": "user", "content": catalog["user"].format(map_url=map_url)},
            ],
        })
    return rendered
