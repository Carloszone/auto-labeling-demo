"""End-effector trigger detection on the parser-aligned frame timeline."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import ruptures as rpt
from scipy.cluster.vq import kmeans2
from scipy.signal import savgol_filter


class EndEffectorTriggerDetector:
    """Detect stable/moving boundaries with clinear candidates and a duration model."""

    def detect(
        self,
        parser_info: dict[str, Any],
        smooth_config: dict[str, Any],
        trigger_config: dict[str, Any],
        fps: float,
    ) -> list[dict[str, Any]]:
        """Return unlabeled, time-aligned state-change trigger points per camera topic."""

        if trigger_config.get("mode") != "end_effector":
            raise ValueError("trigger_detection.mode must be end_effector")
        params = trigger_config.get("params")
        if not isinstance(params, dict):
            raise ValueError("trigger_detection.params is required")
        self._validate(smooth_config, params)

        output: list[dict[str, Any]] = []
        for schema in parser_info["state_schema"]:
            if schema.get("role") != "end_effector" and schema.get("parser") != "gripper":
                continue
            output.extend(self._detect_topic(parser_info, schema, smooth_config, params, fps))
        return sorted(output, key=lambda item: (item["topic_key"], int(item["timestamp_ns"])))

    def _validate(self, smooth: dict[str, Any], params: dict[str, Any]) -> None:
        """Validate parameters that affect filtering, segmentation, and decoding."""

        if smooth.get("method", "savgol") != "savgol":
            raise ValueError("basic.smooth.method must be savgol")
        window = int(smooth.get("window_frame_length", 10))
        polyorder = int(smooth.get("polyorder", 3))
        if window <= polyorder or polyorder < 0:
            raise ValueError("smooth window_frame_length must be greater than polyorder")
        if params.get("model", "clinear") != "clinear" or str(params.get("algorithm", "Pelt")).lower() != "pelt":
            raise ValueError("MVP trigger detection requires Pelt with model=clinear")
        if float(params.get("min_duration_sec", 0)) <= 0:
            raise ValueError("trigger_detection.params.min_duration_sec must be positive")
        if int(params.get("jump_frames", 1)) <= 0:
            raise ValueError("trigger_detection.params.jump_frames must be positive")
        if int(params.get("state_count", 3)) < 2:
            raise ValueError("trigger_detection.params.state_count must be at least 2")
        if float(params.get("feature_window_sec", 0)) <= 0:
            raise ValueError("trigger_detection.params.feature_window_sec must be positive")
        if float(params.get("candidate_sigma_sec", 4 / 3)) <= 0:
            raise ValueError("trigger_detection.params.candidate_sigma_sec must be positive")
        stay = float(params.get("stay_probability", 0.995))
        if not 0 < stay < 1:
            raise ValueError("trigger_detection.params.stay_probability must be in (0, 1)")

    def _detect_topic(
        self,
        parser_info: dict[str, Any],
        schema: dict[str, Any],
        smooth: dict[str, Any],
        params: dict[str, Any],
        fps: float,
    ) -> list[dict[str, Any]]:
        """Run the full detector for one end-effector state topic."""

        key = str(schema["key"])
        values = np.asarray(
            [np.asarray(row[key], dtype=np.float64).reshape(-1) for row in parser_info["state_list"]]
        )
        min_size = self._seconds_to_frames(params.get("min_duration_sec", 2 / 3), fps)
        if len(values) < max(min_size * 2, 4):
            return []
        fields = list(schema.get("fields", []))
        angle_index = fields.index("angle") if "angle" in fields else 0
        angles = values[:, angle_index]
        smoothed = self._smooth(angles, smooth)
        velocity = self._velocity(values, fields, smoothed, parser_info["timestamp_list"])
        feature_window_frames = self._seconds_to_frames(params.get("feature_window_sec", 1 / 6), fps)
        features = self._features(smoothed, velocity, feature_window_frames)
        candidates = [
            int(index)
            for index in rpt.Pelt(model="clinear", min_size=min_size, jump=int(params.get("jump_frames", 1)))
            .fit(smoothed.reshape(-1, 1))
            .predict(pen=float(params.get("pen", 15)))
            if index < len(smoothed)
        ]
        state_count = min(int(params.get("state_count", 3)), len(features))
        means, variances, stable_state = self._fit_emissions(features, state_count)
        emissions = self._emission_log_likelihood(features, means, variances)
        states = self._decode(
            emissions,
            stable_state,
            candidates,
            min_size,
            float(params.get("stay_probability", 0.995)),
            float(params.get("candidate_sigma_sec", 4 / 3)) * fps,
            float(params.get("candidate_bonus", 1.0)),
        )
        is_moving = states != stable_state
        transitions = np.flatnonzero(is_moving[1:] != is_moving[:-1]) + 1
        timestamps = parser_info["timestamp_list"]
        candidate_array = np.asarray(candidates, dtype=np.int64)
        output: list[dict[str, Any]] = []
        camera_topics = self._camera_topics(parser_info, schema)
        for camera in camera_topics:
            for index in transitions:
                nearest = int(candidate_array[np.argmin(np.abs(candidate_array - index))]) if len(candidate_array) else None
                output.append(
                    {
                        "topic_key": camera["key"],
                        "source_topic": camera["topic"],
                        "timestamp_ns": str(timestamps[index]["timestamp_ns"]),
                        "timestamp_sec": str(timestamps[index]["timestamp_sec"]),
                        "timestamp_index": int(index),
                        "evidence": {
                            "model": "clinear+hsmm",
                            "detection_topic_key": key,
                            "detection_source_topic": str(schema.get("topic", "")),
                            "nearest_clinear_index": nearest,
                            "angle": float(angles[index]),
                            "velocity": float(velocity[index]),
                            "min_duration_sec": float(params.get("min_duration_sec", 2 / 3)),
                        },
                    }
                )
        return output

    def _camera_topics(
        self, parser_info: dict[str, Any], state_schema: dict[str, Any]
    ) -> list[dict[str, str]]:
        """Map one detector topic to every configured camera in the same robot group."""

        cameras = parser_info.get("camera_schema", [])
        group = str(state_schema.get("group", ""))
        matched = [
            {"key": str(camera["key"]), "topic": str(camera["topic"])}
            for camera in cameras
            if group and camera.get("group") == group
        ]
        if matched:
            return matched
        return [{"key": str(state_schema["key"]), "topic": str(state_schema.get("topic", ""))}]

    def _smooth(self, values: np.ndarray, config: dict[str, Any]) -> np.ndarray:
        """Apply the shared Savitzky-Golay configuration without changing sequence length."""

        window = min(int(config.get("window_frame_length", 10)), len(values))
        polyorder = int(config.get("polyorder", 3))
        if window <= polyorder:
            return values.copy()
        return np.asarray(savgol_filter(values, window, polyorder), dtype=np.float64)

    def _velocity(
        self,
        values: np.ndarray,
        fields: list[str],
        smoothed: np.ndarray,
        timestamps: list[dict[str, str]],
    ) -> np.ndarray:
        """Prefer reported velocity and otherwise derive it on the aligned timeline."""

        if "velocity" in fields:
            return values[:, fields.index("velocity")]
        seconds = np.asarray([int(item["timestamp_ns"]) for item in timestamps], dtype=np.float64) / 1e9
        return np.gradient(smoothed, seconds, edge_order=1)

    def _features(self, angles: np.ndarray, velocities: np.ndarray, window: int) -> np.ndarray:
        """Create robustly standardized speed and local-range motion features."""

        trailing_range = np.empty_like(angles)
        for index in range(len(angles)):
            trailing_range[index] = np.ptp(angles[max(0, index - window + 1) : index + 1])
        raw = np.column_stack([np.log1p(np.abs(velocities)), np.log1p(trailing_range * 100.0)])
        center = np.median(raw, axis=0)
        scale = np.median(np.abs(raw - center), axis=0)
        scale = np.where(scale > 1e-6, scale, np.std(raw, axis=0) + 1e-6)
        return (raw - center) / scale

    def _seconds_to_frames(self, seconds: Any, fps: float) -> int:
        """Convert one public duration to an internal frame count."""

        return max(1, int(round(float(seconds) * fps)))

    def _fit_emissions(self, features: np.ndarray, state_count: int) -> tuple[np.ndarray, np.ndarray, int]:
        """Fit deterministic diagonal-Gaussian emissions and identify the quietest state."""

        centroids, assignments = kmeans2(features, state_count, minit="++", seed=7)
        means: list[np.ndarray] = []
        variances: list[np.ndarray] = []
        for state in range(state_count):
            members = features[assignments == state]
            if not len(members):
                members = centroids[state].reshape(1, -1)
            means.append(np.mean(members, axis=0))
            variances.append(np.var(members, axis=0) + 1e-3)
        mean_array = np.vstack(means)
        stable_state = int(np.argmin(np.sum(mean_array, axis=1)))
        return mean_array, np.vstack(variances), stable_state

    def _emission_log_likelihood(
        self, features: np.ndarray, means: np.ndarray, variances: np.ndarray
    ) -> np.ndarray:
        """Evaluate diagonal-Gaussian log likelihood for every base state."""

        columns = []
        for state in range(len(means)):
            residual = features - means[state]
            columns.append(
                -0.5 * np.sum(np.log(2 * np.pi * variances[state]) + residual**2 / variances[state], axis=1)
            )
        return np.column_stack(columns)

    def _decode(
        self,
        emissions: np.ndarray,
        stable_state: int,
        candidates: list[int],
        min_size: int,
        stay_probability: float,
        candidate_sigma: float,
        candidate_bonus: float,
    ) -> np.ndarray:
        """Viterbi-decode a minimum-duration expanded-state HSMM approximation."""

        length, base_count = emissions.shape
        expanded_count = base_count * min_size
        negative_infinity = -1e300
        previous = np.full(expanded_count, negative_infinity)
        backpointers = np.full((length, expanded_count), -1, dtype=np.int32)
        previous[stable_state * min_size] = emissions[0, stable_state]
        candidate_array = np.asarray(candidates, dtype=np.int64)
        for time_index in range(1, length):
            current = np.full(expanded_count, negative_infinity)
            nearest = float(np.min(np.abs(candidate_array - time_index))) if len(candidate_array) else 1e9
            switch_bonus = candidate_bonus * math.exp(-0.5 * (nearest / candidate_sigma) ** 2)
            for state in range(base_count):
                offset = state * min_size
                for age in range(1, min_size):
                    source, target = offset + age - 1, offset + age
                    score = previous[source] + emissions[time_index, state]
                    if score > current[target]:
                        current[target], backpointers[time_index, target] = score, source
                mature = offset + min_size - 1
                stay_score = previous[mature] + math.log(stay_probability) + emissions[time_index, state]
                if stay_score > current[mature]:
                    current[mature], backpointers[time_index, mature] = stay_score, mature
                for other in range(base_count):
                    if other == state:
                        continue
                    target = other * min_size
                    score = (
                        previous[mature]
                        + math.log(1.0 - stay_probability)
                        - math.log(base_count - 1)
                        + switch_bonus
                        + emissions[time_index, other]
                    )
                    if score > current[target]:
                        current[target], backpointers[time_index, target] = score, mature
            previous = current
        expanded_state = int(np.argmax(previous))
        states = np.empty(length, dtype=np.int16)
        for time_index in range(length - 1, -1, -1):
            states[time_index] = expanded_state // min_size
            if time_index:
                expanded_state = int(backpointers[time_index, expanded_state])
                if expanded_state < 0:
                    raise RuntimeError("invalid HSMM backpointer")
        return states
