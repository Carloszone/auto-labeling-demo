"""Load canonical service and pipeline defaults from ``config/defaults.toml``."""

from __future__ import annotations

import tomllib
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "defaults.toml"


@lru_cache(maxsize=1)
def load_defaults() -> dict[str, Any]:
    """Read and minimally validate the versioned default-parameter document."""

    with DEFAULT_CONFIG_PATH.open("rb") as stream:
        data = tomllib.load(stream)
    if data.get("schema_version") != 1:
        raise ValueError("config/defaults.toml schema_version must be 1")
    for section in ("service", "multi_mcap", "pipeline"):
        if not isinstance(data.get(section), dict):
            raise ValueError(f"config/defaults.toml missing table: {section}")
    return data


def default_section(*path: str) -> dict[str, Any]:
    """Return a mutable deep copy of one mapping from the default configuration."""

    value: Any = load_defaults()
    for part in path:
        value = value[part]
    if not isinstance(value, dict):
        raise ValueError(f"default section is not a mapping: {'.'.join(path)}")
    return deepcopy(value)


SERVICE_DEFAULTS = default_section("service")
MULTI_MCAP_POLICY = default_section("multi_mcap")
PARSER_DEFAULTS = default_section("pipeline", "parser")
DATA_CHECK_DEFAULTS = default_section("pipeline", "data_check")
EVENT_GENERATION_DEFAULTS = default_section("pipeline", "event_generation")
EVENT_LABELING_DEFAULTS = default_section("pipeline", "event_labeling")
DEFAULT_SYSTEM_PROMPT = str(EVENT_LABELING_DEFAULTS["vlm_params"]["system_prompt"]).strip()
DEFAULT_INPUT_PROMPT = str(EVENT_LABELING_DEFAULTS["vlm_params"]["input_prompt"])
EVENT_LABELING_DEFAULTS["vlm_params"]["system_prompt"] = DEFAULT_SYSTEM_PROMPT


def copy_defaults(value: dict[str, Any]) -> dict[str, Any]:
    """Return mutable request-local defaults."""

    return deepcopy(value)
