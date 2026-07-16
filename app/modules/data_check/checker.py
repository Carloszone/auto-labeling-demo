"""Robot-series and image quality checks for aligned parser output."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import cv2
import numpy as np
from scipy.signal import savgol_filter

from app.modules.data_check.trigger_detector import EndEffectorTriggerDetector


ANOMALIES = {
    "spike": (1001, "数据存在瞬时突变"),
    "step": (1002, "数据存在台阶跳变"),
    "oscillation": (1003, "数据存在振荡"),
    "end_zero": (1004, "末端执行器处于全零速"),
    "end_step": (1005, "末端执行器动作出现台阶跳变"),
    "black": (2001, "图像存在黑帧"),
    "blur": (2002, "图像存在模糊帧"),
    "corrupted": (2003, "图像存在损坏帧"),
    "still": (2004, "图像存在静止帧"),
}


class DataChecker:
    """Validate aligned data, run enabled MVP detectors, and fuse anomaly ranges."""

    def check(self, request: dict[str, Any]) -> dict[str, Any]:
        """Run robot and image detectors and return frame details plus fused ranges."""

        basic = request.get("basic")
        if not isinstance(basic, dict) or not basic.get("task_id"):
            raise ValueError("basic.task_id is required")
        parser_info = basic.get("parser_info")
        if not isinstance(parser_info, dict):
            raise ValueError("basic.parser_info is required")
        self._validate_parser_info(parser_info)
        data_config = request.get("data_detection")
        image_config = request.get("image_detection")
        trigger_config = request.get("trigger_detection")
        merge_config = request.get("merge_policy")
        if not all(isinstance(item, dict) for item in (data_config, image_config, trigger_config, merge_config)):
            raise ValueError("data_detection, image_detection, trigger_detection, and merge_policy are required")
        self._validate_config(data_config, image_config, merge_config)
        fps = float(basic.get("fps", 30))
        if fps <= 0:
            raise ValueError("basic.fps must be positive")
        smooth = basic.get("smooth", {"method": "savgol", "window_frame_length": 10, "polyorder": 3})
        if not isinstance(smooth, dict):
            raise ValueError("basic.smooth must be a dictionary")

        data_detail: dict[int, list[dict[str, Any]]] = {}
        image_detail: dict[int, list[dict[str, Any]]] = {}

        # 1. Derive motion series and detect quality anomalies independently from triggers.
        # 2. Decode aligned camera frames and detect black, blur, corrupted, and still images.
        # 3. Combine frame results, then merge same-type nearby frames into stable anomaly ranges.
        sudden = data_config.get("sudden_change_config", {})
        extreme = data_config.get("extreme_value_config", {})
        if sudden.get("enable", False) or extreme.get("enable", False):
            metrics = self._motion_metrics(parser_info, smooth)
            if not metrics:
                raise ValueError("enabled data detection requires at least one pose{x}d state topic")
            if sudden.get("enable", False):
                self._detect_sudden(metrics, sudden, fps, float(basic.get("eps", 1e-9)), data_detail)
            if extreme.get("enable", False):
                self._detect_extremes(metrics, extreme, data_detail)
                self._detect_zero_speed(metrics, extreme, data_detail)
        if image_config.get("enable", False):
            self._detect_images(parser_info["image_list"], image_config, fps, float(basic.get("eps", 1e-9)), image_detail)

        all_detail = self._combine_details(data_detail, image_detail)
        timestamps = parser_info["timestamp_list"]
        min_frames = self._seconds_to_frames(merge_config.get("min_low_quality_time_sec", 1 / fps), fps)
        max_gap = self._seconds_to_frames(merge_config.get("max_gap_time_sec", 0), fps, allow_zero=True)
        trigger_points = EndEffectorTriggerDetector().detect(parser_info, smooth, trigger_config, fps)
        return {
            "check_list": [1 if index in all_detail else 0 for index in range(len(timestamps))],
            "check_detail": all_detail,
            "data_anomaly_ranges": self._merge_details(data_detail, timestamps, "data", min_frames, max_gap),
            "img_anomaly_ranges": self._merge_details(image_detail, timestamps, "image", min_frames, max_gap),
            "trigger_points": trigger_points,
        }

    def _validate_parser_info(self, parser_info: dict[str, Any]) -> None:
        """Validate parallel aligned lists, monotonic timestamps, and declared state keys."""

        required = {"timestamp_list", "image_list", "state_list", "action_list", "state_schema", "action_schema"}
        missing = sorted(required - set(parser_info))
        if missing:
            raise ValueError(f"parser_info missing fields: {missing}")
        length = len(parser_info["timestamp_list"])
        if length == 0:
            raise ValueError("timestamp_list must not be empty")
        for key in ("image_list", "state_list", "action_list"):
            if len(parser_info[key]) != length:
                raise ValueError(f"{key} length must match timestamp_list")
        timestamps = [int(item["timestamp_ns"]) for item in parser_info["timestamp_list"]]
        if any(left >= right for left, right in zip(timestamps, timestamps[1:])):
            raise ValueError("timestamp_list must be strictly monotonic increasing")
        for schema_key, rows_name in (("state_schema", "state_list"), ("action_schema", "action_list")):
            for schema in parser_info[schema_key]:
                key = schema.get("key")
                if any(key not in row for row in parser_info[rows_name]):
                    raise ValueError(f"{rows_name} is missing schema key: {key}")

    def _validate_config(self, data: dict[str, Any], image: dict[str, Any], merge: dict[str, Any]) -> None:
        """Reject invalid detector thresholds before any expensive processing."""

        sudden = data.get("sudden_change_config", {})
        extreme = data.get("extreme_value_config", {})
        for key in ("window_time_sec", "sudden_time_sec", "step_time_sec"):
            if sudden.get("enable", False) and float(sudden.get(key, 0)) <= 0:
                raise ValueError(f"sudden_change_config.{key} must be positive")
        if sudden.get("enable", False) and not 0 <= float(sudden.get("zcr_ratio", -1)) <= 1:
            raise ValueError("sudden_change_config.zcr_ratio must be in [0, 1]")
        if extreme.get("enable", False) and not 0 < float(extreme.get("degree", 0)) < 1:
            raise ValueError("extreme_value_config.degree must be in (0, 1)")
        for key in ("luminance", "lap_var", "pixel_mae", "moving_area_ratio"):
            if image.get("enable", False) and float(image.get(key, 0)) < 0:
                raise ValueError(f"image_detection.{key} must not be negative")
        if float(merge.get("min_low_quality_time_sec", -1)) < 0 or float(merge.get("max_gap_time_sec", -1)) < 0:
            raise ValueError("merge policy values must not be negative")

    def _motion_metrics(
        self, parser_info: dict[str, Any], smooth: dict[str, Any]
    ) -> dict[str, dict[str, np.ndarray]]:
        """Calculate smoothed position speed, acceleration, and jerk for every pose topic."""

        timestamps = np.asarray([int(item["timestamp_ns"]) for item in parser_info["timestamp_list"]], dtype=np.float64) / 1e9
        metrics: dict[str, dict[str, np.ndarray]] = {}
        for schema in parser_info["state_schema"]:
            parser = str(schema.get("parser", ""))
            if not (parser.startswith("pose") and parser.endswith("d")):
                continue
            key = str(schema["key"])
            positions = np.asarray([np.asarray(row[key], dtype=np.float64).reshape(-1)[:3] for row in parser_info["state_list"]])
            if smooth.get("method", "savgol") != "savgol":
                raise ValueError("basic.smooth.method must be savgol")
            window = min(int(smooth.get("window_frame_length", 10)), len(positions))
            polyorder = int(smooth.get("polyorder", 3))
            if window > polyorder:
                positions = np.column_stack(
                    [savgol_filter(positions[:, axis], window, polyorder) for axis in range(3)]
                )
            velocity_xyz = np.gradient(positions, timestamps, axis=0, edge_order=1)
            acceleration_xyz = np.gradient(velocity_xyz, timestamps, axis=0, edge_order=1)
            jerk_xyz = np.gradient(acceleration_xyz, timestamps, axis=0, edge_order=1)
            velocity = np.linalg.norm(velocity_xyz, axis=1)
            acceleration = np.linalg.norm(acceleration_xyz, axis=1)
            jerk = np.linalg.norm(jerk_xyz, axis=1)
            # Suppress derivative amplification when acceleration is only floating-point
            # cancellation relative to a very large constant velocity.
            if float(np.max(acceleration)) <= max(float(np.max(velocity)) * 1e-6, 1e-9):
                acceleration = np.zeros_like(acceleration)
                jerk = np.zeros_like(jerk)
            metrics[key] = {
                "velocity": velocity,
                "acceleration": acceleration,
                "jerk": jerk,
            }
        return metrics

    def _detect_sudden(self, metrics: dict[str, dict[str, np.ndarray]], config: dict[str, Any], fps: float, eps: float, detail: dict[int, list[dict[str, Any]]]) -> None:
        """Use trailing-window z-scores to classify spikes, steps, and oscillations."""

        window = self._seconds_to_frames(config["window_time_sec"], fps)
        threshold = float(config.get("z_score", 3))
        step_frame_len = self._seconds_to_frames(config["step_time_sec"], fps)
        zcr_ratio = float(config["zcr_ratio"])
        for topic, topic_metrics in metrics.items():
            for metric_name, values in topic_metrics.items():
                for index in range(window, len(values)):
                    history = values[index - window:index]
                    z_score = abs(float(values[index]) - float(np.mean(history))) / (float(np.std(history)) + eps)
                    if z_score <= threshold:
                        continue
                    if abs(float(values[index]) - float(np.mean(history))) <= 1e-6:
                        continue
                    signs = np.sign(np.diff(history))
                    crossings = int(np.count_nonzero(signs[1:] * signs[:-1] < 0))
                    if len(signs) > 1 and crossings / (len(signs) - 1) >= zcr_ratio:
                        name = "oscillation"
                    elif index + step_frame_len <= len(values) and np.all(np.abs(values[index:index + step_frame_len] - np.mean(history)) > threshold * (np.std(history) + eps)):
                        name = "step"
                    else:
                        name = "spike"
                    self._add_detail(detail, index, name, topic, f"{metric_name}: z={z_score:.3f}")

    def _detect_extremes(self, metrics: dict[str, dict[str, np.ndarray]], config: dict[str, Any], detail: dict[int, list[dict[str, Any]]]) -> None:
        """Flag derivative values outside expanded robust percentile bounds."""

        degree = min(float(config["degree"]), 1.0 - float(config["degree"]))
        expansion = float(config.get("expansion_coef", 0.2))
        minimum = float(config.get("min_tor", 1e-4))
        for topic, topic_metrics in metrics.items():
            for metric_name, values in topic_metrics.items():
                low, high = np.quantile(values, [degree, 1.0 - degree])
                margin = max(float(high - low) * expansion, minimum)
                for index in np.flatnonzero((values < low - margin) | (values > high + margin)):
                    self._add_detail(detail, int(index), "spike", topic, f"{metric_name}: extreme={values[index]:.6g}")

    def _detect_zero_speed(self, metrics: dict[str, dict[str, np.ndarray]], config: dict[str, Any], detail: dict[int, list[dict[str, Any]]]) -> None:
        """Mark frames where pose velocity, acceleration, and jerk are all near zero."""

        tolerance = float(config.get("min_tor", 1e-4))
        for topic, values in metrics.items():
            mask = (values["velocity"] <= tolerance) & (values["acceleration"] <= tolerance) & (values["jerk"] <= tolerance)
            for index in np.flatnonzero(mask):
                self._add_detail(detail, int(index), "end_zero", topic, "all derivatives near zero")

    def _detect_gripper_steps(self, parser_info: dict[str, Any], config: dict[str, Any], detail: dict[int, list[dict[str, Any]]]) -> None:
        """Detect gripper changes that are large relative to normal sensor noise."""

        minimum = float(config.get("min_tor", 1e-4))
        for schema in parser_info["state_schema"]:
            if schema.get("parser") != "gripper":
                continue
            key = str(schema["key"])
            values = np.asarray([float(np.asarray(row[key]).reshape(-1)[0]) for row in parser_info["state_list"]])
            differences = np.abs(np.diff(values))
            median = float(np.median(differences))
            threshold = max(minimum, median + 6.0 * float(np.median(np.abs(differences - median))))
            for index in np.flatnonzero(differences > threshold) + 1:
                self._add_detail(detail, int(index), "end_step", key, f"delta={differences[index - 1]:.6g}")

    def _detect_images(self, image_list: list[dict[str, Any]], config: dict[str, Any], fps: float, eps: float, detail: dict[int, list[dict[str, Any]]]) -> None:
        """Decode each camera timeline and detect black, blur, corrupted, and still frames."""

        camera_keys = sorted({key for row in image_list for key in row})
        if not camera_keys:
            raise ValueError("enabled image detection requires camera data")
        for camera_key in camera_keys:
            previous_valid: np.ndarray | None = None
            lap_history: list[float] = []
            for index, row in enumerate(image_list):
                image = row.get(camera_key)
                if image and image.get("alignment_filled"):
                    # A repeated frame represents a small MCAP boundary gap, not
                    # an observed still/blur condition. Keep the last genuine
                    # frame as comparison context and exclude the synthetic one.
                    continue
                frame = self._decode_image(image) if image else None
                if frame is None:
                    self._add_detail(detail, index, "corrupted", camera_key, str((image or {}).get("decode_error", "decode failed")))
                    previous_valid = None
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
                target_width = int(config.get("resize_length", gray.shape[1]))
                target_height = int(config.get("resize_width", gray.shape[0]))
                resized = cv2.resize(gray, (target_width, target_height), interpolation=cv2.INTER_AREA)
                has_primary_issue = False
                if float(np.mean(resized)) < float(config.get("luminance", 10)):
                    self._add_detail(detail, index, "black", camera_key, "mean luminance below threshold")
                    has_primary_issue = True
                lap = float(cv2.Laplacian(resized, cv2.CV_64F).var())
                blur = lap < float(config.get("lap_var", 150))
                window = self._seconds_to_frames(config.get("window_time_sec", 1), fps)
                if not blur and len(lap_history) >= window:
                    history = np.asarray(lap_history[-window:])
                    blur = (float(np.mean(history)) - lap) / (float(np.std(history)) + eps) > float(config.get("z_score", 2))
                lap_history.append(lap)
                if blur:
                    self._add_detail(detail, index, "blur", camera_key, f"laplacian_variance={lap:.3f}")
                    has_primary_issue = True
                if previous_valid is not None and not has_primary_issue:
                    previous_gray = cv2.cvtColor(previous_valid, cv2.COLOR_BGR2GRAY) if previous_valid.ndim == 3 else previous_valid
                    previous_gray = cv2.resize(previous_gray, (target_width, target_height), interpolation=cv2.INTER_AREA)
                    difference = cv2.absdiff(resized, previous_gray)
                    mae = float(np.mean(difference))
                    moving_ratio = float(np.count_nonzero(difference > max(float(config.get("pixel_mae", 5)), 1.0)) / difference.size)
                    if mae <= float(config.get("pixel_mae", 5)) and moving_ratio < float(config.get("moving_area_ratio", 0.05)):
                        self._add_detail(detail, index, "still", camera_key, f"mae={mae:.3f}, moving_ratio={moving_ratio:.4f}")
                previous_valid = None if has_primary_issue else frame

    def _decode_image(self, image: dict[str, Any] | None) -> np.ndarray | None:
        """Decode parser JPEG bytes or reconstruct supported raw ROS image bytes."""

        if not image or image.get("decode_error") or image.get("raw") is None:
            return None
        raw = bytes(image["raw"])
        if image.get("format") in {"jpeg", "jpg", "png"}:
            return cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        height, width = int(image.get("height", 0)), int(image.get("width", 0))
        encoding = str(image.get("encoding", "")).lower()
        channels = 1 if encoding in {"mono8", "8uc1"} else 3
        if height <= 0 or width <= 0 or len(raw) < height * width * channels:
            return None
        frame = np.frombuffer(raw[:height * width * channels], dtype=np.uint8)
        frame = frame.reshape(height, width) if channels == 1 else frame.reshape(height, width, channels)
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR) if encoding in {"rgb8", "rgb"} else frame

    def _add_detail(self, output: dict[int, list[dict[str, Any]]], index: int, name: str, topic: str, suffix: str) -> None:
        """Append one unique frame anomaly in the documented detail schema."""

        code, description = ANOMALIES[name]
        if name == "end_step":
            desc = f"检测发现末端执行器动作({topic})出现台阶跳变"
        else:
            desc = f"检测发现{description}({topic}); {suffix}"
        candidate = {"anomaly_code": code, "anomaly_name": name, "topic": topic, "desc": desc}
        bucket = output.setdefault(index, [])
        if not any(item["anomaly_code"] == code and item["topic"] == topic for item in bucket):
            bucket.append(candidate)

    def _combine_details(self, *groups: dict[int, list[dict[str, Any]]]) -> dict[int, list[dict[str, Any]]]:
        """Combine detector details into index-sorted public check_detail."""

        indexes = sorted({index for group in groups for index in group})
        return {index: [item for group in groups for item in group.get(index, [])] for index in indexes}

    def _merge_details(self, check_detail: dict[int, list[dict[str, Any]]], timestamps: list[dict[str, str]], source: str, min_frames: int, max_gap: int) -> list[dict[str, Any]]:
        """Merge nearby same-type anomalies only within one source topic."""

        grouped: dict[tuple[int, str, str], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
        namespace = range(1000, 2000) if source == "data" else range(2000, 3000)
        for index, details in check_detail.items():
            for item in details:
                if int(item["anomaly_code"]) in namespace:
                    grouped[(
                        int(item["anomaly_code"]), str(item["anomaly_name"]), str(item["topic"])
                    )].append((index, item))
        output: list[dict[str, Any]] = []
        for (code, name, topic), items in grouped.items():
            items.sort(key=lambda pair: pair[0])
            current: list[tuple[int, dict[str, Any]]] = []
            for item in items:
                if current and item[0] - current[-1][0] > max_gap + 1:
                    self._append_range(output, current, timestamps, code, name, topic, min_frames)
                    current = []
                current.append(item)
            self._append_range(output, current, timestamps, code, name, topic, min_frames)
        return sorted(output, key=lambda item: (item["start_timestamp_index"], item["anomaly_code"], item["topics"]))

    def _append_range(self, output: list[dict[str, Any]], items: list[tuple[int, dict[str, Any]]], timestamps: list[dict[str, str]], code: int, name: str, topic: str, min_frames: int) -> None:
        """Append one topic-local range when enough distinct frames are anomalous."""

        if len({index for index, _item in items}) < min_frames:
            return
        start_index, end_index = items[0][0], items[-1][0]
        descs: list[str] = []
        for _index, item in items:
            if item["desc"] not in descs:
                descs.append(item["desc"])
        output.append({
            "start_timestamp_ns": timestamps[start_index]["timestamp_ns"],
            "start_timestamp_sec": timestamps[start_index]["timestamp_sec"],
            "start_timestamp_index": start_index,
            "end_timestamp_ns": timestamps[end_index]["timestamp_ns"],
            "end_timestamp_sec": timestamps[end_index]["timestamp_sec"],
            "end_timestamp_index": end_index,
            "anomaly_code": code, "anomaly_name": name, "topics": topic, "descs": descs,
        })

    def _seconds_to_frames(self, seconds: Any, fps: float, allow_zero: bool = False) -> int:
        """Convert a public seconds parameter to an internal aligned-frame count."""

        value = float(seconds)
        if value < 0 or (value == 0 and not allow_zero):
            raise ValueError("time parameters must be positive")
        frames = int(round(value * fps))
        return max(0 if allow_zero else 1, frames)
