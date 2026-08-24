from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from .config import ROOT

PATH = ROOT / ".streamlit" / "secrets.toml"


def read_secret(name: str) -> str:
    if os.getenv(name): return os.environ[name]
    try:
        with PATH.open("rb") as fh:
            return str(tomllib.load(fh).get(name, ""))
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return ""


def save_secret(name: str, value: str):
    values = {}
    try:
        with PATH.open("rb") as fh: values = tomllib.load(fh)
    except (FileNotFoundError, tomllib.TOMLDecodeError): pass
    values[name] = value.strip()
    PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key} = {json.dumps(str(val), ensure_ascii=False)}" for key, val in values.items()]
    PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    PATH.chmod(0o600)
