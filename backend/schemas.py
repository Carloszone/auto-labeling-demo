"""HTTP request schemas for running and reviewing one job."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _at(mapping: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = mapping
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _validate_number(mapping: dict[str, Any], path: tuple[str, ...], low: float, high: float | None = None) -> None:
    value = _at(mapping, path)
    if value is None:
        return
    label = ".".join(path)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    if value < low or (high is not None and value > high):
        upper = f" and <= {high}" if high is not None else ""
        raise ValueError(f"{label} must be >= {low}{upper}")


def _validate_integer(mapping: dict[str, Any], path: tuple[str, ...], low: int, high: int) -> None:
    value = _at(mapping, path)
    if value is None:
        return
    label = ".".join(path)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in [{low}, {high}]")


class RunRequest(BaseModel):
    """User-editable pipeline settings passed to the existing orchestrator."""

    model_config = ConfigDict(extra="forbid")

    input_prompt: str = Field(default="", max_length=8000)
    system_prompt: str = Field(default="", max_length=20000)
    robot_config_overrides: dict[str, Any] = Field(default_factory=dict)
    parser_config: dict[str, Any] = Field(default_factory=dict)
    data_check_config: dict[str, Any] = Field(default_factory=dict)
    event_generation_config: dict[str, Any] = Field(default_factory=dict)
    event_labeling_config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_page_overrides(self) -> "RunRequest":
        main_topic = self.robot_config_overrides.get("main_time_topic")
        if main_topic is not None and (not isinstance(main_topic, str) or not main_topic.strip()):
            raise ValueError("robot_config_overrides.main_time_topic must be a non-empty string")

        for path in (
            ("insert", "max_tor_time_sec"),
        ):
            _validate_number(self.parser_config, path, 0)
        for path in (
            ("data_detection", "sudden_change_config", "window_time_sec"),
            ("data_detection", "sudden_change_config", "z_score"),
            ("data_detection", "sudden_change_config", "sudden_time_sec"),
            ("data_detection", "sudden_change_config", "step_time_sec"),
            ("data_detection", "extreme_value_config", "min_tor"),
            ("image_detection", "luminance"),
            ("image_detection", "window_time_sec"),
            ("image_detection", "lap_var"),
            ("image_detection", "z_score"),
            ("image_detection", "pixel_mae"),
            ("merge_policy", "min_low_quality_time_sec"),
            ("merge_policy", "max_gap_time_sec"),
        ):
            _validate_number(self.data_check_config, path, 0)
        for path in (
            ("data_detection", "sudden_change_config", "zcr_ratio"),
            ("data_detection", "extreme_value_config", "degree"),
            ("image_detection", "SSIM"),
            ("image_detection", "moving_area_ratio"),
        ):
            _validate_number(self.data_check_config, path, 0, 1)
        _validate_number(
            self.data_check_config,
            ("data_detection", "extreme_value_config", "expansion_coef"),
            0,
        )
        _validate_integer(self.data_check_config, ("image_detection", "resize_length"), 64, 4096)
        _validate_integer(self.data_check_config, ("image_detection", "resize_width"), 64, 4096)
        _validate_integer(
            self.event_labeling_config, ("sampling", "params", "fixed_frame_len"), 1, 20
        )
        _validate_integer(
            self.event_labeling_config,
            ("sampling", "params", "sampling_frame_gap"),
            1,
            1_000_000,
        )
        _validate_integer(
            self.event_labeling_config, ("sampling", "params", "context_frame_len"), 0, 10
        )
        _validate_number(
            self.event_labeling_config, ("vlm_params", "temperature"), 0, 2
        )
        _validate_integer(
            self.event_labeling_config, ("vlm_params", "max_output_tokens"), 1, 1_000_000
        )
        reasoning = _at(self.event_labeling_config, ("vlm_params", "reasoning"))
        if reasoning is not None and reasoning not in {"off", "on"}:
            raise ValueError("event_labeling_config.vlm_params.reasoning must be off or on")
        store = _at(self.event_labeling_config, ("vlm_params", "store"))
        if store is not None and not isinstance(store, bool):
            raise ValueError("event_labeling_config.vlm_params.store must be a boolean")
        return self


class EventPatch(BaseModel):
    """Editable event fields exposed by the review UI."""

    model_config = ConfigDict(extra="forbid")

    start_sec: float | None = Field(default=None, ge=0)
    end_sec: float | None = Field(default=None, ge=0)
    prompt: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=4000)
    action_state: Literal[-1, 0, 1] | None = None
    review_status: Literal["pending", "accepted", "rejected"] | None = None

    @model_validator(mode="after")
    def require_one_field(self) -> "EventPatch":
        """Reject no-op PATCH requests."""

        if not self.model_fields_set:
            raise ValueError("at least one event field is required")
        return self
