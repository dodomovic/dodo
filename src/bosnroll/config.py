from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "assets"
TEMPLATES = Path(__file__).resolve().parent / "templates"


def load_config(path: Path | None = None) -> dict:
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)
