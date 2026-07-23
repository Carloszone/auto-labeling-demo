"""Convert DataCheck trigger points into topic-independent event timelines."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


class TriggerEventGenerator:
    """Validate nodes and pair adjacent nodes independently for every topic."""

    def generate(self, request: dict[str, Any]) -> dict[str, Any]:
        """Return event points and N-1 adjacent periods grouped by topic key."""

        basic = request.get("basic")
        point_policy = request.get("point_policy")
        pairing_policy = request.get("pairing_policy")
        if not all(isinstance(item, dict) for item in (basic, point_policy, pairing_policy)):
            raise ValueError("request must include basic, point_policy, and pairing_policy")
        if not basic.get("task_id"):
            raise ValueError("basic.task_id is required")
        check_info = basic.get("check_info")
        parser_info = basic.get("parser_info")
        if not isinstance(check_info, dict) or not isinstance(parser_info, dict):
            raise ValueError("basic.check_info and basic.parser_info are required")
        triggers = check_info.get("trigger_points")
        timestamps = parser_info.get("timestamp_list")
        if not isinstance(triggers, list) or not isinstance(timestamps, list) or not timestamps:
            raise ValueError("trigger_points must be a list and timestamp_list must be non-empty")
        if point_policy.get("mode") != "pass_through":
            raise ValueError("point_policy.mode must be pass_through")
        if pairing_policy.get("mode") != "adjacent_by_topic":
            raise ValueError("pairing_policy.mode must be adjacent_by_topic")

        timeline = [int(item["timestamp_ns"]) for item in timestamps]
        if any(left >= right for left, right in zip(timeline, timeline[1:])):
            raise ValueError("timestamp_list must be strictly monotonic increasing")
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        seen: set[tuple[str, str, int]] = set()
        for source_index, trigger in enumerate(triggers):
            self._validate_trigger(trigger, timestamps)
            camera_topic_key = str(trigger["topic_key"])
            trigger_topic_key = self._trigger_topic_key(trigger)
            identity = (camera_topic_key, trigger_topic_key, int(trigger["timestamp_index"]))
            if identity in seen:
                continue
            seen.add(identity)
            grouped[(camera_topic_key, trigger_topic_key)].append(
                {**trigger, "source_trigger_index": source_index}
            )

        event_points: dict[str, dict[int, dict[str, Any]]] = {}
        event_periods: dict[str, list[dict[str, Any]]] = {}
        for camera_topic_key, trigger_topic_key in sorted(grouped):
            points = sorted(
                grouped[(camera_topic_key, trigger_topic_key)],
                key=lambda item: int(item["timestamp_ns"]),
            )
            ordered = {order: point for order, point in enumerate(points, start=1)}
            group_key = (
                camera_topic_key
                if trigger_topic_key == camera_topic_key
                else f"{trigger_topic_key}::{camera_topic_key}"
            )
            event_points[group_key] = ordered
            event_periods[group_key] = self._pair_adjacent(
                ordered, timestamps, camera_topic_key, trigger_topic_key
            )
        return {
            "task_id": basic["task_id"],
            "job_id": basic.get("job_id"),
            "event_points": event_points,
            "event_periods": event_periods,
        }

    def _trigger_topic_key(self, trigger: dict[str, Any]) -> str:
        """Return the gripper topic key that actually produced the trigger."""

        evidence = trigger.get("evidence")
        if isinstance(evidence, dict) and evidence.get("detection_topic_key"):
            return str(evidence["detection_topic_key"])
        return str(trigger["topic_key"])

    def _validate_trigger(self, trigger: Any, timestamps: list[dict[str, str]]) -> None:
        """Validate one unlabeled trigger against the aligned main timeline."""

        if not isinstance(trigger, dict):
            raise ValueError("trigger point must be a dictionary")
        required = {"topic_key", "source_topic", "timestamp_ns", "timestamp_sec", "timestamp_index"}
        missing = sorted(required - set(trigger))
        if missing:
            raise ValueError(f"trigger point missing fields: {missing}")
        index = int(trigger["timestamp_index"])
        if index < 0 or index >= len(timestamps):
            raise ValueError("trigger timestamp_index is out of range")
        if int(trigger["timestamp_ns"]) != int(timestamps[index]["timestamp_ns"]):
            raise ValueError("trigger timestamp does not match timestamp_list")

    def _pair_adjacent(
        self,
        event_points: dict[int, dict[str, Any]],
        timestamps: list[dict[str, str]],
        topic_key: str,
        trigger_topic_key: str,
    ) -> list[dict[str, Any]]:
        """Create one period for every adjacent node pair in a single topic timeline."""

        ordered = list(event_points.items())
        periods: list[dict[str, Any]] = []
        for (start_order, start), (end_order, end) in zip(ordered, ordered[1:]):
            start_index = int(start["timestamp_index"])
            end_node_index = int(end["timestamp_index"])
            end_index = end_node_index if end_node_index == len(timestamps) - 1 else end_node_index - 1
            periods.append(
                self._period(
                    timestamps,
                    start_index,
                    end_index,
                    start_order,
                    end_order,
                    topic_key,
                    str(start["source_topic"]),
                    trigger_topic_key,
                    str(start.get("evidence", {}).get("detection_source_topic", start["source_topic"])),
                )
            )
        return periods

    def _period(
        self,
        timestamps: list[dict[str, str]],
        start_index: int,
        end_index: int,
        start_order: int,
        end_order: int,
        topic_key: str,
        source_topic: str,
        trigger_topic_key: str,
        trigger_source_topic: str,
    ) -> dict[str, Any]:
        """Create public time fields and retain the owning camera topic."""

        start_ns = int(timestamps[start_index]["timestamp_ns"])
        end_ns = int(timestamps[end_index]["timestamp_ns"])
        origin_ns = int(timestamps[0]["timestamp_ns"])
        start_rel_ns, end_rel_ns = start_ns - origin_ns, end_ns - origin_ns
        return {
            "start_event_order_id": start_order,
            "end_event_order_id": end_order,
            "topic_key": topic_key,
            "source_topic": source_topic,
            "trigger_topic_key": trigger_topic_key,
            "trigger_source_topic": trigger_source_topic,
            "start_index": start_index,
            "end_index": end_index,
            "startSec": f"{start_rel_ns / 1_000_000_000:.3f}",
            "start_time": self._ns_to_sec(start_ns),
            "startTimeNs": str(start_ns),
            "startTimestampNs": str(start_ns),
            "episodeStartTimeNs": str(start_rel_ns),
            "episode_start_time": self._ns_to_sec(start_rel_ns),
            "timeline_start_sec": self._ns_to_sec(start_rel_ns),
            "endSec": f"{end_rel_ns / 1_000_000_000:.3f}",
            "end_time": self._ns_to_sec(end_ns),
            "endTimeNs": str(end_ns),
            "endTimestampNs": str(end_ns),
            "episodeEndTimeNs": str(end_rel_ns),
            "episode_end_time": self._ns_to_sec(end_rel_ns),
            "timeline_end_sec": self._ns_to_sec(end_rel_ns),
        }

    def _ns_to_sec(self, timestamp_ns: int) -> str:
        return f"{timestamp_ns // 1_000_000_000}.{timestamp_ns % 1_000_000_000:09d}"


AnomalyGenerator = TriggerEventGenerator
