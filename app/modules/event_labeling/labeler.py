"""Event range generation, image sampling, and VLM result formatting."""

from __future__ import annotations

import base64
import logging
import time
import urllib.error
from typing import Any, Callable, Protocol

import cv2

from app.core.video_frames import read_video_frames


LOGGER = logging.getLogger(__name__)
MAX_EVENT_FRAME_LEN = 20


class VlmClientProtocol(Protocol):
    """Protocol implemented by VLM clients used by EventLabeler."""

    def label(
        self,
        *,
        model: str,
        system_prompt: str,
        input_prompt: str,
        samples: dict[str, list[dict[str, Any]]],
        store: bool = False,
        reasoning: str = "off",
        temperature: float = 0.7,
        max_output_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Return one structured VLM label for sampled event frames."""


class EventLabeler:
    """Consume event periods, sample images, call VLM, and format annotations."""

    def __init__(
        self,
        vlm_client: VlmClientProtocol,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> None:
        """Store the injected VLM client so tests can avoid network calls."""

        self._vlm_client = vlm_client
        self._progress_callback = progress_callback
        self._last_segment_id = 0

    def label(self, request: dict[str, Any]) -> dict[str, Any]:
        """Run the event labeling flow for one parser/generation result pair."""

        basic = request.get("basic")
        sampling = request.get("sampling")
        vlm_params = request.get("vlm_params")
        if not isinstance(basic, dict) or not isinstance(sampling, dict) or not isinstance(vlm_params, dict):
            raise ValueError("request must include basic, sampling, and vlm_params")
        task_id = basic.get("task_id")
        job_id = basic.get("job_id")
        if not task_id:
            raise ValueError("basic.task_id is required")
        parser_info = basic.get("parser_info")
        generation_info = basic.get("generation_info")
        if not isinstance(parser_info, dict) or not isinstance(generation_info, dict):
            raise ValueError("parser_info and generation_info are required")
        if not isinstance(parser_info.get("timestamp_list"), list) or not parser_info["timestamp_list"]:
            raise ValueError("parser_info.timestamp_list must be a non-empty list")
        has_images = isinstance(parser_info.get("image_list"), list)
        has_videos = isinstance(parser_info.get("video_paths"), dict) and bool(parser_info["video_paths"])
        if not has_images and not has_videos:
            raise ValueError("parser_info requires image_list or video_paths")
        if has_images and len(parser_info["image_list"]) != len(parser_info["timestamp_list"]):
            raise ValueError("parser_info.image_list must align with timestamp_list")
        if not vlm_params.get("model"):
            raise ValueError("vlm_params.model is required")
        store = vlm_params.get("store", False)
        reasoning = str(vlm_params.get("reasoning", "off"))
        temperature = float(vlm_params.get("temperature", 0.7))
        max_output_tokens = int(vlm_params.get("max_output_tokens", 1024))
        if not isinstance(store, bool):
            raise ValueError("vlm_params.store must be a boolean")
        if reasoning not in {"off", "on"}:
            raise ValueError("vlm_params.reasoning must be off or on")
        if not 0 <= temperature <= 2:
            raise ValueError("vlm_params.temperature must be between 0 and 2")
        if max_output_tokens <= 0:
            raise ValueError("vlm_params.max_output_tokens must be positive")

        event_periods = generation_info.get("event_periods")
        if not isinstance(event_periods, dict):
            raise ValueError("generation_info.event_periods must be grouped by topic")
        if not event_periods:
            return {"task_id": task_id, "job_id": basic.get("job_id"), "response": []}

        fixed_frame_len, sampling_frame_gap, context_frame_len = self._sampling_lengths(sampling)
        flattened = [period for topic_periods in event_periods.values() for period in topic_periods]
        flattened.sort(key=lambda period: (int(period["start_index"]), str(period["topic_key"])))
        periods = self._validated_periods(parser_info["timestamp_list"], flattened)
        output = request.get("output", {}) if isinstance(request.get("output", {}), dict) else {}
        response = []

        # Step 1: validate event periods generated upstream.
        # Step 2: sample each period from parser image frames.
        # Step 3: call VLM for each sampled period and map the response to annotation JSON.
        for completed, period in enumerate(periods, start=1):
            topic_key = str(period.get("topic_key", ""))
            if not topic_key:
                raise ValueError("event period topic_key is required")
            sample_source = parser_info.get("image_list") if has_images else parser_info["video_paths"]
            samples = self._sample_images(
                sample_source,
                parser_info["timestamp_list"],
                period["start_index"],
                period["end_index"],
                fixed_frame_len,
                sampling_frame_gap,
                context_frame_len,
                topic_key,
                context={
                    "task_id": task_id,
                    "job_id": job_id,
                    "period": period,
                    "camera_schema": parser_info.get("camera_schema", []),
                    "state_schema": parser_info.get("state_schema", []),
                    "action_schema": parser_info.get("action_schema", []),
                    "segment_manifest": parser_info.get("segment_manifest", []),
                    "frame_manifest": parser_info.get("frame_manifest", []),
                    "main_time_camera_key": parser_info.get("main_time_camera_key"),
                },
            )
            topic_samples = samples[topic_key]
            sample_manifest = [
                {
                    "frame_index": item["frame_index"],
                    "sample_role": item["sample_role"],
                    "timestamp_ns": item["timestamp_ns"],
                    "timestamp_sec": item["timestamp_sec"],
                }
                for item in topic_samples
            ]
            LOGGER.info(
                "VLM sampling task_id=%s job_id=%s topic_key=%s start_index=%s end_index=%s "
                "event_source_frames=%s event_sample_frames=%s context_before_frames=%s "
                "context_after_frames=%s requested_sampling_frame_gap=%s "
                "effective_sampling_frame_gap=%s max_event_frames=%s",
                task_id,
                job_id,
                topic_key,
                period["start_index"],
                period["end_index"],
                int(period["end_index"]) - int(period["start_index"]) + 1,
                sum(item["sample_role"] == "event" for item in topic_samples),
                sum(item["sample_role"] == "context_before" for item in topic_samples),
                sum(item["sample_role"] == "context_after" for item in topic_samples),
                sampling_frame_gap,
                self._effective_sampling_gap(
                    int(period["end_index"]) - int(period["start_index"]) + 1,
                    sampling_frame_gap,
                    min(fixed_frame_len, MAX_EVENT_FRAME_LEN),
                ),
                MAX_EVENT_FRAME_LEN,
            )
            LOGGER.info(
                "vlm_input task_id=%s job_id=%s topic_key=%s start_index=%s end_index=%s model=%s system_prompt=%r input_prompt=%r sample_manifest=%s",
                task_id, job_id, topic_key, period["start_index"], period["end_index"],
                vlm_params["model"], vlm_params.get("system_prompt", ""),
                vlm_params.get("input_prompt", ""), sample_manifest,
            )
            vlm_started = time.perf_counter()
            try:
                vlm_result = self._vlm_client.label(
                    model=str(vlm_params["model"]),
                    system_prompt=str(vlm_params.get("system_prompt", "")),
                    input_prompt=str(vlm_params.get("input_prompt", "")),
                    samples=samples,
                    store=store,
                    reasoning=reasoning,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                )
            except Exception as exc:
                LOGGER.exception(
                    "vlm_call_failed task_id=%s job_id=%s topic_key=%s start_index=%s end_index=%s duration_sec=%.6f",
                    task_id, job_id, topic_key, period["start_index"], period["end_index"],
                    time.perf_counter() - vlm_started,
                )
                vlm_result = {
                    "action_summary": self._vlm_error_summary(exc),
                    "action_state": -1,
                    "detailed_description": self._vlm_error_detail(exc),
                }
            LOGGER.info(
                "vlm_output task_id=%s job_id=%s topic_key=%s start_index=%s end_index=%s duration_sec=%.6f result=%s",
                task_id, job_id, topic_key, period["start_index"], period["end_index"],
                time.perf_counter() - vlm_started, vlm_result,
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
            if self._progress_callback is not None:
                self._progress_callback(completed, len(periods))

        return {"task_id": task_id, "job_id": basic.get("job_id"), "response": response}

    def _vlm_error_summary(self, exc: Exception) -> str:
        """Return a concise failed annotation instead of aborting the batch."""

        if isinstance(exc, urllib.error.HTTPError):
            return f"VLM请求失败（HTTP {exc.code}）"
        return f"VLM请求失败（{type(exc).__name__}）"

    def _vlm_error_detail(self, exc: Exception) -> str:
        """Preserve useful VLM failure details in the generated annotation."""

        if isinstance(exc, urllib.error.HTTPError):
            body = str(getattr(exc, "vlm_error_body", ""))
            suffix = f"；服务端响应：{body}" if body else ""
            return f"VLM服务返回HTTP {exc.code} {exc.reason}{suffix}"[:4000]
        detail = f"{type(exc).__name__}: {exc}"
        raw_output = str(
            getattr(exc, "vlm_model_content", "")
            or getattr(exc, "vlm_raw_response", "")
        )
        if raw_output:
            detail += f"；模型原始返回：{raw_output}"
        return detail[:4000]

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

    def _sampling_lengths(self, sampling: dict[str, Any]) -> tuple[int, int, int]:
        """Extract the event cap, sampling gap, and per-side context length."""

        if sampling.get("mode") != "fixed_sequence":
            raise ValueError("only fixed_sequence sampling is supported")
        params = sampling.get("params", {})
        fixed_frame_len = params.get("fixed_frame_len")
        sampling_frame_gap = params.get("sampling_frame_gap", 20)
        context_frame_len = params.get("context_frame_len", 2)
        if not isinstance(fixed_frame_len, int) or fixed_frame_len <= 0:
            raise ValueError("sampling.params.fixed_frame_len must be a positive integer")
        if not isinstance(sampling_frame_gap, int) or sampling_frame_gap <= 0:
            raise ValueError("sampling.params.sampling_frame_gap must be a positive integer")
        if not isinstance(context_frame_len, int) or context_frame_len < 0:
            raise ValueError("sampling.params.context_frame_len must be a non-negative integer")
        return fixed_frame_len, sampling_frame_gap, context_frame_len

    def _sample_images(
        self,
        image_list: list[dict[str, Any]] | dict[str, str],
        timestamps: list[dict[str, str]],
        start_index: int,
        end_index: int,
        fixed_frame_len: int,
        sampling_frame_gap: int,
        context_frame_len: int,
        topic_key: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Sample the event plus context frames before and after without changing it."""

        before = range(max(0, start_index - context_frame_len), start_index)
        event = self._sample_indexes(
            start_index,
            end_index,
            min(fixed_frame_len, MAX_EVENT_FRAME_LEN),
            sampling_frame_gap,
        )
        after = range(end_index + 1, min(len(timestamps), end_index + 1 + context_frame_len))
        indexed_roles = (
            [(index, "context_before") for index in before]
            + [(index, "event") for index in event]
            + [(index, "context_after") for index in after]
        )
        ordered_indexes = [index for index, _role in indexed_roles]
        if ordered_indexes != sorted(ordered_indexes) or len(ordered_indexes) != len(set(ordered_indexes)):
            raise ValueError("sample images must be unique and ordered chronologically")
        samples: dict[str, list[dict[str, Any]]] = {}
        if isinstance(image_list, dict):
            video_path = image_list.get(topic_key)
            if not video_path:
                raise ValueError(f"sample video topic is missing: {topic_key}")
            decoded = read_video_frames(video_path, ordered_indexes)
            for frame_index, sample_role in indexed_roles:
                frame = decoded[frame_index]
                ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
                if not ok:
                    raise ValueError(f"failed to encode sampled video frame: {frame_index}")
                samples.setdefault(topic_key, []).append({
                    "frame_index": frame_index,
                    "sample_role": sample_role,
                    "image_base64": base64.b64encode(encoded.tobytes()).decode("ascii"),
                    "encoding": "bgr8",
                    "source_topic": next((
                        str(item.get("topic", "")) for item in (context or {}).get("camera_schema", [])
                        if item.get("key") == topic_key
                    ), ""),
                    "format": "jpeg",
                    "timestamp_ns": timestamps[frame_index]["timestamp_ns"],
                    "timestamp_sec": timestamps[frame_index]["timestamp_sec"],
                })
            return samples
        for frame_index, sample_role in indexed_roles:
            frame_images = image_list[frame_index]
            image = frame_images.get(topic_key)
            if image is None:
                context = context or {}
                frame_manifest = context.get("frame_manifest")
                frame_source = None
                if isinstance(frame_manifest, list) and 0 <= frame_index < len(frame_manifest):
                    frame_source = frame_manifest[frame_index]
                available_image_keys = sorted(str(key) for key in frame_images.keys())
                camera_keys = [
                    str(item.get("key", ""))
                    for item in context.get("camera_schema", [])
                    if isinstance(item, dict)
                ]
                state_keys = [
                    str(item.get("key", ""))
                    for item in context.get("state_schema", [])
                    if isinstance(item, dict)
                ]
                action_keys = [
                    str(item.get("key", ""))
                    for item in context.get("action_schema", [])
                    if isinstance(item, dict)
                ]
                LOGGER.error(
                    "sample_image_topic_missing task_id=%s job_id=%s topic_key=%s "
                    "frame_index=%s sample_role=%s start_index=%s end_index=%s "
                    "timestamp=%s frame_source=%s available_image_keys=%s "
                    "camera_keys=%s state_keys=%s action_keys=%s main_time_camera_key=%s "
                    "segment_manifest=%s period=%s",
                    context.get("task_id"),
                    context.get("job_id"),
                    topic_key,
                    frame_index,
                    sample_role,
                    start_index,
                    end_index,
                    timestamps[frame_index] if 0 <= frame_index < len(timestamps) else None,
                    frame_source,
                    available_image_keys,
                    camera_keys,
                    state_keys,
                    action_keys,
                    context.get("main_time_camera_key"),
                    context.get("segment_manifest"),
                    context.get("period"),
                )
                raise ValueError(
                    "sample image topic is missing: "
                    f"{topic_key}; frame_index={frame_index}; sample_role={sample_role}; "
                    f"available_image_keys={available_image_keys}; camera_keys={camera_keys}; "
                    f"state_keys={state_keys}; action_keys={action_keys}; frame_source={frame_source}"
                )
            raw = image.get("raw")
            if raw is None:
                context = context or {}
                frame_manifest = context.get("frame_manifest")
                frame_source = None
                if isinstance(frame_manifest, list) and 0 <= frame_index < len(frame_manifest):
                    frame_source = frame_manifest[frame_index]
                LOGGER.error(
                    "sample_image_raw_missing task_id=%s job_id=%s topic_key=%s frame_index=%s "
                    "sample_role=%s image_keys=%s timestamp=%s frame_source=%s",
                    context.get("task_id"),
                    context.get("job_id"),
                    topic_key,
                    frame_index,
                    sample_role,
                    sorted(str(key) for key in image.keys()),
                    timestamps[frame_index] if 0 <= frame_index < len(timestamps) else None,
                    frame_source,
                )
                raise ValueError(
                    "sample image is missing raw bytes: "
                    f"topic_key={topic_key}; frame_index={frame_index}; "
                    f"sample_role={sample_role}; frame_source={frame_source}; "
                    f"image_keys={sorted(str(key) for key in image.keys())}"
                )
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

    def _sample_indexes(
        self,
        start_index: int,
        end_index: int,
        fixed_frame_len: int,
        sampling_frame_gap: int,
    ) -> list[int]:
        """Sample by the requested frame gap, enlarging it when the cap would be exceeded."""

        count = end_index - start_index + 1
        effective_gap = self._effective_sampling_gap(
            count, sampling_frame_gap, fixed_frame_len
        )
        return list(range(start_index, end_index + 1, effective_gap))

    @staticmethod
    def _effective_sampling_gap(
        frame_count: int, sampling_frame_gap: int, fixed_frame_len: int
    ) -> int:
        """Return the requested gap or the minimum larger gap required by the image cap."""

        requested_count = (frame_count + sampling_frame_gap - 1) // sampling_frame_gap
        if requested_count <= fixed_frame_len:
            return sampling_frame_gap
        return (frame_count + fixed_frame_len - 1) // fixed_frame_len

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
                "review_status": "pending",
            }
        )
        return annotation

    def _ns_to_sec(self, timestamp_ns: int) -> str:
        """Format an integer nanosecond value as a 9-decimal seconds string."""

        return f"{timestamp_ns // 1_000_000_000}.{timestamp_ns % 1_000_000_000:09d}"
