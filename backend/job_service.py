"""Single-job state, local workspace, pipeline execution, and review persistence."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

import av
import cv2
import numpy as np
from fastapi import UploadFile

from app.core.config import load_robot_config
from app.core.defaults import DEFAULT_INPUT_PROMPT, DEFAULT_SYSTEM_PROMPT, EVENT_GENERATION_DEFAULTS, MULTI_MCAP_POLICY
from app.modules.event_labeling.labeler import EventLabeler
from app.services.orchestrator import AutoLabelingService
from app.services.vlm_client import HttpVlmClient
from backend.errors import ApiError
from backend.schemas import EventPatch, RunRequest
from backend.settings import Settings


LOGGER = logging.getLogger(__name__)
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class SnowflakeGenerator:
    """Small process-local snowflake generator for human-readable job IDs."""

    def __init__(self, worker_id: int) -> None:
        if not 0 <= worker_id <= 1023:
            raise ValueError("worker_id must be in [0, 1023]")
        self.worker_id = worker_id
        self._lock = threading.Lock()
        self._last_ms = -1
        self._sequence = 0

    def next_id(self) -> int:
        with self._lock:
            now_ms = int(time.time() * 1000)
            if now_ms < self._last_ms:
                rollback = self._last_ms - now_ms
                if rollback > 5:
                    raise ApiError(500, "CLOCK_ROLLBACK", "系统时钟回拨，暂时无法创建工作项")
                time.sleep(rollback / 1000)
                now_ms = int(time.time() * 1000)
            if now_ms == self._last_ms:
                self._sequence = (self._sequence + 1) & 0xFFF
                if self._sequence == 0:
                    while now_ms <= self._last_ms:
                        now_ms = int(time.time() * 1000)
            else:
                self._sequence = 0
            self._last_ms = now_ms
            return (now_ms << 22) | (self.worker_id << 12) | self._sequence


class JobService:
    """Own the only job accepted by the MVP HTTP service."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._job: dict[str, Any] | None = None
        self._history: dict[str, dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="auto-label")
        self._ids = SnowflakeGenerator(settings.worker_id)
        self._pipeline = AutoLabelingService()
        self.settings.workspace_root.mkdir(parents=True, exist_ok=True)
        self._clean_stale_workspaces()

    def _clean_stale_workspaces(self) -> None:
        for child in self.settings.workspace_root.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            elif child.suffix == ".part":
                child.unlink(missing_ok=True)

    async def create_job(self, mcaps: list[UploadFile], robot_config: UploadFile) -> dict[str, Any]:
        """Stream an MCAP set plus robot JSON to a new workspace."""

        if len(mcaps) > int(MULTI_MCAP_POLICY["max_segment_count"]):
            raise ApiError(400, "TOO_MANY_MCAPS", "MCAP 文件数量超过内部限制")

        with self._lock:
            if self._job and self._job["status"] == "running":
                raise ApiError(409, "JOB_RUNNING", "当前工作项正在运行，不能创建新工作项")
            self._rotate_current_locked()
            free_bytes = shutil.disk_usage(self.settings.workspace_root).free
            if free_bytes < self.settings.min_free_bytes:
                raise ApiError(507, "INSUFFICIENT_STORAGE", "临时目录可用空间不足 6 GiB")
            job_id = f"job{self._ids.next_id()}"
            workspace = self.settings.workspace_root / job_id
            (workspace / "input").mkdir(parents=True)
            (workspace / "config").mkdir()
            (workspace / "videos").mkdir()
            (workspace / "metadata").mkdir()
            (workspace / "annotations").mkdir()
            (workspace / "export").mkdir()
            now = _utc_now()
            self._job = {
                "job_id": job_id,
                "file_name": Path(mcaps[0].filename or "source.mcap").name,
                "file_names": [Path(item.filename or f"segment_{index:03d}.mcap").name for index, item in enumerate(mcaps)],
                "mcap_count": len(mcaps),
                "file_size_bytes": 0,
                "workspace_path": workspace,
                "mcap_path": workspace / "input" / "segment_000.mcap",
                "mcap_paths": [workspace / "input" / f"segment_{index:03d}.mcap" for index in range(len(mcaps))],
                "segment_manifest": [],
                "robot_config_path": workspace / "config" / "robot.json",
                "status": "validating",
                "stage": "validating",
                "progress": 0,
                "message": "正在上传文件",
                "error": None,
                "warnings": [],
                "effective_config": {},
                "input_prompt": "",
                "duration_sec": None,
                "main_camera_key": None,
                "cameras": [],
                "events": [],
                "raw_result": {"task_id": job_id, "job_id": job_id, "response": []},
                "data_anomaly_ranges": [],
                "image_anomaly_ranges": [],
                "timeline_ns": [],
                "vlm_completed_count": 0,
                "vlm_total_count": 0,
                "created_at": now,
                "completed_at": None,
                "updated_at": now,
                "available_camera_topics": [],
                "main_time_topic": None,
            }

        try:
            size = 0
            for index, upload in enumerate(mcaps):
                if not (upload.filename or "").lower().endswith(".mcap"):
                    raise ApiError(400, "INVALID_MCAP", "MCAP 文件扩展名必须为 .mcap")
                remaining = self.settings.max_upload_bytes - size
                if remaining <= 0:
                    raise ApiError(413, "UPLOAD_TOO_LARGE", "MCAP 文件总大小超过允许值")
                file_size = await self._stream_upload(upload, self._job["mcap_paths"][index], remaining)
                if file_size == 0:
                    raise ApiError(400, "INVALID_MCAP", f"MCAP 文件不能为空: {upload.filename}")
                size += file_size
            await self._stream_upload(robot_config, self._job["robot_config_path"], 2 * 1024 * 1024)
            try:
                config = load_robot_config(self._job["robot_config_path"])
            except (ValueError, KeyError, json.JSONDecodeError) as exc:
                raise ApiError(400, "INVALID_ROBOT_CONFIG", f"Robot Config 校验失败: {exc}") from exc
            if not config.cameras:
                raise ApiError(400, "INVALID_ROBOT_CONFIG", "Robot Config 至少需要一个 camera topic")
            if config.main_time_topic not in {camera.topic for camera in config.cameras}:
                raise ApiError(
                    400,
                    "INVALID_ROBOT_CONFIG",
                    "main_time_topic 必须对应 cameras 中的一个 topic",
                )
            topics = [{"camera_key": item.name, "source_topic": item.topic} for item in config.cameras]
            with self._lock:
                self._job.update({
                    "file_size_bytes": size,
                    "status": "ready_to_run",
                    "stage": "ready_to_run",
                    "progress": 5,
                    "message": "上传和基础校验完成",
                    "available_camera_topics": topics,
                    "main_time_topic": config.main_time_topic,
                    "updated_at": _utc_now(),
                })
                LOGGER.info(
                    "job_created job_id=%s file_names=%s file_size_bytes=%s main_time_topic=%s cameras=%s",
                    job_id, json.dumps(self._job["file_names"], ensure_ascii=False), size, config.main_time_topic,
                    json.dumps(topics, ensure_ascii=False),
                )
                return self.summary(job_id)
        except Exception:
            with self._lock:
                self._delete_current_locked()
            raise
        finally:
            for upload in mcaps:
                await upload.close()
            await robot_config.close()

    async def _stream_upload(self, upload: UploadFile, target: Path, limit: int) -> int:
        part = target.with_suffix(target.suffix + ".part")
        size = 0
        try:
            with part.open("wb") as output:
                while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
                    size += len(chunk)
                    if size > limit:
                        code = "UPLOAD_TOO_LARGE"
                        message = "上传文件超过允许大小"
                        raise ApiError(413, code, message, {"max_bytes": limit})
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            os.replace(part, target)
            return size
        finally:
            part.unlink(missing_ok=True)

    def start(self, job_id: str, request: RunRequest) -> dict[str, Any]:
        """Submit the pipeline to the sole background worker."""

        with self._lock:
            if not self._job or self._job["job_id"] != job_id:
                raise ApiError(409, "HISTORY_READ_ONLY", "历史工作项不能重新运行")
            job = self._require(job_id)
            if job["status"] == "running":
                raise ApiError(409, "JOB_RUNNING", "当前工作项正在运行")
            if job["status"] not in {"ready_to_run", "ready", "failed"}:
                raise ApiError(409, "INVALID_JOB_STATE", "当前状态不能启动自动标注")
            self._clear_artifacts(job)
            effective_config = request.model_dump(exclude_none=True)
            effective_config["input_prompt"] = request.input_prompt.strip() or DEFAULT_INPUT_PROMPT
            effective_config["system_prompt"] = request.system_prompt.strip() or DEFAULT_SYSTEM_PROMPT
            job.update({
                "status": "running", "stage": "parsing", "progress": 5,
                "message": "自动标注已启动", "error": None, "warnings": [],
                "effective_config": effective_config,
                "input_prompt": effective_config["input_prompt"],
                "system_prompt": effective_config["system_prompt"],
                "updated_at": _utc_now(),
            })
            self._atomic_json(job["workspace_path"] / "config" / "effective_pipeline.json", job["effective_config"])
            LOGGER.info(
                "pipeline_input job_id=%s config=%s",
                job_id, json.dumps(job["effective_config"], ensure_ascii=False, default=str),
            )
            self._executor.submit(self._execute, job_id, request)
            return self.summary(job_id)

    def _clear_artifacts(self, job: dict[str, Any]) -> None:
        for folder in ("videos", "metadata", "annotations", "export"):
            path = job["workspace_path"] / folder
            shutil.rmtree(path, ignore_errors=True)
            path.mkdir()
        job.update({
            "duration_sec": None, "main_camera_key": None, "cameras": [], "events": [],
            "completed_at": None,
            "data_anomaly_ranges": [], "image_anomaly_ranges": [], "timeline_ns": [],
            "segment_manifest": [],
            "vlm_completed_count": 0, "vlm_total_count": 0,
            "raw_result": {"task_id": job["job_id"], "job_id": job["job_id"], "response": []},
        })

    def _execute(self, job_id: str, request: RunRequest) -> None:
        pipeline_started = time.perf_counter()
        try:
            with self._lock:
                job = self._require(job_id)
                mcap_paths = list(job["mcap_paths"])
                robot_path = job["robot_config_path"]
                workspace = job["workspace_path"]
            robot = load_robot_config(robot_path)
            main_topic = request.robot_config_overrides.get("main_time_topic")
            if main_topic:
                if main_topic not in {camera.topic for camera in robot.cameras}:
                    raise ValueError("main_time_topic must match one configured camera topic")
                robot.main_time_topic = str(main_topic)
            self._update(job_id, main_time_topic=robot.main_time_topic)

            self._set_stage(job_id, "parsing", 5, "正在解析和对齐 MCAP")
            parser_request = self._pipeline._parser_request(
                job_id, job_id, mcap_paths[0], robot, None, request.parser_config
            )
            parser_request["parser"]["mcap_paths"] = mcap_paths
            with self._timed(job_id, "parser"):
                parser_info = self._pipeline.parser.parse(parser_request)
            self._atomic_json(workspace / "metadata" / "segments.json", parser_info.get("segment_manifest", []))
            self._atomic_json(workspace / "metadata" / "frames.json", parser_info.get("frame_manifest", []))
            timestamps_ns = [int(item["timestamp_ns"]) for item in parser_info["timestamp_list"]]
            duration = max(0.0, (timestamps_ns[-1] - timestamps_ns[0]) / 1e9)
            LOGGER.info(
                "parser_output job_id=%s aligned_frames=%s duration_sec=%.9f main_camera_key=%s state_topics=%s action_topics=%s",
                job_id, len(timestamps_ns), duration, parser_info["main_time_camera_key"],
                len(parser_info.get("state_schema", [])), len(parser_info.get("action_schema", [])),
            )
            LOGGER.info(
                "multi_mcap_manifest job_id=%s segments=%s",
                job_id, json.dumps(parser_info.get("segment_manifest", []), ensure_ascii=False),
            )
            self._update(job_id, timeline_ns=timestamps_ns, duration_sec=duration, main_camera_key=parser_info["main_time_camera_key"])
            self._update(job_id, segment_manifest=deepcopy(parser_info.get("segment_manifest", [])))

            self._set_stage(job_id, "video_generating", 25, "正在生成摄像头视频")
            with self._timed(job_id, "video_generation"):
                cameras, warnings = self._generate_videos(job_id, workspace, parser_info, robot, duration)
            self._update(job_id, cameras=cameras, warnings=warnings)

            self._set_stage(job_id, "data_checking", 40, "正在进行数据和图像质检")
            check_config = self._pipeline._data_check_config(request.data_check_config)
            check_basic = check_config.pop("basic")
            check_basic.update({"task_id": job_id, "job_id": job_id, "parser_info": parser_info})
            with self._timed(job_id, "data_check"):
                check_info = self._pipeline.checker.check({"basic": check_basic, **check_config})
            LOGGER.info(
                "data_check_output job_id=%s data_anomaly_count=%s image_anomaly_count=%s trigger_points=%s",
                job_id, len(check_info.get("data_anomaly_ranges", [])),
                len(check_info.get("img_anomaly_ranges", [])),
                json.dumps(check_info.get("trigger_points", {}), ensure_ascii=False, default=str),
            )
            self._set_stage(job_id, "event_generating", 60, "正在生成事件区间")
            generation_config = deepcopy(EVENT_GENERATION_DEFAULTS)
            generation_config = self._merge(generation_config, request.event_generation_config)
            generation_config.pop("basic", None)
            with self._timed(job_id, "event_generation"):
                generation_info = self._pipeline.generator.generate({
                    "basic": {"task_id": job_id, "job_id": job_id, "check_info": check_info, "parser_info": parser_info},
                    **generation_config,
                })
            LOGGER.info(
                "event_generation_output job_id=%s event_periods=%s",
                job_id,
                json.dumps(generation_info.get("event_periods", {}), ensure_ascii=False, default=str),
            )
            total_events = sum(len(items) for items in generation_info["event_periods"].values())
            self._update(job_id, vlm_total_count=total_events)
            self._set_stage(job_id, "vlm_labeling", 65, f"正在标注 event（0/{total_events}）")
            label_config = self._pipeline._event_labeling_config(
                request.event_labeling_config,
                {
                    "input_prompt": request.input_prompt.strip() or DEFAULT_INPUT_PROMPT,
                    "system_prompt": request.system_prompt.strip() or DEFAULT_SYSTEM_PROMPT,
                },
            )
            label_config.pop("basic", None)
            if self.settings.vlm_endpoint:
                vlm_client: Any = HttpVlmClient(self.settings.vlm_endpoint, self.settings.vlm_timeout_sec)
            else:
                vlm_client = _MissingVlmClient()

            def on_labeled(completed: int, total: int) -> None:
                progress = 95 if total == 0 else 65 + int(30 * completed / total)
                self._update(
                    job_id, progress=progress, vlm_completed_count=completed,
                    message=f"正在标注 event（{completed}/{total}）",
                )

            with self._timed(job_id, "event_labeling"):
                raw_result = EventLabeler(vlm_client, progress_callback=on_labeled).label({
                    "basic": {
                        "task_id": job_id, "job_id": job_id,
                        "parser_info": parser_info, "generation_info": generation_info,
                    },
                    **label_config,
                })
            self._set_stage(job_id, "saving", 95, "正在保存标注结果")
            with self._timed(job_id, "result_saving"):
                self._atomic_json(workspace / "annotations" / "raw.json", raw_result)
                self._atomic_json(workspace / "annotations" / "reviewed.json", raw_result)
            events = [self._event_view(item) for item in raw_result["response"]]
            data_ranges = self._anomaly_views(check_info["data_anomaly_ranges"], timestamps_ns[0])
            image_ranges = self._anomaly_views(check_info["img_anomaly_ranges"], timestamps_ns[0])
            self._update(
                job_id, status="ready", stage="ready", progress=100, message="自动标注完成",
                completed_at=_utc_now(),
                raw_result=raw_result, events=events, data_anomaly_ranges=data_ranges,
                image_anomaly_ranges=image_ranges,
            )
            with self._lock:
                self._prune_history_locked()
            LOGGER.info(
                "pipeline_completed job_id=%s event_count=%s duration_sec=%.6f",
                job_id, len(events), time.perf_counter() - pipeline_started,
            )
        except Exception as exc:
            LOGGER.exception(
                "pipeline_failed job_id=%s duration_sec=%.6f",
                job_id, time.perf_counter() - pipeline_started,
            )
            code = "VLM_UNAVAILABLE" if isinstance(exc, (TimeoutError, ConnectionError)) or "VLM" in str(exc) else "PIPELINE_FAILED"
            self._update(
                job_id, status="failed", stage="failed", message="自动标注失败",
                error={"code": code, "message": str(exc)},
            )

    @contextmanager
    def _timed(self, job_id: str, module: str):
        started = time.perf_counter()
        LOGGER.info("module_started job_id=%s module=%s", job_id, module)
        try:
            yield
        except Exception:
            LOGGER.exception(
                "module_failed job_id=%s module=%s duration_sec=%.6f",
                job_id, module, time.perf_counter() - started,
            )
            raise
        LOGGER.info(
            "module_completed job_id=%s module=%s duration_sec=%.6f",
            job_id, module, time.perf_counter() - started,
        )

    def _generate_videos(
        self, job_id: str, workspace: Path, parser_info: dict[str, Any], robot: Any, duration: float
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        cameras: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        candidates = [camera for camera in robot.cameras if camera.output_video] or list(robot.cameras)
        main_key = parser_info["main_time_camera_key"]
        for index, camera in enumerate(candidates, start=1):
            target = workspace / "videos" / f"{camera.name}.mp4"
            try:
                self._encode_camera(parser_info["image_list"], camera.name, target)
                cameras.append({
                    "camera_key": camera.name,
                    "source_topic": camera.topic,
                    "video_url": f"/api/v1/jobs/{job_id}/videos/{camera.name}",
                    "duration_sec": duration,
                    "is_main_camera": camera.name == main_key,
                    "generation_status": "ready",
                    "error": None,
                })
            except Exception as exc:
                if camera.name == main_key:
                    raise RuntimeError(f"主摄像头视频生成失败 ({camera.name}): {exc}") from exc
                warning = {"code": "SECONDARY_VIDEO_FAILED", "camera_key": camera.name, "message": str(exc)}
                warnings.append(warning)
                cameras.append({
                    "camera_key": camera.name, "source_topic": camera.topic, "video_url": None,
                    "duration_sec": duration, "is_main_camera": False,
                    "generation_status": "failed", "error": warning,
                })
            self._update(job_id, progress=25 + int(15 * index / max(len(candidates), 1)))
        return cameras, warnings

    def _encode_camera(self, image_list: list[dict[str, Any]], camera_key: str, target: Path) -> None:
        if not image_list:
            raise ValueError("没有可编码图像")
        first = self._decode_frame(image_list[0], camera_key)
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
                frame = first if index == 0 else self._decode_frame(row, camera_key)
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

    def _decode_frame(self, row: dict[str, Any], camera_key: str) -> np.ndarray:
        image = row.get(camera_key)
        if not image or image.get("raw") is None:
            raise ValueError("缺少对齐图像")
        raw = np.frombuffer(bytes(image["raw"]), dtype=np.uint8)
        frame = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("无法解码图像")
        return frame

    def summary(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._require(job_id)
            counts = {status: 0 for status in ("pending", "accepted", "rejected")}
            for event in job["events"]:
                counts[event["review_status"]] += 1
            return {
                "job_id": job["job_id"], "file_name": job["file_name"],
                "file_names": deepcopy(job["file_names"]), "mcap_count": job["mcap_count"],
                "file_size_bytes": job["file_size_bytes"], "status": job["status"],
                "stage": job["stage"], "progress": job["progress"], "message": job["message"],
                "duration_sec": job["duration_sec"], "camera_count": len(job["cameras"]),
                "event_count": len(job["events"]), "pending_event_count": counts["pending"],
                "accepted_event_count": counts["accepted"], "rejected_event_count": counts["rejected"],
                "vlm_completed_count": job["vlm_completed_count"], "vlm_total_count": job["vlm_total_count"],
                "warnings": deepcopy(job["warnings"]), "error": deepcopy(job["error"]),
                "available_camera_topics": deepcopy(job["available_camera_topics"]),
                "main_time_topic": job["main_time_topic"],
                "segment_manifest": deepcopy(job["segment_manifest"]),
                "created_at": job["created_at"], "completed_at": job["completed_at"],
                "updated_at": job["updated_at"],
            }

    def current_summary(self) -> dict[str, Any]:
        with self._lock:
            if not self._job:
                raise ApiError(404, "JOB_NOT_FOUND", "当前没有工作项")
            return self.summary(self._job["job_id"])

    def history(self) -> list[dict[str, Any]]:
        """Return at most five successful jobs, newest first."""

        with self._lock:
            jobs = list(self._history.values())
            if self._job and self._job["status"] == "ready":
                jobs.append(self._job)
            jobs.sort(key=lambda item: item.get("completed_at") or item["updated_at"], reverse=True)
            return [self.summary(item["job_id"]) for item in jobs[:5]]

    def result(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._require(job_id)
            return {
                "job_id": job_id, "duration_sec": job["duration_sec"],
                "main_camera_key": job["main_camera_key"], "cameras": deepcopy(job["cameras"]),
                "events": deepcopy(job["events"]),
                "data_anomaly_ranges": deepcopy(job["data_anomaly_ranges"]),
                "image_anomaly_ranges": deepcopy(job["image_anomaly_ranges"]),
            }

    def update_event(self, job_id: str, event_id: str, patch: EventPatch) -> dict[str, Any]:
        with self._lock:
            job = self._require(job_id)
            if job["status"] != "ready":
                raise ApiError(409, "INVALID_JOB_STATE", "只有已完成的工作项可以修改 event")
            try:
                index = next(i for i, event in enumerate(job["events"]) if event["id"] == event_id)
            except StopIteration as exc:
                raise ApiError(404, "EVENT_NOT_FOUND", "event 不存在") from exc
            event = deepcopy(job["events"][index])
            values = patch.model_dump(exclude_unset=True)
            event.update(values)
            start, end = float(event["start_sec"]), float(event["end_sec"])
            if end <= start or job["duration_sec"] is None or end > float(job["duration_sec"]) + 1e-9:
                raise ApiError(422, "EVENT_VALIDATION_FAILED", "event 时间范围非法")
            raw = next(item for item in job["raw_result"]["response"] if item["id"] == event_id)
            if "start_sec" in values or "end_sec" in values:
                start = self._snap_time(job["timeline_ns"], start)
                end = self._snap_time(job["timeline_ns"], end)
                if end <= start:
                    raise ApiError(422, "EVENT_VALIDATION_FAILED", "吸附到视频帧后 event 时长必须大于 0")
                event["start_sec"], event["end_sec"] = start, end
                self._apply_time(raw, "start", start, job["timeline_ns"])
                self._apply_time(raw, "end", end, job["timeline_ns"])
            for key in ("prompt", "description", "action_state", "review_status"):
                if key in values:
                    raw[key] = values[key]
            job["events"][index] = event
            job["updated_at"] = _utc_now()
            self._atomic_json(job["workspace_path"] / "annotations" / "reviewed.json", job["raw_result"])
            LOGGER.info(
                "event_updated job_id=%s event_id=%s patch=%s result=%s",
                job_id, event_id, json.dumps(values, ensure_ascii=False),
                json.dumps(event, ensure_ascii=False),
            )
            return deepcopy(event)

    def _snap_time(self, timeline: list[int], value: float) -> float:
        base = timeline[0]
        target = base + int(round(value * 1e9))
        nearest = min(timeline, key=lambda item: abs(item - target))
        return (nearest - base) / 1e9

    def _apply_time(self, raw: dict[str, Any], side: str, relative: float, timeline: list[int]) -> None:
        base = timeline[0]
        absolute_ns = base + int(round(relative * 1e9))
        relative_ns = absolute_ns - base
        absolute_sec = f"{absolute_ns // 1_000_000_000}.{absolute_ns % 1_000_000_000:09d}"
        relative_sec = f"{relative_ns // 1_000_000_000}.{relative_ns % 1_000_000_000:09d}"
        if side == "start":
            raw.update({
                "startSec": f"{relative:.3f}", "start_time": absolute_sec,
                "startTimeNs": str(absolute_ns), "startTimestampNs": str(absolute_ns),
                "episodeStartTimeNs": str(relative_ns), "episode_start_time": relative_sec,
                "timeline_start_sec": relative_sec,
            })
        else:
            raw.update({
                "endSec": f"{relative:.3f}", "end_time": absolute_sec,
                "endTimeNs": str(absolute_ns), "endTimestampNs": str(absolute_ns),
                "episodeEndTimeNs": str(relative_ns), "episode_end_time": relative_sec,
                "timeline_end_sec": relative_sec,
            })

    def export_path(self, job_id: str) -> Path:
        with self._lock:
            job = self._require(job_id)
            if job["status"] != "ready":
                raise ApiError(409, "INVALID_JOB_STATE", "工作项尚未完成")
            accepted = [deepcopy(item) for item in job["raw_result"]["response"] if item.get("review_status") == "accepted"]
            result = {"task_id": job_id, "job_id": job_id, "response": accepted}
            target = job["workspace_path"] / "export" / "annotations.json"
            self._atomic_json(target, result)
            LOGGER.info("annotations_exported job_id=%s accepted_event_count=%s", job_id, len(accepted))
            return target

    def export_filename(self, job_id: str) -> str:
        with self._lock:
            job = self._require(job_id)
            return f"{Path(job['file_name']).stem}.annotations.json"

    def video_path(self, job_id: str, camera_key: str) -> Path:
        with self._lock:
            job = self._require(job_id)
            camera = next((item for item in job["cameras"] if item["camera_key"] == camera_key), None)
            if not camera or camera["generation_status"] != "ready":
                raise ApiError(404, "VIDEO_NOT_FOUND", "视频不存在或生成失败")
            target = job["workspace_path"] / "videos" / f"{camera_key}.mp4"
            if not target.exists():
                raise ApiError(404, "VIDEO_NOT_FOUND", "视频文件不存在")
            return target

    def delete(self, job_id: str) -> None:
        with self._lock:
            job = self._require(job_id)
            if job["status"] == "running":
                raise ApiError(409, "JOB_RUNNING", "运行中的工作项不能删除")
            if self._job and self._job["job_id"] == job_id:
                self._delete_current_locked()
            else:
                removed = self._history.pop(job_id)
                shutil.rmtree(removed["workspace_path"], ignore_errors=True)

    def _delete_current_locked(self) -> None:
        if self._job:
            shutil.rmtree(self._job["workspace_path"], ignore_errors=True)
        self._job = None

    def _rotate_current_locked(self) -> None:
        """Archive a successful current job and discard unsuccessful state."""

        if not self._job:
            return
        if self._job["status"] == "ready":
            self._history[self._job["job_id"]] = self._job
            self._job = None
            self._prune_history_locked()
        else:
            self._delete_current_locked()

    def _prune_history_locked(self) -> None:
        """Keep five successful jobs total, counting a ready current job."""

        history_limit = 4 if self._job and self._job["status"] == "ready" else 5
        ordered = sorted(
            self._history.values(),
            key=lambda item: item.get("completed_at") or item["updated_at"], reverse=True,
        )
        keep = {item["job_id"] for item in ordered[:history_limit]}
        for job_id in list(self._history):
            if job_id not in keep:
                removed = self._history.pop(job_id)
                shutil.rmtree(removed["workspace_path"], ignore_errors=True)

    def _require(self, job_id: str) -> dict[str, Any]:
        if self._job and self._job["job_id"] == job_id:
            return self._job
        if job_id in self._history:
            return self._history[job_id]
        raise ApiError(404, "JOB_NOT_FOUND", "工作项不存在")

    def _set_stage(self, job_id: str, stage: str, progress: int, message: str) -> None:
        self._update(job_id, status="running", stage=stage, progress=progress, message=message)

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._require(job_id)
            if "progress" in values and job["status"] == "running":
                values["progress"] = max(int(job["progress"]), int(values["progress"]))
            values["updated_at"] = _utc_now()
            job.update(values)

    def _event_view(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(raw["id"]), "topic_key": str(raw.get("topic_key", "")),
            "source_topic": str(raw.get("source_topic", "")),
            "start_sec": float(raw.get("timeline_start_sec", raw.get("episode_start_time", raw.get("startSec", 0)))),
            "end_sec": float(raw.get("timeline_end_sec", raw.get("episode_end_time", raw.get("endSec", 0)))),
            "prompt": str(raw.get("prompt", "")), "description": str(raw.get("description", "")),
            "baseline_camera_key": str(raw.get("baseline_camera_key", "")),
            "action_state": int(raw.get("action_state", 0)),
            "review_status": str(raw.get("review_status", "pending")),
        }

    def _anomaly_views(self, ranges: list[dict[str, Any]], base_ns: int) -> list[dict[str, Any]]:
        output = []
        for item in ranges:
            descs = item.get("descs", [])
            flattened = [desc for values in descs.values() for desc in values] if isinstance(descs, dict) else list(descs)
            output.append({
                "anomaly_code": item["anomaly_code"], "anomaly_name": item["anomaly_name"],
                "start_sec": (int(item["start_timestamp_ns"]) - base_ns) / 1e9,
                "end_sec": (int(item["end_timestamp_ns"]) - base_ns) / 1e9,
                "topics": str(item.get("topics", "")), "descs": flattened,
            })
        return output

    def _atomic_json(self, target: Path, value: Any) -> None:
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
        os.replace(temp, target)

    def _merge(self, defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(defaults)
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = self._merge(result[key], value)
            else:
                result[key] = deepcopy(value)
        return result


class _MissingVlmClient:
    def label(self, **_kwargs: Any) -> dict[str, Any]:
        raise ValueError("VLM endpoint is not configured; set AUTO_LABEL_VLM_ENDPOINT")
