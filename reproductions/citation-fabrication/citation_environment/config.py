from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def compose_file():
    path = ROOT / "docker" / "compose.yaml"
    if not path.is_file():
        raise FileNotFoundError("Run from the checkout or install in editable mode: pip install -e .")
    return str(path)
