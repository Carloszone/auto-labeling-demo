"""Event range generation, image sampling, and VLM result formatting."""

from __future__ import annotations

import base64
import logging
import time
from typing import Any, Protocol


LOGGER = logging.getLogger(__name__)
MAX_EVENT_FRAME_LEN = 20


class VlmClientProtocol(Protocol):
    """Protocol implemented by VLM clients used by EventLabeler."""

    def label(self, *, model: str, system_prompt: str, input_prompt: str, samples: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        """Return one structured VLM label for sampled event frames."""


class EventLabeler:
    """Consume event periods, sample images, call VLM, and format annotations."""

    def __init__(self, vlm_client: VlmClientProtocol) -> None:
        """Store the injected VLM client so tests can avoid network calls."""

        self._vlm_client = vlm_client
        self._last_segment_id = 0

    def label(self, request: dict[str, Any]) -> dict[str, Any]:
        """Run the event labeling flow for one parser/generation result pair."""

        basic = request.get("basic")
        sampling = request.get("sampling")
        vlm_params = request.get("vlm_params")
        if not isinstance(basic, dict) or not isinstance(sampling, dict) or not isinstance(vlm_params, dict):
            raise ValueError("request must include basic, sampling, and vlm_params")
        task_id = basic.get("task_id")
        if not task_id:
            raise ValueError("basic.task_id is required")
        parser_info = basic.get("parser_info")
        generation_info = basic.get("generation_info")
        if not isinstance(parser_info, dict) or not isinstance(generation_info, dict):
            raise ValueError("parser_info and generation_info are required")
        if not isinstance(parser_info.get("timestamp_list"), list) or not parser_info["timestamp_list"]:
            raise ValueError("parser_info.timestamp_list must be a non-empty list")
        if not isinstance(parser_info.get("image_list"), list) or len(parser_info["image_list"]) != len(parser_info["timestamp_list"]):
            raise ValueError("parser_info.image_list must align with timestamp_list")
        if not vlm_params.get("model"):
            raise ValueError("vlm_params.model is required")

        event_periods = generation_info.get("event_periods")
        if not isinstance(event_periods, dict):
            raise ValueError("generation_info.event_periods must be grouped by topic")
        if not event_periods:
            return {"task_id": task_id, "job_id": basic.get("job_id"), "response": []}

        fixed_frame_len, context_frame_len = self._sampling_lengths(sampling)
        flattened = [period for topic_periods in event_periods.values() for period in topic_periods]
        flattened.sort(key=lambda period: (int(period["start_index"]), str(period["topic_key"])))
        periods = self._validated_periods(parser_info["timestamp_list"], flattened)
        output = request.get("output", {}) if isinstance(request.get("output", {}), dict) else {}
        response = []

        # Step 1: validate event periods generated upstream.
        # Step 2: sample each period from parser image frames.
        # Step 3: call VLM for each sampled period and map the response to annotation JSON.
        for period in periods:
            topic_key = str(period.get("topic_key", ""))
            if not topic_key:
                raise ValueError("event period topic_key is required")
            samples = self._sample_images(
                parser_info["image_list"],
                parser_info["timestamp_list"],
                period["start_index"],
                period["end_index"],
                fixed_frame_len,
                context_frame_len,
                topic_key,
            )
            topic_samples = samples[topic_key]
            LOGGER.info(
                "VLM sampling task_id=%s topic_key=%s start_index=%s end_index=%s "
                "event_source_frames=%s event_sample_frames=%s context_before_frames=%s "
                "context_after_frames=%s max_event_frames=%s",
                task_id,
                topic_key,
                period["start_index"],
                period["end_index"],
                int(period["end_index"]) - int(period["start_index"]) + 1,
                sum(item["sample_role"] == "event" for item in topic_samples),
                sum(item["sample_role"] == "context_before" for item in topic_samples),
                sum(item["sample_role"] == "context_after" for item in topic_samples),
                MAX_EVENT_FRAME_LEN,
            )
            vlm_result = self._vlm_client.label(
                model=str(vlm_params["model"]),
                system_prompt=str(vlm_params.get("system_prompt", "")),
                input_prompt=str(vlm_params.get("input_prompt", "")),
                samples=samples,
            )
            response.append(
                self._format_annotation(
                    period,
                    samples,
                    vlm_result,
                    output,
                    topic_key,
                )
            )

        return {"task_id": task_id, "job_id": basic.get("job_id"), "response": response}

    def _validated_periods(
        self, timestamps: list[dict[str, str]], periods: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Validate generation-owned period indexes and absolute timestamps."""

        timeline = [int(item["timestamp_ns"]) for item in timestamps]
        if any(left >= right for left, right in zip(timeline, timeline[1:])):
            raise ValueError("timestamp_list must be strictly monotonic increasing")
        validated: list[dict[str, Any]] = []
        previous_start = -1
        for period in periods:
            if not isinstance(period, dict):
                raise ValueError("event period must be a dictionary")
            if "start_index" not in period or "end_index" not in period:
                raise ValueError("event period requires start_index and end_index")
            start, end = int(period["start_index"]), int(period["end_index"])
            if start < 0 or end < start or end >= len(timestamps):
                raise ValueError("event period indexes are out of range")
            if start < previous_start:
                raise ValueError("event periods must be ordered by start_index")
            if int(period.get("startTimeNs", timeline[start])) != timeline[start]:
                raise ValueError("event period start timestamp does not match timestamp_list")
            if int(period.get("endTimeNs", timeline[end])) != timeline[end]:
                raise ValueError("event period end timestamp does not match timestamp_list")
            validated.append(dict(period))
            previous_start = start
        return validated

    def _sampling_lengths(self, sampling: dict[str, Any]) -> tuple[int, int]:
        """Extract event and per-side context sampling lengths in frames."""

        if sampling.get("mode") != "fixed_sequence":
            raise ValueError("only fixed_sequence sampling is supported")
        params = sampling.get("params", {})
        fixed_frame_len = params.get("fixed_frame_len")
        context_frame_len = params.get("context_frame_len", 2)
        if not isinstance(fixed_frame_len, int) or fixed_frame_len <= 0:
            raise ValueError("sampling.params.fixed_frame_len must be a positive integer")
        if not isinstance(context_frame_len, int) or context_frame_len < 0:
            raise ValueError("sampling.params.context_frame_len must be a non-negative integer")
        return fixed_frame_len, context_frame_len

    def _sample_images(
        self,
        image_list: list[dict[str, Any]],
        timestamps: list[dict[str, str]],
        start_index: int,
        end_index: int,
        fixed_frame_len: int,
        context_frame_len: int,
        topic_key: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """Sample the event plus context frames before and after without changing it."""

        before = range(max(0, start_index - context_frame_len), start_index)
        event = self._sample_indexes(
            start_index, end_index, min(fixed_frame_len, MAX_EVENT_FRAME_LEN)
        )
        after = range(end_index + 1, min(len(image_list), end_index + 1 + context_frame_len))
        indexed_roles = (
            [(index, "context_before") for index in before]
            + [(index, "event") for index in event]
            + [(index, "context_after") for index in after]
        )
        samples: dict[str, list[dict[str, Any]]] = {}
        for frame_index, sample_role in indexed_roles:
            image = image_list[frame_index].get(topic_key)
            if image is None:
                raise ValueError(f"sample image topic is missing: {topic_key}")
            raw = image.get("raw")
            if raw is None:
                raise ValueError("sample image is missing raw bytes")
            samples.setdefault(topic_key, []).append(
                {
                    "frame_index": frame_index,
                    "sample_role": sample_role,
                    "image_base64": base64.b64encode(raw).decode("ascii"),
                    "encoding": image.get("encoding", ""),
                    "source_topic": image.get("source_topic", ""),
                    "format": image.get("format", "jpeg"),
                    "timestamp_ns": timestamps[frame_index]["timestamp_ns"],
                    "timestamp_sec": timestamps[frame_index]["timestamp_sec"],
                }
            )
        if not samples:
            raise ValueError("no image samples were generated")
        return samples

    def _sample_indexes(self, start_index: int, end_index: int, fixed_frame_len: int) -> list[int]:
        """Return deterministic frame indexes for fixed-sequence sampling."""

        count = end_index - start_index + 1
        if count <= fixed_frame_len:
            return list(range(start_index, end_index + 1))
        if fixed_frame_len == 1:
            return [start_index]
        return [
            start_index + round(i * (count - 1) / (fixed_frame_len - 1))
            for i in range(fixed_frame_len)
        ]

    def _format_annotation(
        self,
        period: dict[str, Any],
        samples: dict[str, list[dict[str, Any]]],
        vlm_result: dict[str, Any],
        output: dict[str, Any],
        baseline_camera_key: str,
    ) -> dict[str, Any]:
        """Map one VLM result and period into the external annotation schema."""

        if not baseline_camera_key or baseline_camera_key not in samples:
            raise ValueError("baseline_camera_key must identify the sampled event camera")
        action_state = int(vlm_result["action_state"])
        if action_state not in {-1, 0, 1}:
            raise ValueError("VLM action_state must be -1, 0, or 1")
        if period["start_index"] == period["end_index"]:
            # A single image cannot establish motion completion or manipulation success.
            action_state = 0
        segment_id = max(int(time.time() * 1000), self._last_segment_id + 1)
        self._last_segment_id = segment_id
        internal_fields = {"start_index", "end_index", "start_event_order_id", "end_event_order_id"}
        annotation = {key: value for key, value in period.items() if key not in internal_fields}
        annotation.update(
            {
                "id": f"seg_{segment_id}",
                "prompt": str(vlm_result["action_summary"]),
                "layerId": output.get("layerId", "l2"),
                "category": output.get("category", "detail"),
                "attributes": output.get("attributes", {"scene": "tabletop", "sceneTags": []}),
                "description": str(vlm_result["detailed_description"]),
                "baseline_camera_key": baseline_camera_key,
                "action_state": action_state,
            }
        )
        return annotation

    def _ns_to_sec(self, timestamp_ns: int) -> str:
        """Format an integer nanosecond value as a 9-decimal seconds string."""

        return f"{timestamp_ns // 1_000_000_000}.{timestamp_ns % 1_000_000_000:09d}"
