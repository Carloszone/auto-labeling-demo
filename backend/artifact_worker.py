"""Isolated MCAP parsing and CFR video materialization worker."""

from __future__ import annotations

import argparse
import json
import os
import pickle
from fractions import Fraction
from pathlib import Path
from typing import Any

import av
import cv2
import numpy as np

from app.core.config import load_robot_config
from app.services.orchestrator import AutoLabelingService


def _decode_frame(row: dict[str, Any], camera_key: str) -> np.ndarray:
    image = row.get(camera_key)
    if not image or image.get("raw") is None:
        raise ValueError("missing aligned image")
    frame = cv2.imdecode(np.frombuffer(bytes(image["raw"]), dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("cannot decode aligned image")
    return frame


def _encode_camera(image_list: list[dict[str, Any]], camera_key: str, target: Path) -> None:
    if not image_list:
        raise ValueError("no images to encode")
    first = _decode_frame(image_list[0], camera_key)
    height, width = first.shape[:2]
    even_width, even_height = width + width % 2, height + height % 2
    part = target.with_suffix(".mp4.part")
    container = av.open(str(part), mode="w", format="mp4", options={"movflags": "+faststart"})
    try:
        stream = container.add_stream("libx264", rate=30)
        stream.width = even_width
        stream.height = even_height
        stream.pix_fmt = "yuv420p"
        stream.options = {"preset": "veryfast", "crf": "23"}
        for index, row in enumerate(image_list):
            frame = first if index == 0 else _decode_frame(row, camera_key)
            if frame.shape[1] != even_width or frame.shape[0] != even_height:
                frame = cv2.copyMakeBorder(
                    frame, 0, even_height - frame.shape[0], 0, even_width - frame.shape[1],
                    cv2.BORDER_CONSTANT,
                )
            video_frame = av.VideoFrame.from_ndarray(frame, format="bgr24")
            video_frame.pts = index
            video_frame.time_base = Fraction(1, 30)
            for packet in stream.encode(video_frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()
    os.replace(part, target)


def materialize(request: dict[str, Any]) -> dict[str, Any]:
    """Parse all MCAPs, encode every camera, and return no image payloads."""

    workspace = Path(request["workspace"])
    robot = load_robot_config(request["robot_config_path"])
    main_topic = request.get("main_time_topic")
    if main_topic:
        robot.main_time_topic = str(main_topic)
    service = AutoLabelingService()
    parser_request = service._parser_request(
        str(request["job_id"]), str(request["job_id"]), Path(request["mcap_paths"][0]),
        robot, request.get("max_frames"), request.get("parser_config"),
    )
    parser_request["parser"]["mcap_paths"] = [Path(path) for path in request["mcap_paths"]]
    parser_info = service.parser.parse(parser_request)
    timestamps_ns = [int(item["timestamp_ns"]) for item in parser_info["timestamp_list"]]
    duration = max(0.0, (timestamps_ns[-1] - timestamps_ns[0]) / 1e9)
    main_key = parser_info["main_time_camera_key"]
    cameras: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    video_paths: dict[str, str] = {}
    for camera in robot.cameras:
        target = workspace / "videos" / f"{camera.name}.mp4"
        try:
            _encode_camera(parser_info["image_list"], camera.name, target)
            video_paths[camera.name] = str(target)
            cameras.append({
                "camera_key": camera.name, "source_topic": camera.topic,
                "video_url": f"/api/v1/jobs/{request['job_id']}/videos/{camera.name}",
                "duration_sec": duration, "is_main_camera": camera.name == main_key,
                "generation_status": "ready", "error": None,
            })
        except Exception as exc:
            if camera.name == main_key:
                raise RuntimeError(f"main camera video generation failed ({camera.name}): {exc}") from exc
            warning = {"code": "SECONDARY_VIDEO_FAILED", "camera_key": camera.name, "message": str(exc)}
            warnings.append(warning)
            cameras.append({
                "camera_key": camera.name, "source_topic": camera.topic, "video_url": None,
                "duration_sec": duration, "is_main_camera": False,
                "generation_status": "failed", "error": warning,
            })
    parser_info["video_paths"] = video_paths
    parser_info["video_fps"] = 30
    parser_info.pop("image_list", None)
    return {
        "parser_info": parser_info, "timestamps_ns": timestamps_ns, "duration_sec": duration,
        "cameras": cameras, "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = materialize(json.loads(args.request.read_text()))
    part = args.output.with_suffix(args.output.suffix + ".part")
    with part.open("wb") as stream:
        pickle.dump(result, stream, protocol=pickle.HIGHEST_PROTOCOL)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(part, args.output)


if __name__ == "__main__":
    main()
