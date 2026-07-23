# 后端功能模块设计文档

## 1. 文档状态与目标

- 文档类型：可实施的 Demo 后端设计与接口规范
- 所属流程：MCAP 自动标注服务编排与 API 层
- 当前阶段：MVP 实施基线
- 核心目标：让局域网内其他主机通过 HTTP 上传一个 MCAP，调用现有算法，查看多摄像头视频与标注，并完成简单人工复核和 JSON 导出

本设计优先保证快速跑通和便于调试。MVP 不使用数据库、S3、Redis、Celery 或消息队列。

## 2. 范围与约束

### 2.1 MVP 包含

- 前端和后端部署在同一台 Linux 服务主机。
- 后端监听 `0.0.0.0:8000`，局域网客户端通过 `http://<server-ip>:8000` 访问。
- 单次上传 1–32 个属于同一连续任务的 MCAP 和一个 robot config JSON。
- 服务同一时间只运行一个当前工作项，并在进程内保留最近 5 个成功工作项供查看、复核和导出。
- 工作项内所有 MCAP 合计最大 50 GiB，上传到后端本地临时目录。
- 串行调用 Parser、DataCheck、EventGeneration 和 EventLabeling。
- 使用 PyAV 将各摄像头对齐帧编码为 H.264 MP4。
- 提供进度、视频、event、异常区间、人工复核和 JSON 导出接口。
- 自动生成的 event 初始包含 `review_status: "pending"`。

### 2.2 MVP 不包含

- 多工作项并发和跨任务批处理。
- 数据库、S3 和服务重启后的任务恢复。
- 登录、角色、权限和公网安全能力。
- 任务取消、断点续传和阶段级续跑。
- 多人同时编辑、版本冲突和完整审计历史。
- 一级 event 合并和根据异常区间裁减 event。

## 3. 技术方案

### 3.1 技术栈

- Web 框架：FastAPI。
- 运行服务：Uvicorn，单 worker。
- 请求模型：Pydantic。
- 大文件表单：`python-multipart` + FastAPI `UploadFile`。
- 后台执行：单进程 `ThreadPoolExecutor(max_workers=1)`。
- 视频编码：项目已有 PyAV，使用 H.264、`yuv420p`、30 FPS 和 MP4 容器。
- 文件响应：Starlette/FastAPI `FileResponse`，视频必须支持 HTTP Range。
- 日志：Python `logging`，结构化关键字段写入普通文本日志。

单 worker 是硬性要求。任务状态保存在进程内存中，多 worker 会产生多个不一致的“当前工作项”。

### 3.2 部署方式

开发环境：

```text
Vite dev server :5173 ──proxy /api──> FastAPI :8000
```

演示环境：

```text
FastAPI :8000
├── /api/v1/*       后端 API
├── /assets/*       前端构建资源
└── /*              frontend/dist/index.html
```

演示环境由 FastAPI 托管 `frontend/dist`，前后端同源，不启用 CORS。服务只允许在可信局域网使用；主机防火墙仅向演示网段开放 8000 端口。

环境变量：

本地开发和演示部署默认从项目根目录 `.env` 加载配置；操作系统、systemd 或容器已经注入的同名环境变量优先于 `.env`。真实 `.env` 不提交到 Git，仓库只提供 `.env.example`。可通过 `AUTO_LABEL_ENV_FILE` 指定其他配置文件。未来数据库密码和 S3 密钥也遵循该规则，但当前 MVP 不读取或依赖这些配置。

| 变量 | 默认值 | 用途 |
|---|---|---|
| `AUTO_LABEL_HOST` | `0.0.0.0` | HTTP 监听地址 |
| `AUTO_LABEL_PORT` | `8000` | HTTP 监听端口 |
| `AUTO_LABEL_WORKSPACE_ROOT` | `/tmp/auto-labeling-demo` | 临时工作目录 |
| `AUTO_LABEL_MAX_UPLOAD_BYTES` | `53687091200` | 工作项内 MCAP 合计最大 50 GiB |
| `AUTO_LABEL_WORKER_ID` | `1` | 雪花 ID worker ID，范围 0–1023 |
| `AUTO_LABEL_VLM_ENDPOINT` | 无默认值 | VLM HTTP 地址 |
| `AUTO_LABEL_VLM_TIMEOUT_SEC` | `300` | 单次 VLM 请求超时；仅约束单次 Event 的 VLM HTTP 调用，不是整个 Pipeline 总时长 |
| `AUTO_LABEL_ENV_FILE` | `<项目根目录>/.env` | 可选的环境变量文件路径 |
| `AUTO_LABEL_LOG_PATH` | `logs/auto-labeling-demo.log` | 后端日志文件 |
| `AUTO_LABEL_LOG_LEVEL` | `INFO` | 日志等级 |
| `AUTO_LABEL_LOG_MAX_BYTES` | `20971520` | 单个日志文件最大 20 MiB |
| `AUTO_LABEL_LOG_BACKUP_COUNT` | `5` | 轮转日志保留数量 |

## 4. 服务模块

1. `DemoJobService`
   - 持有唯一的 `CurrentJob` 和最多 5 个成功历史工作项。
   - 负责状态机、雪花 ID、互斥和工作项替换。
2. `UploadService`
   - 校验一个或多个 `mcaps` multipart 字段、robot config 和文件合计大小。
   - 将上传内容流式写入 `.part`，成功后原子改名。
3. `LocalWorkspaceService`
   - 创建、清理当前工作目录。
   - 原子写入配置、标注和导出文件。
4. `AutoLabelingRunner`
   - 在后台线程串行调用算法模块。
   - 更新阶段进度和错误信息。
5. `VideoArtifactService`
   - 通过独立 Python 工作进程执行 Parser，并使用对齐后的 `image_list` 为全部配置相机生成固定 30 FPS MP4。
   - 子进程只落盘不含图像的 `parser_compact.pkl`；退出后由操作系统回收 Parser、JPEG 和编码器堆内存。
   - 主进程只加载 `video_paths`、时间轴、state/action 和 manifest；后续模块按帧索引解码。
   - 主相机失败则任务失败；非主相机失败记录 warning 并继续。
8. `MultiMcapPreflightService`
   - 仅读取每个 MCAP 的 `main_time_topic` 元数据，按实际起止时间戳排序。
   - 检查重叠和缺口；禁止使用上传顺序或文件名序号直接排序。
   - 输出 `segment_manifest`，记录接受帧数和重叠丢弃帧数。
6. `AnnotationReviewService`
   - 返回标准化 EventView。
   - 保存 event 编辑和 `review_status`。
7. `ExportService`
   - 只导出 `accepted` event。
   - 输出与现有 annotations JSON 兼容的结构。

## 5. 内存状态与并发

### 5.1 CurrentJob

```text
CurrentJob
├── job_id: str
├── file_name: str
├── file_names: list[str]
├── mcap_count: int
├── file_size_bytes: int
├── workspace_path: Path
├── mcap_path: Path
├── mcap_paths: list[Path]
├── segment_manifest: list[SegmentManifest]
├── robot_config_path: Path
├── status: JobStatus
├── stage: JobStage
├── progress: int
├── message: str
├── error: ErrorView | null
├── warnings: list[WarningView]
├── effective_config: dict
├── input_prompt: str
├── system_prompt: str
├── duration_sec: float | null
├── cameras: list[CameraInfo]
├── events: list[EventView]
├── data_anomaly_ranges: list[AnomalyRangeView]
├── image_anomaly_ranges: list[AnomalyRangeView]
├── vlm_completed_count: int
├── vlm_total_count: int
├── created_at: ISO-8601 string
└── updated_at: ISO-8601 string
```

### 5.2 多 MCAP 内部固定策略

以下策略只存在于后端规范和 `MULTI_MCAP_POLICY`，不通过 `/api/v1/config.pipeline_defaults` 返回，也不允许 `RunRequest` 覆盖：

- `fps=30`，使用整数帧号生成统一相对纳秒时间轴。
- `max_segment_count=32`。
- `continuous_gap_sec=0.066666667`。
- `max_video_fill_gap_sec=0.2`。
- `max_motion_interpolation_gap_sec=0.2`。
- `overlap_policy=earlier_mcap_wins`：按主相机记录时间戳排序后，重叠部分保留前一个 MCAP。
- `large_gap_policy=fail`：边界缺口达到视频补帧阈值时任务失败；较小缺口允许重复最近图像帧并对连续运动量插值。

上传写入仍采用 `.part` 加原子改名。预扫描、解析和视频编码位于独立工作进程；原始 MCAP 按实际时间顺序读取。工作进程完成全部 MP4 后，原子写入不含图像的 `parser_compact.pkl`，再正常退出。主进程随后加载紧凑结果，DataCheck 顺序解码 MP4，EventLabeling 根据事件帧索引精确读取 MP4；主进程从不接收 `image_list`。工作进程非零退出、被信号杀死或没有产生紧凑结果时，任务进入 `failed` 并保留 stderr 摘要。

`AnomalyRangeView.topics` 为单一 topic 字符串，`descs` 为字符串列表。DataCheck 当前只在同 topic、同异常类型内执行区间合并，禁止跨 topic 融合。异常复核状态属于前端临时展示状态，不通过后端保存，也不影响导出。

- `DemoJobService` 使用 `threading.RLock` 保护状态读写。
- API 返回状态前复制快照，不把可变对象直接暴露给响应序列化。
- 后台线程每次更新状态时同时更新 `updated_at`。
- 进度只能增加，重跑时先显式归零。

### 5.3 雪花 ID

- 工作项 ID 为 `job<雪花ID>`。
- 使用 41 位毫秒时间戳、10 位 worker ID 和 12 位毫秒内序列号。
- 生成器由进程级锁保护。
- 时钟回拨不超过 5 ms 时等待追平；超过 5 ms 时创建工作项失败并返回 `CLOCK_ROLLBACK`。
- 服务启动会清理旧工作目录，因此进程重启后即使出现理论上的 ID 重复，也不会关联到旧任务数据。

## 6. 本地文件

默认目录：

```text
/tmp/auto-labeling-demo/{job_id}/
├── input/
│   ├── segment_000.mcap
│   ├── segment_001.mcap
│   └── segment_NNN.mcap.part
├── metadata/
│   ├── segments.json
│   ├── frames.json
│   ├── artifact_worker_request.json
│   └── parser_compact.pkl
├── config/
│   ├── robot.json
│   └── effective_pipeline.json
├── videos/
│   ├── left_image.mp4
│   └── right_image.mp4
├── annotations/
│   ├── raw.json
│   └── reviewed.json
└── export/
    └── annotations.json
```

规则：

- 上传时每次读取 8 MiB，累计大小超过限制立即失败。
- 客户端文件名只用于页面展示；服务器将 MCAP 保存为 `segment_NNN.mcap`、配置保存为 `robot.json`，避免路径穿越。`NNN` 仅代表上传序号，解析和拼接顺序必须依据 MCAP 内主时间 topic 的时间戳。
- JSON 和标注文件先写入同目录临时文件，再使用 `os.replace()` 原子替换。
- 服务启动时清理 workspace root 下所有旧工作目录和 `.part` 文件，因为 MVP 不恢复任务。
- 正常关闭时尝试清理当前目录；清理失败只记录日志，不阻止进程退出。
- 创建新工作项时将旧的 `ready` 工作项加入最近成功历史，删除 `failed` 或未完成工作项；历史超过 5 条时删除最早成功工作项及目录。旧任务正在运行时返回 409。
- 导出文件通过 HTTP 下载到客户端，不额外复制到服务器其他目录。
- 创建工作项之前检查 workspace root 至少有 6 GiB 可用空间；不足时返回 507。

## 7. 工作项状态机

高层状态 `status`：

```text
validating → ready_to_run → running → ready
     │              │          │        │
     └──────────────┴──────────┴──────→ failed
ready/failed → validating（创建新工作项后替换）
```

运行阶段 `stage`：

```text
validating
ready_to_run
parsing
video_generating
data_checking
event_generating
vlm_labeling
saving
ready
failed
```

允许的操作：

| 状态 | 查询 | 启动/重跑 | 编辑 event | 导出 | 删除/覆盖 |
|---|---:|---:|---:|---:|---:|
| `validating` | 是 | 否 | 否 | 否 | 否 |
| `ready_to_run` | 是 | 是 | 否 | 否 | 是 |
| `running` | 是 | 否 | 否 | 否 | 否 |
| `ready` | 是 | 是，需前端确认 | 是 | 是 | 是 |
| `failed` | 是 | 是 | 否 | 否 | 是 |

MVP 不实现取消。运行中执行启动、删除或覆盖均返回 409。失败后可在同一工作项重跑；重跑会清理视频和标注产物，但保留上传文件和配置。

## 8. 进度计算

| 阶段 | 进度范围 | 计算方式 |
|---|---:|---|
| `validating` | 0–5 | 配置与文件校验完成后到 5 |
| `parsing` | 5–25 | 独立工作进程执行 Parser 和 MP4 编码，完成后到 25 |
| `video_generating` | 25–40 | 主进程登记工作进程生成的视频产物后进入 40 |
| `data_checking` | 40–60 | 进入时 40，完成时 60 |
| `event_generating` | 60–65 | 进入时 60，完成时 65 |
| `vlm_labeling` | 65–95 | 按已完成 event 数线性计算；无 event 直接到 95 |
| `saving` | 95–100 | 文件保存完成后到 100 |

视频生成完成后立即开放视频接口，前端可以在 DataCheck/VLM 仍运行时提前播放。

## 9. 视频规范

- 输入：独立工作进程内 Parser 返回的对齐 `image_list`；该对象不会通过进程边界返回主进程。
- 每个配置相机都生成一个文件，作为前端播放、图像质检和 VLM 抽帧的统一帧存储。
- 编码：H.264，像素格式 `yuv420p`，MP4 容器，30 FPS。
- 使用 PyAV `libx264` 编码；当前 vla_main 环境已具备该 codec，不依赖系统 `ffmpeg` 命令。
- 输出分辨率保持原图；宽或高为奇数时补齐到偶数。
- MP4 写入完成前使用 `.part` 后缀，关闭容器后原子改名。
- 编码帧的 PTS 等于零基 `frame_index`，time base 为 `1/30`。抽帧后必须校验解码器位置为 `frame_index + 1`，不得仅按播放秒数近似定位。
- `timestamp_list[frame_index]` 和 `frames.json` 保存帧索引对应的业务时间戳；MP4 本身只承担固定帧率图像存储。
- `main_time_topic` 对应相机是主视频；主视频失败则整个任务失败。
- 非主视频失败时任务继续，`CameraInfo.generation_status=failed` 并写入 warnings。
- 视频接口支持 `Range`、`206 Partial Content`、`Accept-Ranges: bytes` 和正确的 `Content-Length`。

## 10. 配置规范

### 10.1 默认值唯一来源

后端新增 `app/core/defaults.py`，作为算法默认值唯一来源。命令行、HTTP API 和文档示例均读取或对应这份默认配置。前端通过 `GET /api/v1/config` 获取默认值，不在前端代码中复制默认参数。

### 10.2 页面可覆盖配置

`POST /run` 接收以下结构：

```json
{
  "input_prompt": "这是一段机器人操作视频……",
  "system_prompt": "你是一位……具身智能数据标注专家……",
  "robot_config_overrides": {
    "main_time_topic": "/gripper/camera_fisheye_r/color/image_raw"
  },
  "parser_config": {
    "insert": {
      "max_tor_time_sec": 0.2
    }
  },
  "data_check_config": {
    "data_detection": {
      "sudden_change_config": {
        "window_time_sec": 0.5,
        "z_score": 5.0,
        "sudden_time_sec": 0.066666667,
        "step_time_sec": 0.5,
        "zcr_ratio": 0.4
      },
      "extreme_value_config": {
        "degree": 0.01,
        "expansion_coef": 0.2,
        "min_tor": 0.0001
      }
    },
    "image_detection": {
      "luminance": 10,
      "window_time_sec": 1.0,
      "lap_var": 150,
      "z_score": 5.0,
      "resize_length": 860,
      "resize_width": 640,
      "SSIM": 0.7,
      "pixel_mae": 5,
      "moving_area_ratio": 0.05
    },
    "merge_policy": {
      "min_low_quality_time_sec": 0.166666667,
      "max_gap_time_sec": 0.2
    }
  },
  "event_labeling_config": {
    "sampling": {
      "params": {
        "fixed_frame_len": 20,
        "sampling_frame_gap": 5,
        "context_frame_len": 2
      }
    }
  }
}
```

所有字段均可省略；后端递归合并默认值。`resize_length/resize_width` 以传入值为准。

`parser_config.insert.rotation` 当前没有被 Parser 消费，因此不在 HTTP schema 和页面中展示。后续实现相应算法后再开放，避免用户修改无效参数。

主要校验：

- 所有 `_time_sec`、`max_tor_time_sec`、`min_tor`：大于等于 0。
- `z_score`、`lap_var`、`pixel_mae`：大于等于 0。
- `degree`、`zcr_ratio`、`SSIM`、`moving_area_ratio`：0–1。
- `resize_length/resize_width`：整数，64–4096。
- `fixed_frame_len`：整数，1–20。
- `sampling_frame_gap`：整数，1–1000000；候选采样数超限时后端自动扩大有效间隔。
- `context_frame_len`：整数，0–10。
- `main_time_topic`：必须匹配上传 robot config 中某个 camera topic。
- `input_prompt`：去除首尾空白后最多 8000 个字符；空值使用后端默认 Prompt。
- `system_prompt`：去除首尾空白后最多 20000 个字符；空值使用后端默认 System Prompt。

固定不开放的策略仍由后端提供：`fps=30`、Savitzky-Golay 默认平滑、`clinear + Pelt + HSMM`、`pass_through + adjacent_by_topic`、VLM model 和输出层级。System Prompt 有后端默认值，但允许随运行请求覆盖。

## 11. API 通用规范

- Base URL：`/api/v1`。
- Content-Type：JSON 接口使用 `application/json; charset=utf-8`。
- 时间：页面 API 使用相对秒数 `number`，保留到最多 9 位小数。
- ID：字符串。
- 所有响应包含 `X-Request-ID`；客户端可传入该 header，否则后端生成 UUID。
- API 错误统一结构：

```json
{
  "error": {
    "code": "JOB_RUNNING",
    "message": "当前工作项正在运行，不能创建新工作项",
    "details": {},
    "request_id": "b970..."
  }
}
```

### 11.1 错误码

| HTTP | code | 场景 |
|---:|---|---|
| 400 | `INVALID_REQUEST` | 请求结构或 JSON 非法 |
| 400 | `INVALID_MCAP` | 文件为空、扩展名或 MCAP 内容非法 |
| 400 | `INVALID_ROBOT_CONFIG` | robot config 非法 |
| 404 | `JOB_NOT_FOUND` | 当前或指定工作项不存在 |
| 404 | `EVENT_NOT_FOUND` | event ID 不存在 |
| 409 | `JOB_RUNNING` | 运行中重复启动、覆盖或删除 |
| 409 | `INVALID_JOB_STATE` | 当前状态不允许该操作 |
| 413 | `UPLOAD_TOO_LARGE` | MCAP 合计超过 50 GiB |
| 422 | `CONFIG_VALIDATION_FAILED` | 算法参数越界 |
| 422 | `EVENT_VALIDATION_FAILED` | event 时间或字段非法 |
| 500 | `PIPELINE_FAILED` | 算法执行异常 |
| 502 | `VLM_UNAVAILABLE` | VLM 请求或响应失败 |
| 507 | `INSUFFICIENT_STORAGE` | 临时磁盘空间不足 |

日志同时输出到终端和 `AUTO_LABEL_LOG_PATH`，采用 20 MiB、5 个备份文件的轮转策略。必须包含：

- HTTP 请求的 `request_id`、method、path、status 和耗时。
- Pipeline 输入配置和实际 User Prompt。
- Parser、视频生成、DataCheck、EventGeneration、EventLabeling、结果保存及完整 Pipeline 的独立耗时。
- Parser 输出帧数/时长，DataCheck anomaly 与 trigger 摘要，EventGeneration 区间，VLM 帧角色/顺序、System Prompt、User Prompt 和解析后的输出。
- `job_id`、`camera_key`（适用时）、异常类型和堆栈。

图片 Base64 不写入日志，避免文件急剧膨胀。对外错误不返回服务器绝对路径、VLM 凭证或完整堆栈。

## 12. API 接口

### 12.0 健康检查

`GET /api/v1/health`

响应 `200`：

```json
{
  "status": "ok",
  "version": "0.1.0",
  "vlm_configured": true,
  "workspace_writable": true,
  "h264_encoder_available": true
}
```

健康检查不调用 VLM，只检查 endpoint 是否已配置、工作目录是否可写及 PyAV 是否存在 H.264 encoder。

### 12.1 获取配置与服务能力

`GET /api/v1/config`

响应 `200`：

```json
{
  "max_upload_bytes": 53687091200,
  "fps": 30,
  "default_input_prompt": "这是一段机器人操作视频……",
  "pipeline_defaults": {
    "parser_config": {},
    "data_check_config": {},
    "event_labeling_config": {}
  },
  "capabilities": {
    "multi_mcap": true,
    "cancel": false,
    "timeline_zoom": false
  }
}
```

`pipeline_defaults` 返回第 10 节的完整默认配置。

### 12.2 创建工作项并上传

`POST /api/v1/jobs`

请求：`multipart/form-data`

| 字段 | 类型 | 必需 | 规则 |
|---|---|---:|---|
| `mcaps` | file[] | 是 | 一个或多个 `.mcap`，合计最大 50 GiB；重复 multipart 字段 |
| `robot_config` | file | 是 | 单个 UTF-8 JSON，最大 2 MiB |

上传进度由浏览器根据请求已发送字节计算。接口在文件落盘并完成基础校验后返回。

响应 `201`：

```json
{
  "job_id": "job286972534784000000",
  "file_name": "train_data_1.mcap",
  "file_size_bytes": 123456789,
  "status": "ready_to_run",
  "stage": "validating",
  "progress": 5,
  "message": "上传和基础校验完成",
  "available_camera_topics": [
    {
      "camera_key": "right_image",
      "source_topic": "/gripper/camera_fisheye_r/color/image_raw"
    }
  ],
  "main_time_topic": "/gripper/camera_fisheye_r/color/image_raw",
  "created_at": "2026-07-14T10:00:00Z",
  "updated_at": "2026-07-14T10:00:03Z"
}
```

`POST /api/v1/jobs/new` 会立即创建 `draft` 工作项并返回 Job ID。当前工作项为 `ready` 时先归入最近成功历史；为未完成状态时清理。当前工作项为 `running` 时返回 409。随后上传接口携带 `draft_job_id`，文件上传和校验复用该 Job ID。历史超过 5 条时清理最早完成项。

### 12.3 获取当前工作项

`GET /api/v1/jobs/current`

- 有当前工作项：返回 `200 JobSummary`。
- 没有当前工作项：返回 404 `JOB_NOT_FOUND`。

### 12.3.1 获取最近成功工作项

`GET /api/v1/jobs/history`

- 返回按完成时间从新到旧排列的 `JobSummary[]`，最多 5 条。
- 仅包含成功完成的工作项；当前项若已成功，也包含在列表中且不会重复。
- 记录仅在当前后端进程内有效，服务重启后不恢复。
- 历史工作项允许读取结果、修改复核状态和导出，但调用运行接口返回 409。

### 12.4 获取指定工作项状态

`GET /api/v1/jobs/{job_id}`

响应 `200`：

```json
{
  "job_id": "job286972534784000000",
  "file_name": "train_data_1.mcap",
  "file_size_bytes": 123456789,
  "status": "running",
  "stage": "vlm_labeling",
  "progress": 78,
  "message": "已完成 5/9 个 event",
  "duration_sec": 20.4,
  "camera_count": 2,
  "event_count": 9,
  "pending_event_count": 9,
  "accepted_event_count": 0,
  "rejected_event_count": 0,
  "vlm_completed_count": 5,
  "vlm_total_count": 9,
  "warnings": [],
  "error": null,
  "created_at": "2026-07-14T10:00:00Z",
  "updated_at": "2026-07-14T10:02:00Z"
}
```

### 12.5 启动或重跑

`POST /api/v1/jobs/{job_id}/run`

请求：第 10.2 节 JSON。响应 `202`：

```json
{
  "job_id": "job286972534784000000",
  "status": "running",
  "stage": "parsing",
  "progress": 5,
  "message": "自动标注已启动"
}
```

接口只提交后台线程，不等待算法完成。`ready_to_run`、`ready` 和 `failed` 可启动；后两者重跑前清理派生产物和人工复核结果。

### 12.6 查询结果

`GET /api/v1/jobs/{job_id}/result`

运行期间也可调用；视频完成后会先返回 cameras，events 在标注完成前为空。

响应 `200`：

```json
{
  "job_id": "job286972534784000000",
  "duration_sec": 20.4,
  "main_camera_key": "right_image",
  "cameras": [
    {
      "camera_key": "right_image",
      "source_topic": "/gripper/camera_fisheye_r/color/image_raw",
      "video_url": "/api/v1/jobs/job286972534784000000/videos/right_image",
      "duration_sec": 20.4,
      "is_main_camera": true,
      "generation_status": "ready",
      "error": null
    }
  ],
  "events": [],
  "data_anomaly_ranges": [],
  "image_anomaly_ranges": []
}
```

### 12.7 EventView

EventView 使用前端友好的统一时间字段，不直接暴露 annotations JSON 中的重复别名：

```json
{
  "id": "seg_1783934175584",
  "topic_key": "right_image",
  "source_topic": "/gripper/camera_fisheye_r/color/image_raw",
  "start_sec": 5.667916693,
  "end_sec": 6.619081630,
  "prompt": "将蓝色物体放入格子中",
  "description": "机器人……",
  "baseline_camera_key": "right_image",
  "action_state": 1,
  "review_status": "pending"
}
```

- `start_sec/end_sec` 为主时间轴相对秒数。
- 编辑时间时，后端吸附到最近的主时间轴帧，并重新计算 annotations JSON 的绝对时间和相对时间别名。
- API 保持字段名 `prompt`；前端标签显示为“动作摘要”。不在 MVP 中改名，避免破坏现有输出兼容性。

AnomalyRangeView 统一为：

```json
{
  "anomaly_code": "image_blur",
  "anomaly_name": "图像模糊",
  "start_sec": 3.2,
  "end_sec": 3.8,
  "topics": ["right_image"],
  "descs": ["right_image 在该区间清晰度低于阈值"]
}
```

- `start_sec/end_sec` 为 `number`，满足 `0 <= start_sec <= end_sec <= duration_sec`。
- `topics/descs` 始终为数组；没有描述时返回空数组，不返回 null。

### 12.8 修改 event

`PATCH /api/v1/jobs/{job_id}/events/{event_id}`

请求字段均可选，但至少提供一个：

```json
{
  "start_sec": 5.7,
  "end_sec": 6.7,
  "prompt": "拿起蓝色物体",
  "description": "机器人从垫子上拿起蓝色物体",
  "action_state": 1,
  "review_status": "accepted"
}
```

校验：

- `0 <= start_sec < end_sec <= duration_sec`。
- `action_state` 只能是 `-1/0/1`。
- `review_status` 只能是 `pending/accepted/rejected`。
- `prompt` 最多 500 字符，`description` 最多 4000 字符。

响应 `200` 返回保存后的 EventView。每次成功修改后原子重写 `annotations/reviewed.json`。

### 12.9 获取视频

`GET /api/v1/jobs/{job_id}/videos/{camera_key}`

- 完整请求返回 `200 video/mp4`。
- Range 请求返回 `206 video/mp4`。
- 视频尚未完成返回 409 `INVALID_JOB_STATE`。
- camera 不存在或生成失败返回 404。

### 12.10 导出

`GET /api/v1/jobs/{job_id}/export`

- 即时生成或刷新 `export/annotations.json`。
- 只包含 `review_status=accepted` 的 event。
- 保留 `review_status` 字段。
- 没有 accepted event 时返回包含空 `response` 的合法 JSON。
- 响应 header：`Content-Disposition: attachment; filename="<原文件名>.annotations.json"`。

### 12.11 删除工作项

`DELETE /api/v1/jobs/{job_id}`

- `ready_to_run`、`ready`、`failed`：清理内存和工作目录，返回 `204`。
- `running`：返回 409；MVP 不取消正在运行的算法。

## 13. 异常与降级

- robot config JSON 非法或必需 topic 缺失：创建工作项失败或运行进入 `failed`。
- 主视频生成失败：任务进入 `failed`。
- 非主视频生成失败：记录 warning，继续算法和页面展示。
- VLM 单次请求失败：对应 Event 写入失败标注并继续；只有 EventLabeling 自身结构错误或结果保存失败才使任务进入 `failed`。
- VLM 返回非 2xx HTTP 状态时，日志记录状态码、reason、Content-Type、图片数、请求体字节数、耗时和最多 20000 字符的错误响应正文；正文超限时记录截断标记。
- 无 event：任务正常进入 `ready`，events 为空，视频仍可播放。
- `.part`、视频或 JSON 写入失败：进入 `failed` 并记录磁盘错误。
- 上传过程中客户端断线：清理 `.part`；客户端重新完整上传。
- 服务重启：启动清理旧目录，`GET /jobs/current` 返回 404，前端清除 session。

## 14. 实现顺序

1. FastAPI 应用、配置、错误模型和 `/health`。
2. 当前工作项、workspace 和 multipart 上传。
3. 后台 Runner、进度状态和现有算法调用。
4. PyAV 视频生成和 Range 视频接口。
5. 结果标准化、event PATCH 和 reviewed JSON。
6. 导出和前端静态文件托管。
7. API 单元测试及 `tests/train_data_1.mcap` 端到端测试。

## 15. 验收标准

1. 局域网其他主机可以打开 Demo 页面。
2. 可以上传 `tests/train_data_1.mcap` 和 `tests/train_data_1.json`。
3. 不依赖数据库或 S3 即可运行现有自动标注算法。
4. 页面能看到阶段、进度、视频、event 和异常区间。
5. 每个 event 初始包含 `review_status=pending`。
6. event 可编辑、接受、舍弃和恢复。
7. accepted event 可导出为兼容 JSON。
8. 上传、参数、算法、VLM、视频和磁盘错误均返回规范化错误。
9. 后端 API 单元测试通过，并使用测试 MCAP 完成一次端到端演示。

## 16. 更新记录

- 2026-07-16：EventLabeling 的 VLM 默认参数增加 `store: false`，关闭 LM Studio `/api/v1/chat` 的 prediction history 存储。
- 2026-07-16：EventLabeling 页面参数新增 `sampling_frame_gap`，当前默认 5；`fixed_frame_len` 明确为 event 图片数量上限。
- 2026-07-16：`RunRequest` 新增可选 `system_prompt`，最多 20000 字符；空值使用后端默认值，并与 User Prompt 一同写入有效配置后传给 EventLabeling。
- 2026-07-15：新增最近 5 个成功工作项历史接口；历史项保留视频、标注、异常和复核能力，但禁止重新运行。
- 2026-07-15：VLM HTTP 错误日志新增响应正文、状态码、内容类型、请求规模和截断标记，便于定位服务端 500。
- 2026-07-15：根据本地 VLM 长响应情况，将单次 HTTP 请求超时默认值由 120 秒延长至 300 秒。
- 2026-07-15：新增多 MCAP 上传和时间戳预扫描合并；内部固定边界策略不暴露给页面，上传顺序仅保留用于展示和完全同时间范围时的确定性仲裁。
- 2026-07-15：异常区间接口的 `topics` 调整为单一字符串；明确后端不保存数据/图像异常的临时复核状态，导出逻辑保持不变。
- 2026-07-14：补完可运行 Demo 技术选型、状态机、进度、视频、文件、错误和完整 HTTP API 规范，作为第一版实施基线。
- 2026-07-14：确定前后端同机部署、其他主机通过 HTTP 访问；输入改为文件上传到本地临时目录，EventLabeler 输出增加 `review_status=pending`。
- 2026-07-14：调整为单 MCAP 轻量 Demo，移除数据库、S3、批次和多工作项设计。
- 2026-07-07：统一文档格式。
