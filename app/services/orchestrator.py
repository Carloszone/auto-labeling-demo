"""Serial orchestration for the local core auto-labeling pipeline."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.core.config import build_run_config, load_robot_config
from app.modules.data_check.checker import DataChecker
from app.modules.event_generation.anomaly_generator import AnomalyGenerator
from app.modules.event_labeling.labeler import EventLabeler
from app.modules.parser.mcap_parser import McapParser
from app.services.vlm_client import HttpVlmClient

DEFAULT_SYSTEM_PROMPT = """You are a robotics manipulation annotation expert.
Analyze the ordered frames from one camera viewpoint from the robot perspective.
Frames marked context_before/context_after provide context; describe the action in event frames.
Return only a JSON object with exactly these fields:
{"action_summary": "concise action", "action_state": 1, "detailed_description": "detailed scene and interaction"}
action_state must be 1 for success, -1 for failure, or 0 when the segment is incomplete or uncertain.
Describe only observable robot, end-effector, object, spatial, and outcome details."""

DEFAULT_INPUT_PROMPT = (
    "Analyze this robot manipulation segment. Summarize the action, determine whether it "
    "succeeds, and describe object positions, state changes, and end-effector interactions."
)


class AutoLabelingService:
    """Run Parser, DataCheck, EventGeneration, and EventLabeling in order."""

    def __init__(
        self,
        parser: McapParser | None = None,
        checker: DataChecker | None = None,
        generator: AnomalyGenerator | None = None,
        vlm_client: Any | None = None,
    ) -> None:
        """Allow tests to inject modules while production uses default implementations."""

        self.parser = parser or McapParser()
        self.checker = checker or DataChecker()
        self.generator = generator or AnomalyGenerator()
        self.vlm_client = vlm_client

    def run(
        self,
        *,
        mcap_path: Path | str,
        robot_config_path: Path | str,
        output_path: Path | str | None = None,
        task_id: str = "local-run",
        job_id: str | None = None,
        max_frames: int | None = None,
        vlm_params: dict[str, Any] | None = None,
        vlm_endpoint: str | None = None,
        parser_config: dict[str, Any] | None = None,
        data_check_config: dict[str, Any] | None = None,
        event_generation_config: dict[str, Any] | None = None,
        event_labeling_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute one local run with caller overrides for each pipeline module."""

        run_config = build_run_config(mcap_path, robot_config_path, output_path)
        robot_config = load_robot_config(run_config.robot_config_path)
        parser_request = self._parser_request(
            task_id, job_id, run_config.mcap_path, robot_config, max_frames, parser_config
        )
        check_request_config = self._data_check_config(data_check_config)
        generation_request_config = _deep_merge(
            {
                "point_policy": {"mode": "pass_through"},
                "pairing_policy": {"mode": "adjacent_by_topic"},
            },
            event_generation_config,
        )
        generation_request_config.pop("basic", None)
        labeling_request_config = self._event_labeling_config(event_labeling_config, vlm_params)
        labeling_request_config.pop("basic", None)
        vlm_client = self.vlm_client or (HttpVlmClient(vlm_endpoint) if vlm_endpoint else None)
        if vlm_client is None:
            vlm_client = _UnavailableVlmClient()

        # Step 1: parse the MCAP using runtime paths and robot config.
        parser_info = self.parser.parse(parser_request)

        # Step 2: detect end-effector triggers and generate motion event periods.
        check_basic = check_request_config.pop("basic")
        check_basic.update({"task_id": task_id, "job_id": job_id, "parser_info": parser_info})
        check_info = self.checker.check({"basic": check_basic, **check_request_config})
        generation_info = self.generator.generate(
            {
                "basic": {"task_id": task_id, "job_id": job_id, "check_info": check_info, "parser_info": parser_info},
                **generation_request_config,
            }
        )

        # Step 3: label event periods and write the final JSON if requested.
        result = EventLabeler(vlm_client).label(
            {
                "basic": {
                    "task_id": task_id,
                    "job_id": job_id,
                    "parser_info": parser_info,
                    "generation_info": generation_info,
                },
                **labeling_request_config,
            }
        )
        if run_config.output_path is not None:
            run_config.output_path.parent.mkdir(parents=True, exist_ok=True)
            run_config.output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    def _parser_request(
        self,
        task_id: str,
        job_id: str | None,
        mcap_path: Path,
        robot_config: Any,
        max_frames: int | None,
        overrides: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build a Parser request while protecting runtime-owned input fields."""

        request = _deep_merge(
            {
                "insert": {"rotation": "ZYX", "max_tor_time_sec": 0.2},
                "output_format": {"include_vector_view": True, "include_component_schema": True},
            },
            overrides,
        )
        request["basic"] = {"task_id": task_id, "job_id": job_id}
        request["parser"] = {
            **request.get("parser", {}),
            "mcap_path": mcap_path,
            "file_type": "mcap",
            "robot_config": robot_config,
            "max_frames": max_frames,
        }
        return request

    def _data_check_config(self, overrides: dict[str, Any] | None) -> dict[str, Any]:
        """Return default DataCheck settings merged with caller-provided values."""

        return _deep_merge({
            "basic": {
                "eps": 1e-9,
                "fps": 30,
                "smooth": {"method": "savgol", "window_frame_length": 10, "polyorder": 3},
            },
            "data_detection": {
                "sudden_change_config": {
                    "enable": True,
                    "window_time_sec": 0.5,
                    "z_score": 3,
                    "sudden_time_sec": 0.066666667,
                    "step_time_sec": 0.5,
                    "zcr_ratio": 0.4,
                },
                "extreme_value_config": {"enable": True, "degree": 0.01, "expansion_coef": 0.2, "min_tor": 1e-4},
            },
            "image_detection": {"enable": True, "luminance": 10, "window_time_sec": 1.0, "lap_var": 150, "z_score": 2, "resize_length": 860, "resize_width": 640, "SSIM": 0.7, "pixel_mae": 5, "moving_area_ratio": 0.05},
            "trigger_detection": {
                "mode": "end_effector",
                "params": {
                    "algorithm": "Pelt",
                    "model": "clinear",
                    "pen": 15,
                    "min_duration_sec": 0.666666667,
                    "jump_frames": 1,
                    "state_count": 3,
                    "feature_window_sec": 0.166666667,
                    "stay_probability": 0.995,
                    "candidate_sigma_sec": 1.333333333,
                    "candidate_bonus": 1,
                },
            },
            "merge_policy": {"min_low_quality_time_sec": 0.166666667, "max_gap_time_sec": 0.2},
        }, overrides)

    def _event_labeling_config(
        self,
        overrides: dict[str, Any] | None,
        legacy_vlm_params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Merge EventLabeling settings and preserve the existing vlm_params argument."""

        config = _deep_merge(
            {
                "sampling": {
                    "mode": "fixed_sequence",
                    "params": {"fixed_frame_len": 20, "context_frame_len": 2},
                },
                "vlm_params": {
                    "model": "qwen/qwen3.5-9b",
                    "system_prompt": DEFAULT_SYSTEM_PROMPT,
                    "input_prompt": DEFAULT_INPUT_PROMPT,
                },
                "output": {
                    "layerId": "l2",
                    "category": "detail",
                    "attributes": {"scene": "tabletop", "sceneTags": []},
                },
            },
            overrides,
        )
        non_empty_legacy_vlm = {
            key: value for key, value in (legacy_vlm_params or {}).items() if value not in (None, "")
        }
        config["vlm_params"] = _deep_merge(config["vlm_params"], non_empty_legacy_vlm)
        for key, fallback in {
            "model": "qwen/qwen3.5-9b",
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
            "input_prompt": DEFAULT_INPUT_PROMPT,
        }.items():
            if not config["vlm_params"].get(key):
                config["vlm_params"][key] = fallback
        return config


def _deep_merge(defaults: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Recursively merge mappings without mutating caller-owned configuration."""

    if overrides is not None and not isinstance(overrides, dict):
        raise ValueError("module configuration overrides must be JSON objects")
    result = deepcopy(defaults)
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


class _UnavailableVlmClient:
    """Fail clearly if VLM is needed but no endpoint/client was provided."""

    def label(self, **_kwargs: Any) -> dict[str, Any]:
        """Raise a configuration error for non-empty event labeling runs."""

        raise ValueError("VLM endpoint or client is required when event_periods is not empty")
