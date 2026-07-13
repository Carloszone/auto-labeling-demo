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


class McapParser:
    """Parse configured MCAP topics and align them to the main time topic."""

    def parse(self, request: dict[str, Any]) -> dict[str, Any]:
        """Read MCAP messages and return aligned parser output lists."""

        parser_cfg = request.get("parser", {})
        if parser_cfg.get("file_type") != "mcap":
            raise ValueError("MVP parser only supports mcap file_type")
        mcap_path = Path(parser_cfg["mcap_path"])
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

        return {
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
