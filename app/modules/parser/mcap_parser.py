"""MCAP parser for local robot logs used by the MVP pipeline."""

from __future__ import annotations

from bisect import bisect_left
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from mcap.reader import make_reader
from mcap_ros2.decoder import DecoderFactory

from app.core.config import RobotConfig, TopicConfig
from app.core.defaults import MULTI_MCAP_POLICY


class McapParser:
    """Parse configured MCAP topics and align them to the main time topic."""

    def parse(self, request: dict[str, Any]) -> dict[str, Any]:
        """Read MCAP messages and return aligned parser output lists."""

        parser_cfg = request.get("parser", {})
        if parser_cfg.get("file_type") != "mcap":
            raise ValueError("MVP parser only supports mcap file_type")
        configured_paths = parser_cfg.get("mcap_paths") or [parser_cfg.get("mcap_path")]
        mcap_paths = [Path(path) for path in configured_paths if path]
        if not mcap_paths:
            raise ValueError("at least one MCAP path is required")
        for mcap_path in mcap_paths:
            if not mcap_path.exists():
                raise FileNotFoundError(mcap_path)
        robot_config = parser_cfg["robot_config"]
        if not isinstance(robot_config, RobotConfig):
            raise ValueError("parser.robot_config must be a RobotConfig")
        max_frames = parser_cfg.get("max_frames")
        max_tor_time_sec = float(request.get("insert", {}).get("max_tor_time_sec", 0.2))
        if max_tor_time_sec < 0:
            raise ValueError("insert.max_tor_time_sec must not be negative")
        max_tor_time_ns = int(round(max_tor_time_sec * 1_000_000_000))

        if len(mcap_paths) > 1:
            return self._parse_multiple(mcap_paths, robot_config, max_frames, max_tor_time_ns)
        return self._parse_one(mcap_paths[0], robot_config, max_frames, max_tor_time_ns)

    def _parse_one(
        self, mcap_path: Path, robot_config: RobotConfig, max_frames: int | None,
        max_tor_time_ns: int,
    ) -> dict[str, Any]:
        """Parse one file using its main camera timestamps."""

        raw = self._read_topics(mcap_path, robot_config, max_frames=max_frames)
        main_records = raw[robot_config.main_time_topic]
        if max_frames is not None:
            main_records = main_records[: int(max_frames)]
        if not main_records:
            raise ValueError("main time topic has no messages")

        # Step 1: use main_time_topic as the canonical timeline.
        # Step 2: nearest-neighbor align every configured camera/state/action topic to each main frame.
        # Step 3: build parallel lists plus optional vector/schema views for downstream modules.
        timestamp_list = [self._timestamp_record(record["timestamp_ns"]) for record in main_records]
        image_list: list[dict[str, Any]] = []
        state_list: list[dict[str, np.ndarray]] = []
        action_list: list[dict[str, np.ndarray]] = []
        state_schema = self._schema(robot_config.observation_state)
        action_schema = self._schema(robot_config.action)
        camera_schema = [
            {"key": camera.name, "topic": camera.topic, "role": camera.role, "group": camera.group}
            for camera in robot_config.cameras
        ]

        for main_record in main_records:
            ts = int(main_record["timestamp_ns"])
            image_list.append(self._aligned_images(raw, robot_config.cameras, ts, max_tor_time_ns))
            state_list.append(self._aligned_values(raw, robot_config.observation_state, ts, max_tor_time_ns))
            action_list.append(self._aligned_values(raw, robot_config.action, ts, max_tor_time_ns))

        result = {
            "timestamp_list": timestamp_list,
            "image_list": image_list,
            "main_time_camera_key": self._main_time_camera_key(robot_config),
            "camera_schema": camera_schema,
            "state_list": state_list,
            "action_list": action_list,
            "state_vector_list": [self._vectorize(row, robot_config.observation_state) for row in state_list],
            "action_vector_list": [self._vectorize(row, robot_config.action) for row in action_list],
            "state_schema": state_schema,
            "action_schema": action_schema,
        }
        result["segment_manifest"] = [{
            "source_mcap": mcap_path.name,
            "start_timestamp_ns": timestamp_list[0]["timestamp_ns"],
            "end_timestamp_ns": timestamp_list[-1]["timestamp_ns"],
            "source_frame_count": len(timestamp_list),
        }]
        return result

    def scan_main_time_range(self, mcap_path: Path, main_time_topic: str) -> dict[str, Any]:
        """Read only the main topic envelope used to order and validate segments."""

        first: int | None = None
        last: int | None = None
        count = 0
        with mcap_path.open("rb") as stream:
            reader = make_reader(stream)
            for _schema, channel, message in reader.iter_messages(topics=[main_time_topic]):
                timestamp = int(message.log_time)
                first = timestamp if first is None else min(first, timestamp)
                last = timestamp if last is None else max(last, timestamp)
                count += 1
        if first is None or last is None:
            raise ValueError(f"main time topic has no messages: {mcap_path.name}")
        return {
            "path": mcap_path, "source_mcap": mcap_path.name,
            "start_timestamp_ns": first, "end_timestamp_ns": last,
            "source_frame_count": count,
        }

    def _parse_multiple(
        self, paths: list[Path], robot_config: RobotConfig, max_frames: int | None,
        max_tor_time_ns: int,
    ) -> dict[str, Any]:
        """Order files by recorded timestamps, resolve boundaries, and build one CFR dataset."""

        policy = MULTI_MCAP_POLICY
        if len(paths) > int(policy["max_segment_count"]):
            raise ValueError(f"MCAP segment count exceeds {policy['max_segment_count']}")
        scanned = [self.scan_main_time_range(path, robot_config.main_time_topic) for path in paths]
        # Upload order is only a deterministic tie breaker. Recorded main-camera
        # timestamps are the sole ordering key for different time ranges.
        upload_index = {path: index for index, path in enumerate(paths)}
        scanned.sort(key=lambda item: (
            int(item["start_timestamp_ns"]), int(item["end_timestamp_ns"]),
            upload_index[item["path"]],
        ))
        max_gap_ns = int(round(float(policy["max_video_fill_gap_sec"]) * 1e9))
        coverage_end = int(scanned[0]["end_timestamp_ns"])
        previous_name = str(scanned[0]["source_mcap"])
        for current in scanned[1:]:
            gap = int(current["start_timestamp_ns"]) - coverage_end
            if gap >= max_gap_ns:
                raise ValueError(
                    f"MCAP gap is too large: {previous_name} -> "
                    f"{current['source_mcap']}, gap_sec={gap / 1e9:.9f}"
                )
            if int(current["end_timestamp_ns"]) > coverage_end:
                coverage_end = int(current["end_timestamp_ns"])
                previous_name = str(current["source_mcap"])

        parsed_segments: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for metadata in scanned:
            parsed_segments.append((
                metadata,
                self._parse_one(metadata["path"], robot_config, max_frames, max_tor_time_ns),
            ))
        return self._stitch_segments(parsed_segments, policy)

    def _stitch_segments(
        self, segments: list[tuple[dict[str, Any], dict[str, Any]]], policy: dict[str, Any]
    ) -> dict[str, Any]:
        """Create a relative fixed-rate timeline with earlier-segment overlap precedence."""

        fps = int(policy["fps"])
        frame_period_ns = 1_000_000_000 / fps
        first_ns = int(segments[0][0]["start_timestamp_ns"])
        last_ns = max(int(metadata["end_timestamp_ns"]) for metadata, _parsed in segments)
        frame_count = int(round((last_ns - first_ns) / frame_period_ns)) + 1
        source_rows: list[tuple[int, int, dict[str, Any], int]] = []
        manifest: list[dict[str, Any]] = []
        accepted_until = -1
        for segment_index, (metadata, parsed) in enumerate(segments):
            timestamps = [int(item["timestamp_ns"]) for item in parsed["timestamp_list"]]
            accepted = [index for index, timestamp in enumerate(timestamps) if timestamp > accepted_until]
            dropped = len(timestamps) - len(accepted)
            if accepted:
                accepted_until = max(accepted_until, timestamps[accepted[-1]])
                for index in accepted:
                    source_rows.append((timestamps[index], segment_index, parsed, index))
            manifest.append({
                **{key: value for key, value in metadata.items() if key != "path"},
                "accepted_frame_count": len(accepted), "overlap_dropped_frame_count": dropped,
            })
        if not source_rows:
            raise ValueError("no frames remain after resolving MCAP overlap")
        source_rows.sort(key=lambda item: item[0])
        source_times = [item[0] for item in source_rows]
        timestamp_list: list[dict[str, str]] = []
        image_list: list[dict[str, Any]] = []
        state_list: list[dict[str, np.ndarray]] = []
        action_list: list[dict[str, np.ndarray]] = []
        frame_manifest: list[dict[str, Any]] = []
        for frame_index in range(frame_count):
            source_target_ns = first_ns + int(round(frame_index * frame_period_ns))
            position = bisect_left(source_times, source_target_ns)
            candidate_positions = [idx for idx in (position - 1, position) if 0 <= idx < len(source_rows)]
            nearest_position = min(candidate_positions, key=lambda idx: abs(source_times[idx] - source_target_ns))
            source_ns, segment_index, parsed, source_index = source_rows[nearest_position]
            relative_ns = int(round(frame_index * 1_000_000_000 / fps))
            image_filled = abs(source_ns - source_target_ns) > int(round(float(policy["continuous_gap_sec"]) * 1e9))
            timestamp_list.append(self._timestamp_record(relative_ns))
            image_list.append({
                key: {**value, "alignment_filled": image_filled}
                for key, value in parsed["image_list"][source_index].items()
            })
            state_list.append(self._interpolate_aligned_values(source_rows, position, source_target_ns, "state_list", policy))
            action_list.append(self._interpolate_aligned_values(source_rows, position, source_target_ns, "action_list", policy))
            frame_manifest.append({
                "global_frame_index": frame_index, "relative_timestamp_ns": str(relative_ns),
                "source_timestamp_ns": str(source_ns),
                "source_mcap": segments[segment_index][0]["source_mcap"],
                "source_frame_index": source_index,
                "image_filled": image_filled,
            })
        template = segments[0][1]
        return {
            "timestamp_list": timestamp_list, "image_list": image_list,
            "main_time_camera_key": template["main_time_camera_key"],
            "camera_schema": template["camera_schema"],
            "state_list": state_list, "action_list": action_list,
            "state_vector_list": [self._vectorize(row, self._configs_from_schema(template["state_schema"])) for row in state_list],
            "action_vector_list": [self._vectorize(row, self._configs_from_schema(template["action_schema"])) for row in action_list],
            "state_schema": template["state_schema"], "action_schema": template["action_schema"],
            "segment_manifest": manifest, "frame_manifest": frame_manifest,
        }

    def _interpolate_aligned_values(
        self, rows: list[tuple[int, int, dict[str, Any], int]], position: int,
        target_ns: int, list_name: str, policy: dict[str, Any],
    ) -> dict[str, np.ndarray]:
        """Use nearby aligned values, otherwise interpolate only across an allowed gap."""

        candidates = [idx for idx in (position - 1, position) if 0 <= idx < len(rows)]
        nearest_index = min(candidates, key=lambda idx: abs(rows[idx][0] - target_ns))
        nearest = rows[nearest_index]
        tolerance_ns = int(round(float(policy["continuous_gap_sec"]) * 1e9))
        if abs(nearest[0] - target_ns) <= tolerance_ns or position == 0 or position == len(rows):
            return {key: value.copy() for key, value in nearest[2][list_name][nearest[3]].items()}
        before, after = rows[position - 1], rows[position]
        gap_ns = after[0] - before[0]
        max_gap_ns = int(round(float(policy["max_motion_interpolation_gap_sec"]) * 1e9))
        if gap_ns > max_gap_ns:
            raise ValueError(f"motion interpolation gap is too large: {gap_ns / 1e9:.9f}s")
        ratio = (target_ns - before[0]) / gap_ns
        left = before[2][list_name][before[3]]
        right = after[2][list_name][after[3]]
        schema_name = "state_schema" if list_name == "state_list" else "action_schema"
        parsers = {str(item["key"]): str(item.get("parser", "")) for item in before[2][schema_name]}
        output: dict[str, np.ndarray] = {}
        for key in left:
            if parsers.get(key) == "pose7d" and left[key].size == 7:
                position_value = left[key][:3] + (right[key][:3] - left[key][:3]) * ratio
                quaternion = self._slerp(left[key][3:7], right[key][3:7], ratio)
                output[key] = np.concatenate([position_value, quaternion]).astype(np.float32)
            else:
                output[key] = (left[key] + (right[key] - left[key]) * ratio).astype(np.float32)
        return output

    def _configs_from_schema(self, schema: list[dict[str, Any]]) -> list[TopicConfig]:
        """Build the minimal config view needed by the existing vectorizer."""

        return [TopicConfig(
            name=str(item["key"]), topic=str(item["topic"]), role="",
            parser=str(item.get("parser", "")), dtype=str(item.get("dtype", "float32")),
            shape=list(item.get("shape", [])),
        ) for item in schema]

    def _main_time_camera_key(self, robot_config: RobotConfig) -> str:
        """Return the configured camera name whose topic drives the aligned timeline."""

        for camera in robot_config.cameras:
            if camera.topic == robot_config.main_time_topic:
                return camera.name
        raise ValueError("main_time_topic must match one configured camera topic")

    def _read_topics(self, mcap_path: Path, robot_config: RobotConfig, max_frames: int | None) -> dict[str, list[dict[str, Any]]]:
        """Read configured topics from MCAP until optional main-frame limit is reached."""

        topics = {item.topic for item in robot_config.cameras + robot_config.observation_state + robot_config.action}
        topics.add(robot_config.main_time_topic)
        by_topic: dict[str, list[dict[str, Any]]] = {topic: [] for topic in topics}
        main_count = 0
        with mcap_path.open("rb") as stream:
            reader = make_reader(stream, decoder_factories=[DecoderFactory()])
            for _schema, channel, message, decoded in reader.iter_decoded_messages(topics=list(topics)):
                topic = channel.topic
                if topic not in by_topic:
                    continue
                config = self._config_for_topic(robot_config, topic)
                by_topic[topic].append({
                    "timestamp_ns": int(message.log_time),
                    "decoded": self._normalize_message(decoded, config),
                })
                if topic == robot_config.main_time_topic:
                    main_count += 1
                if max_frames is not None and main_count >= int(max_frames) and self._has_required_seed_records(by_topic, robot_config):
                    break
        for topic, records in by_topic.items():
            records.sort(key=lambda item: int(item["timestamp_ns"]))
            timestamps = [int(item["timestamp_ns"]) for item in records]
            if len(timestamps) != len(set(timestamps)):
                raise ValueError(f"duplicate timestamp in topic: {topic}")
        self._validate_required_topics(by_topic, robot_config)
        return by_topic

    def _config_for_topic(self, robot_config: RobotConfig, topic: str) -> TopicConfig:
        """Return the first configured definition for a raw MCAP topic."""

        for item in robot_config.cameras + robot_config.observation_state + robot_config.action:
            if item.topic == topic:
                return item
        raise ValueError(f"topic is not configured: {topic}")

    def _normalize_message(self, decoded: Any, config: TopicConfig) -> Any:
        """Convert large image messages to compact JPEG records while retaining numeric messages."""

        if config.role != "image":
            return decoded
        height = int(getattr(decoded, "height", config.height))
        width = int(getattr(decoded, "width", config.width))
        encoding = str(getattr(decoded, "encoding", config.encoding)).lower()
        raw = bytes(decoded.data)
        channels = 1 if encoding in {"mono8", "8uc1"} else 3
        expected = height * width * channels
        if height <= 0 or width <= 0 or len(raw) < expected:
            return {
                "raw": raw, "format": "raw", "encoding": encoding,
                "width": width, "height": height,
                "decode_error": "invalid image dimensions or payload length",
            }
        array = (
            np.frombuffer(raw[:expected], dtype=np.uint8).reshape(height, width, channels)
            if channels > 1
            else np.frombuffer(raw[:expected], dtype=np.uint8).reshape(height, width)
        )
        if encoding in {"rgb8", "rgb"}:
            array = cv2.cvtColor(array, cv2.COLOR_RGB2BGR)
        elif encoding not in {"bgr8", "bgr", "mono8", "8uc1"}:
            return {
                "raw": raw, "format": "raw", "encoding": encoding,
                "width": width, "height": height,
                "decode_error": f"unsupported image encoding: {encoding}",
            }
        ok, encoded = cv2.imencode(".jpg", array, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise ValueError(f"failed to encode image topic as JPEG: {config.topic}")
        return {
            "raw": encoded.tobytes(), "format": "jpeg",
            "encoding": "bgr8" if channels == 3 else "mono8",
            "width": width, "height": height,
        }

    def _has_required_seed_records(self, by_topic: dict[str, list[dict[str, Any]]], robot_config: RobotConfig) -> bool:
        """Return whether every required configured topic has at least one decoded message."""

        for item in robot_config.cameras + robot_config.observation_state + robot_config.action:
            if item.required and not by_topic.get(item.topic):
                return False
        return True

    def _validate_required_topics(self, by_topic: dict[str, list[dict[str, Any]]], robot_config: RobotConfig) -> None:
        """Fail fast when a required configured topic has no MCAP messages."""

        for item in robot_config.cameras + robot_config.observation_state + robot_config.action:
            if item.required and not by_topic.get(item.topic):
                raise ValueError(f"required topic has no messages: {item.topic}")

    def _timestamp_record(self, timestamp_ns: int) -> dict[str, str]:
        """Build the public timestamp object with ns and seconds strings."""

        return {"timestamp_ns": str(timestamp_ns), "timestamp_sec": self._ns_to_sec(timestamp_ns)}

    def _aligned_images(
        self,
        raw: dict[str, list[dict[str, Any]]],
        configs: list[TopicConfig],
        timestamp_ns: int,
        max_tor_time_ns: int,
    ) -> dict[str, Any]:
        """Nearest-neighbor align image topics and preserve raw image bytes."""

        output: dict[str, Any] = {}
        for config in configs:
            record = self._nearest(raw.get(config.topic, []), timestamp_ns, max_tor_time_ns, config)
            decoded = record["decoded"]
            output[config.name] = {
                "raw": decoded["raw"],
                "format": decoded["format"],
                "encoding": decoded["encoding"],
                "width": decoded["width"],
                "height": decoded["height"],
                "array": None,
                "source_topic": config.topic,
            }
            if decoded.get("decode_error"):
                output[config.name]["decode_error"] = decoded["decode_error"]
        return output

    def _aligned_values(
        self,
        raw: dict[str, list[dict[str, Any]]],
        configs: list[TopicConfig],
        timestamp_ns: int,
        max_tor_time_ns: int,
    ) -> dict[str, np.ndarray]:
        """Nearest-neighbor align numeric state/action topics."""

        output: dict[str, np.ndarray] = {}
        for config in configs:
            records = raw.get(config.topic, [])
            if not records and config.missing_policy in {"zero", "previous"}:
                output[config.name] = np.zeros(config.shape, dtype=np.float32)
                continue
            output[config.name] = self._aligned_value(records, timestamp_ns, max_tor_time_ns, config)
        return output

    def _aligned_value(
        self,
        records: list[dict[str, Any]],
        timestamp_ns: int,
        max_tor_time_ns: int,
        config: TopicConfig,
    ) -> np.ndarray:
        """Use nearest data within tolerance, otherwise interpolate between surrounding records."""

        timestamps = [int(item["timestamp_ns"]) for item in records]
        pos = bisect_left(timestamps, timestamp_ns)
        candidates = [records[idx] for idx in (pos - 1, pos) if 0 <= idx < len(records)]
        nearest = min(candidates, key=lambda item: abs(int(item["timestamp_ns"]) - timestamp_ns))
        if abs(int(nearest["timestamp_ns"]) - timestamp_ns) <= max_tor_time_ns or pos == 0 or pos == len(records):
            return self._extract_value(nearest["decoded"], config)
        before, after = records[pos - 1], records[pos]
        start_ns, end_ns = int(before["timestamp_ns"]), int(after["timestamp_ns"])
        ratio = (timestamp_ns - start_ns) / (end_ns - start_ns)
        left = self._extract_value(before["decoded"], config)
        right = self._extract_value(after["decoded"], config)
        if config.parser == "pose7d" and left.size == 7:
            position = left[:3] + (right[:3] - left[:3]) * ratio
            quaternion = self._slerp(left[3:7], right[3:7], ratio)
            return np.concatenate([position, quaternion]).astype(np.float32)
        return (left + (right - left) * ratio).astype(np.float32)

    def _slerp(self, left: np.ndarray, right: np.ndarray, ratio: float) -> np.ndarray:
        """Interpolate normalized quaternions along their shortest spherical arc."""

        q0 = left.astype(np.float64) / max(float(np.linalg.norm(left)), 1e-12)
        q1 = right.astype(np.float64) / max(float(np.linalg.norm(right)), 1e-12)
        dot = float(np.dot(q0, q1))
        if dot < 0.0:
            q1, dot = -q1, -dot
        dot = float(np.clip(dot, -1.0, 1.0))
        if dot > 0.9995:
            result = q0 + ratio * (q1 - q0)
            return (result / np.linalg.norm(result)).astype(np.float32)
        theta = np.arccos(dot)
        result = (np.sin((1.0 - ratio) * theta) * q0 + np.sin(ratio * theta) * q1) / np.sin(theta)
        return result.astype(np.float32)

    def _nearest(
        self,
        records: list[dict[str, Any]],
        timestamp_ns: int,
        max_tor_time_ns: int,
        config: TopicConfig,
    ) -> dict[str, Any]:
        """Return the nearest record to timestamp_ns or a clear missing-topic error."""

        if not records:
            raise ValueError(f"topic has no messages: {config.topic}")
        timestamps = [int(item["timestamp_ns"]) for item in records]
        pos = bisect_left(timestamps, timestamp_ns)
        candidates = []
        if pos < len(records):
            candidates.append(records[pos])
        if pos > 0:
            candidates.append(records[pos - 1])
        nearest = min(candidates, key=lambda item: abs(int(item["timestamp_ns"]) - timestamp_ns))
        if abs(int(nearest["timestamp_ns"]) - timestamp_ns) > max_tor_time_ns and config.required:
            raise ValueError(f"required topic cannot align within max_tor_time_sec: {config.topic}")
        return nearest

    def _extract_value(self, decoded: Any, config: TopicConfig) -> np.ndarray:
        """Extract a configured numeric vector from one decoded ROS message."""

        values: list[float] = []
        for field in config.fields:
            values.append(float(self._read_field(decoded, field)))
        return np.asarray(values, dtype=np.float32)

    def _read_field(self, obj: Any, field_path: str) -> Any:
        """Read dotted attributes and simple list indexes from a decoded message."""

        current = obj
        for part in field_path.split("."):
            if "[" in part and part.endswith("]"):
                name, index_text = part[:-1].split("[", 1)
                current = getattr(current, name)[int(index_text)]
            else:
                current = getattr(current, part)
        return current

    def _schema(self, configs: list[TopicConfig]) -> list[dict[str, Any]]:
        """Build offset-based schema for vectorized state/action rows."""

        schema: list[dict[str, Any]] = []
        offset = 0
        for config in configs:
            width = int(np.prod(config.shape)) if config.shape else len(config.fields)
            schema.append(
                {
                    "key": config.name,
                    "topic": config.topic,
                    "role": config.role,
                    "parser": config.parser,
                    "fields": config.fields,
                    "group": config.group,
                    "offset": offset,
                    "shape": config.shape,
                    "dtype": config.dtype,
                }
            )
            offset += width
        return schema

    def _vectorize(self, row: dict[str, np.ndarray], configs: list[TopicConfig]) -> np.ndarray:
        """Concatenate configured row values in stable robot-config order."""

        values = [row[config.name].reshape(-1) for config in configs]
        if not values:
            return np.asarray([], dtype=np.float32)
        return np.concatenate(values).astype(np.float32)

    def _ns_to_sec(self, timestamp_ns: int) -> str:
        """Format an integer nanosecond timestamp as seconds with 9 decimals."""

        return f"{timestamp_ns // 1_000_000_000}.{timestamp_ns % 1_000_000_000:09d}"
