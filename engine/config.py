from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_config() -> dict:
    with (ROOT / "config" / "thresholds.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)

