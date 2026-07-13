"""Runtime configuration loading for the local auto-labeling MVP."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class TopicConfig:
    """Describes one robot topic consumed by parser or downstream modules."""

    name: str
    topic: str
    role: str
    parser: str = ""
    dtype: str = "float32"
    fields: list[str] = field(default_factory=list)
    shape: list[int] = field(default_factory=list)
    required: bool = False
    sync_policy: str = ""
    missing_policy: str = "zero"
    group: str = ""
    key: str = ""
    encoding: str = ""
    format: str = "raw"
    width: int = 0
    height: int = 0
    output_video: bool = False
    video_dir: str = ""


@dataclass(slots=True)
class PlaybackConfig:
    """Playback options used when images are rendered or exported."""

    img_rotation: int | float | str = 0


@dataclass(slots=True)
class FullAnnotationConfig:
    """Container for full annotation service options from robot config JSON."""

    playback: PlaybackConfig = field(default_factory=PlaybackConfig)


@dataclass(slots=True)
class RobotConfig:
    """Parsed robot/topic configuration for one MCAP processing run."""

    schema_version: str
    robot_type: str
    robot_name: str
    description: str
    main_time_topic: str
    cameras: list[TopicConfig]
    observation_state: list[TopicConfig]
    action: list[TopicConfig]
    full_annotation: FullAnnotationConfig = field(default_factory=FullAnnotationConfig)


@dataclass(slots=True)
class RunConfig:
    """Runtime file paths for a single local auto-labeling execution."""

    mcap_path: Path
    robot_config_path: Path
    output_path: Path | None = None


def _topic_from_dict(raw: dict[str, Any]) -> TopicConfig:
    """Convert one topic dictionary from JSON into a typed topic config."""

    return TopicConfig(
        name=str(raw.get("name", raw.get("key", ""))),
        topic=str(raw["topic"]),
        role=str(raw.get("role", "")),
        parser=str(raw.get("parser", raw.get("role", ""))),
        dtype=str(raw.get("dtype", "float32")),
        fields=list(raw.get("fields", [])),
        shape=list(raw.get("shape", [])),
        required=bool(raw.get("required", False)),
        sync_policy=str(raw.get("sync_policy", "")),
        missing_policy=str(raw.get("missing_policy", "zero")),
        group=str(raw.get("group", "")),
        key=str(raw.get("key", "")),
        encoding=str(raw.get("encoding", "")),
        format=str(raw.get("format", "raw")),
        width=int(raw.get("width", 0)),
        height=int(raw.get("height", 0)),
        output_video=bool(raw.get("output_video", False)),
        video_dir=str(raw.get("video_dir", "")),
    )


def load_robot_config(path: Path | str) -> RobotConfig:
    """Load a robot/topic configuration JSON file for the current run."""

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(config_path)

    raw = json.loads(config_path.read_text())
    playback_raw = raw.get("full_annotation", {}).get("playback", {})

    return RobotConfig(
        schema_version=str(raw.get("schema_version", "")),
        robot_type=str(raw.get("robot_type", "")),
        robot_name=str(raw.get("robot_name", "")),
        description=str(raw.get("description", "")),
        main_time_topic=str(raw["main_time_topic"]),
        cameras=[_topic_from_dict(item) for item in raw.get("cameras", [])],
        observation_state=[_topic_from_dict(item) for item in raw.get("observation_state", [])],
        action=[_topic_from_dict(item) for item in raw.get("action", [])],
        full_annotation=FullAnnotationConfig(
            playback=PlaybackConfig(img_rotation=playback_raw.get("img_rotation", 0))
        ),
    )


def build_run_config(
    mcap_path: Path | str,
    robot_config_path: Path | str,
    output_path: Path | str | None = None,
) -> RunConfig:
    """Validate and package runtime paths without hard-coding test files."""

    resolved_mcap_path = Path(mcap_path)
    resolved_robot_config_path = Path(robot_config_path)
    if not resolved_mcap_path.exists():
        raise FileNotFoundError(resolved_mcap_path)
    if not resolved_robot_config_path.exists():
        raise FileNotFoundError(resolved_robot_config_path)

    return RunConfig(
        mcap_path=resolved_mcap_path,
        robot_config_path=resolved_robot_config_path,
        output_path=Path(output_path) if output_path is not None else None,
    )
