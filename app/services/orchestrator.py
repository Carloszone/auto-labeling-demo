"""Serial orchestration for the local core auto-labeling pipeline."""

from __future__ import annotations

import json
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
    ) -> dict[str, Any]:
        """Execute one local run and optionally write the final annotation JSON."""

        run_config = build_run_config(mcap_path, robot_config_path, output_path)
        robot_config = load_robot_config(run_config.robot_config_path)
        provided_vlm = vlm_params or {}
        vlm_params = {
            "model": provided_vlm.get("model") or "qwen/qwen3.5-9b",
            "system_prompt": provided_vlm.get("system_prompt") or DEFAULT_SYSTEM_PROMPT,
            "input_prompt": provided_vlm.get("input_prompt") or DEFAULT_INPUT_PROMPT,
        }
        vlm_client = self.vlm_client or HttpVlmClient(vlm_endpoint) if vlm_endpoint else self.vlm_client
        if vlm_client is None:
            vlm_client = _UnavailableVlmClient()

        # Step 1: parse the MCAP using runtime paths and robot config.
        parser_info = self.parser.parse(
            {
                "basic": {"task_id": task_id, "job_id": job_id},
                "parser": {
                    "mcap_path": run_config.mcap_path,
                    "file_type": "mcap",
                    "robot_config": robot_config,
                    "max_frames": max_frames,
                },
                "insert": {"max_tor_time_sec": 0.2},
                "output_format": {"include_vector_view": True, "include_component_schema": True},
            }
        )

        # Step 2: detect end-effector triggers and generate motion event periods.
        check_info = self.checker.check(self._data_check_request(task_id, job_id, parser_info))
        generation_info = self.generator.generate(
            {
                "basic": {"task_id": task_id, "job_id": job_id, "check_info": check_info, "parser_info": parser_info},
                "point_policy": {
                    "mode": "pass_through",
                },
                "pairing_policy": {"mode": "adjacent_by_topic"},
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
                "sampling": {
                    "mode": "fixed_sequence",
                    "params": {"fixed_frame_len": 20, "context_frame_len": 2},
                },
                "vlm_params": vlm_params,
                "output": {"layerId": "l2", "category": "detail", "attributes": {"scene": "tabletop", "sceneTags": []}},
            }
        )
        if run_config.output_path is not None:
            run_config.output_path.parent.mkdir(parents=True, exist_ok=True)
            run_config.output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    def _data_check_request(self, task_id: str, job_id: str | None, parser_info: dict[str, Any]) -> dict[str, Any]:
        """Build the default MVP DataCheck request used by local orchestration."""

        return {
            "basic": {
                "task_id": task_id,
                "job_id": job_id,
                "eps": 1e-9,
                "fps": 30,
                "smooth": {"method": "savgol", "window_frame_length": 10, "polyorder": 3},
                "parser_info": parser_info,
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
            "image_detection": {"enable": True, "luminance": 10, "window_time_sec": 1.0, "lap_var": 150, "z_score": 2, "resize_length": 640, "resize_width": 480, "SSIM": 0.7, "pixel_mae": 5, "moving_area_ratio": 0.05},
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
        }


class _UnavailableVlmClient:
    """Fail clearly if VLM is needed but no endpoint/client was provided."""

    def label(self, **_kwargs: Any) -> dict[str, Any]:
        """Raise a configuration error for non-empty event labeling runs."""

        raise ValueError("VLM endpoint or client is required when event_periods is not empty")
