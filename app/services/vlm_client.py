"""HTTP client for calling the configured VLM chat service."""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from json_repair import repair_json


class HttpVlmClient:
    """Call a VLM HTTP endpoint and normalize its JSON response."""

    def __init__(self, endpoint: str, timeout_sec: float = 120.0) -> None:
        """Store endpoint and timeout for later labeling calls."""

        self.endpoint = endpoint
        self.timeout_sec = timeout_sec

    def label(self, *, model: str, system_prompt: str, input_prompt: str, samples: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        """Send sampled frames to VLM and return action_summary/action_state/details."""

        payload = {
            "model": model,
            "system_prompt": system_prompt,
            "input": self._build_input(input_prompt, samples),
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
            raw_response = response.read().decode("utf-8")
        return self._parse_response(raw_response)

    def _build_input(self, input_prompt: str, samples: dict[str, list[dict[str, Any]]]) -> list[dict[str, str]]:
        """Build the mixed text/image input list expected by the VLM service."""

        items: list[dict[str, str]] = [{"type": "text", "content": input_prompt}]
        for camera_key, camera_samples in samples.items():
            for sample in camera_samples:
                timestamp = sample.get("timestamp_sec", "")
                role = sample.get("sample_role", "event")
                items.append({"type": "text", "content": f"{camera_key} {role} frame {sample['frame_index']}, timestamp: {timestamp} seconds"})
                image_format = "png" if sample.get("format") == "png" else "jpeg"
                items.append({"type": "image", "data_url": f"data:image/{image_format};base64,{sample['image_base64']}"})
        return items

    def _parse_response(self, raw_response: str) -> dict[str, Any]:
        """Parse a VLM response, repairing JSON text when needed."""

        try:
            data = json.loads(raw_response)
        except json.JSONDecodeError:
            data = json.loads(repair_json(raw_response))
        if isinstance(data, dict) and "action_summary" in data:
            return self._validate_label(data)
        content = self._extract_content(data)
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        repaired = json.loads(repair_json(content))
        return self._validate_label(repaired)

    def _extract_content(self, data: Any) -> Any:
        """Extract model message content from supported VLM response envelopes."""

        if not isinstance(data, dict):
            return data
        output = data.get("output")
        if isinstance(output, list):
            for item in reversed(output):
                if isinstance(item, dict) and item.get("type") == "message" and "content" in item:
                    return item["content"]
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict):
                message = choice.get("message")
                if isinstance(message, dict) and "content" in message:
                    return message["content"]
                if "text" in choice:
                    return choice["text"]
        for key in ("content", "message", "response"):
            value = data.get(key)
            if isinstance(value, dict) and "content" in value:
                return value["content"]
            if value is not None:
                return value
        raise ValueError("VLM response does not contain message content")

    def _validate_label(self, data: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize required fields from a VLM label."""

        if not isinstance(data, dict):
            raise ValueError("VLM label must be a JSON object")
        for key in ["action_summary", "action_state", "detailed_description"]:
            if key not in data:
                raise ValueError(f"VLM response missing {key}")
        action_state = int(data["action_state"])
        if action_state not in {-1, 0, 1}:
            raise ValueError("VLM action_state must be -1, 0, or 1")
        return {
            "action_summary": str(data["action_summary"]),
            "action_state": action_state,
            "detailed_description": str(data["detailed_description"]),
        }
