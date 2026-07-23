# 自动质检与标注服务技术说明

## 1. 文档目的

本文面向共同开发和部署本项目的工程人员，说明系统边界、运行链路、配置管理、关键中间产物以及修改代码时必须保持的约定。具体算法公式仍以各模块文档为准。

## 2. 系统组成

```text
Browser
  │ multipart MCAP + robot config / JSON overrides
  ▼
FastAPI (backend/)
  ├─ JobService：单任务状态、上传、进度、导出
  ├─ artifact_worker：MCAP 解析与视频物化子进程
  └─ pipeline：DataCheck → EventGeneration → EventLabeling
        │
        └─ HTTP VLM service

Vue (frontend/)
  └─ 创建任务、显示进度、多视频复核、编辑和导出
```

核心算法也可通过 `python -m app.main` 脱离 Web 运行。

## 3. 一次任务的数据流

1. 客户端上传 1～32 个连续任务 MCAP 和一个 RobotConfig。
2. 后端按 8 MiB 分块写入 `.part`，`fsync` 后原子改名。
3. Parser 根据 MCAP 内主时间 topic 排序分段，处理重叠和间隙。
4. Artifact worker 生成各摄像头 30 FPS MP4，并返回不含图像字节的紧凑 parser 信息。
5. DataCheck 顺序读取视频，检查数值、图像质量并生成 trigger。
6. EventGeneration 按 trigger topic 分组，将相邻 trigger 配成事件区间。
7. EventLabeling 从事件所属视频按索引采样 JPEG，逐事件调用 VLM。
8. 后端保存原始/复核结果，页面提供视频复核和 JSON 导出。

工作目录结构：

```text
<workspace_root>/<job_id>/
├── input/          segment_NNN.mcap
├── config/         robot.json、effective_pipeline.json
├── videos/         camera_key.mp4
├── metadata/       segment/frame manifest、紧凑 parser 结果
├── annotations/    raw.json、reviewed.json
└── export/         导出文件
```

所有大型中间文件先写 `.part`，成功后原子替换。模块间传递路径和紧凑 metadata，不传递完整视频帧列表。

## 4. 配置管理

### 4.1 唯一默认源

[`config/defaults.toml`](../config/defaults.toml) 是所有可选参数默认值的唯一事实来源：

- `[service]`：HTTP、工作目录、上传与日志限制。
- `[multi_mcap]`：固定帧率、分段数、重叠与间隙策略。
- `[pipeline.parser]`：对齐与输出。
- `[pipeline.data_check]`：数值、图像、trigger 和异常合并。
- `[pipeline.event_generation]`：trigger 到 event 的策略。
- `[pipeline.event_labeling]`：采样、VLM 和输出 schema。

`app/core/defaults.py` 只负责加载、版本检查和深拷贝，不应再次定义数值默认值。模块中的 `.get(key, fallback)` 只能作为对不完整内部调用的防御；其 fallback 必须与 TOML 一致，新增公开参数时应优先从已合并配置中强制读取。

### 4.2 配置优先级

从低到高：

```text
config/defaults.toml
→ .env 中的部署覆盖
→ 已存在的系统环境变量
→ API/CLI 的本次任务覆盖
→ 编排层生成的运行时字段
```

算法 JSON 使用递归合并：只覆盖调用方给出的叶子字段。运行时路径、上传文件和 parser 产物不允许被算法 override 覆盖。

### 4.3 必须外部传入的值

这些值由任务或部署决定，不应进入默认文件：

| 参数 | 来源 | 原因 |
|---|---|---|
| MCAP 路径/文件 | API 上传或 CLI | 每个任务不同 |
| RobotConfig | API 上传或 CLI | 定义机器人和 topic schema |
| `main_time_topic` | RobotConfig，可由任务覆盖 | 数据集相关 |
| `task_id`/`job_id` | 外部或编排层 | 运行时身份 |
| 输出路径 | CLI 或工作区生成 | 运行时位置 |
| VLM endpoint | 环境变量或 CLI | 部署相关且可能包含网络信息 |
| 密钥、数据库/S3 凭据 | 环境或密钥服务 | 不得提交 Git |

### 4.4 部署环境变量

环境变量仅覆盖 `[service]` 或提供外部集成地址。完整清单见 `.env.example`。新增变量时必须同步：

1. `backend/settings.py`
2. `.env.example`
3. README 的配置说明
4. 本文档（如果它改变配置边界）

## 5. 关键参数解释

TOML 内每个字段都有行内注释，以下只强调容易误用的组合：

- `multi_mcap.fps` 与 `pipeline.data_check.basic.fps` 当前都为 30；前者决定物化时间轴，后者负责秒到帧的换算，两者必须保持一致。
- `window_frame_length`、`jump_frames`、`fixed_frame_len`、`sampling_frame_gap`、`context_frame_len` 的单位是帧。
- 名称以 `_sec` 结尾的窗口、间隔和容差单位均为秒。
- `pose7d` 对外格式是 `[x,y,z,qx,qy,qz,qw]`；当前实现使用四元数 SLERP，`insert.rotation="ZYX"` 是兼容字段，不改变输出顺序。
- `fixed_frame_len=20` 是事件帧上限，前后上下文帧另计。
- `AUTO_LABEL_VLM_TIMEOUT_SEC` 是单个事件请求的超时，不是整个任务超时。

## 6. 外部接口

主要 API：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/health` | 服务、工作区和编码器健康状态 |
| GET | `/api/v1/config` | 前端需要的可编辑默认配置 |
| POST | `/api/v1/jobs/new` | 创建空白工作项 |
| POST | `/api/v1/jobs` | 上传 MCAP 与 RobotConfig |
| POST | `/api/v1/jobs/{id}/run` | 合并覆盖并启动流水线 |
| GET | `/api/v1/jobs/{id}` | 查询阶段和进度 |
| GET | `/api/v1/jobs/{id}/result` | 获取复核视图 |
| PATCH | `/api/v1/jobs/{id}/events/{event_id}` | 修改事件 |
| GET | `/api/v1/jobs/{id}/export` | 导出复核结果 |

准确请求/响应以 FastAPI `/docs` 和 `doc/BackendDoc.md` 为准。

## 7. 开发约定

- 所有命令从仓库根目录运行。
- 不硬编码测试数据、topic、机器人型号、IP 或开发机路径。
- 公共时间戳纳秒值使用字符串，内部计算使用整数。
- 修改默认参数必须修改 TOML，并增加或调整测试。
- 修改模块输入输出必须同步对应模块文档和前后端类型。
- 文件修改应保留用户工作区中不相关的未提交改动。
- 大型 MCAP 不进入 Git；测试优先使用小 fixture 或显式本地路径。

## 8. 验证与排障

后端：

```bash
pytest tests/unit -q
```

前端：

```bash
npm run build --prefix frontend
```

配置加载：

```bash
python -c "from app.core.defaults import load_defaults; print(load_defaults()['schema_version'])"
```

若整机卡死或服务突然消失，先检查：

```bash
journalctl -k -b | grep -i oom
```

Parser、DataCheck 和视频/VLM 都可能产生较高资源压力。定位时使用日志中的 `job_id` 和 module timing，区分上传、解析、质检、事件生成和事件标注阶段。
