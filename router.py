"""Filename-based routing — matches filenames against table_config.json patterns."""

import json
import re
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "table_config.json"


def load_config(config_path: str | Path | None = None) -> list[dict]:
    """Load mappings from table_config.json."""
    path = Path(config_path) if config_path else CONFIG_PATH
    with open(path) as f:
        data = json.load(f)
    return data.get("mappings", [])


def route_filename(filename: str, mappings: list[dict] | None = None) -> str:
    """Match a filename to a target table name.

    Returns the matched table name, or a sanitised version of the filename stem
    if no pattern matches.
    """
    if mappings is None:
        mappings = load_config()

    stem = Path(filename).stem.lower()

    for mapping in mappings:
        if re.search(mapping["pattern"], stem, re.IGNORECASE):
            return mapping["table"]

    # No match — derive table name from filename
    sanitised = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    return sanitised or "unknown"
