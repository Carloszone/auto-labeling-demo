# 自动标注 Demo 前端页面描述清单

## 1. 页面基本信息

- 页面名称：自动标注 Demo
- 页面使用者：VLA 平台用户、自动标注功能体验用户
- 页面目标：快速处理一个 MCAP，将多摄像头视频、自动标注 event 和异常区间可视化，并支持简单人工复核和 JSON 导出
- MVP 范围：单 MCAP、单工作项、HTTP 上传、串行处理、本地临时文件、内存状态，不使用数据库和 S3
- 部署方式：前端和后端部署在同一台服务主机，其他主机通过 HTTP 地址访问页面、上传文件并启动自动标注
- 工作项命名：`job<雪花 ID>`，即字符串 `job` 与雪花 ID 直接拼接
- 覆盖规则：页面只保留当前工作项；创建新工作项前清理或覆盖上一个工作项

后续可扩展多 MCAP：一次输入的多个 MCAP 被视为同一连续动作的若干片段，属于同一个工作项。该能力不属于当前 MVP，实施前另行设计排序、连续性校验、视频拼接和标注时间偏移规则。

## 2. 页面结构参考

页面由两个主要展示区域和一个操作区构成：

1. 顶部操作与进度区。
2. 视频与时间轴区域。
3. Event 标注信息与复核区域。

参考文件：

- 整体页面结构：`frontend/docs/PageStructureExample.png`
- 多摄像头视频布局：`frontend/docs/VideoPlayExample.png`

布局约束：

- 目标分辨率：1920×1080。
- 页面区域不提供折叠功能。
- 当前不要求移动端适配。
- 顶部操作区高度 112 px。
- 主体左侧视频与时间轴约占 65%（1248 px），右侧 Event 复核约占 35%（672 px）。
- 左侧视频区约高 640 px，播放控制和时间轴约高 328 px。

## 3. 页面输入

### 3.1 MCAP 输入

其他主机通过浏览器访问 Demo 页面，因此必须上传 MCAP 文件内容，不能传递客户端本地绝对路径。页面使用文件选择控件，并显示文件名、文件大小、上传进度和校验状态。

- 单次只允许上传一个 MCAP。
- 单文件上限为 5 GB。
- 前端通过 HTTP 将文件上传到后端。
- 后端流式写入本机临时目录，禁止把整个文件一次性读入内存。
- 上传过程中使用 `.part` 临时文件；上传和基础校验成功后再原子改名。
- MVP 不要求断点续传；上传中断后删除不完整文件，用户重新上传。

MVP 使用单次 `multipart/form-data` 请求，同时提交 `mcap` 和 `robot_config` 两个文件；算法配置和 VLM Prompt 在后续启动请求中提交。

multipart 字段固定为 `mcap` 和 `robot_config`，接口、响应和错误结构见 `doc/BackendDoc.md`。MVP 由 Uvicorn 直接提供服务，不配置反向代理；后端限制 MCAP 为 5 GiB、robot config 为 2 MiB，并在创建工作项前要求至少 6 GiB 可用临时磁盘空间。

### 3.2 Robot/Topic 配置

用户同时上传 robot config JSON 文件。格式参考 `tests/train_data_1.json`，主要包含：

- `schema_version`
- `robot_type`、`robot_name`、`description`
- `main_time_topic`
- `cameras`
- `observation_state`
- `action`
- `full_annotation.playback`

后端负责校验 JSON、topic 配置和 `main_time_topic`，并将该配置传给 Parser。

robot config 与 MCAP 在同一个 `multipart/form-data` 请求中上传。

### 3.3 VLM 输入 Prompt

- 页面提供文本输入框。
- 输入框常驻显示在顶部操作区，而不是隐藏在参数弹窗中；标签明确说明它对应每次 VLM 请求的 `input[0].content`。
- 默认值来自 `tests/example_prompt.json` 的 `user_prompt`。
- 用户未填写时使用默认值。
- VLM 输入 Prompt 与 event 中的动作摘要 `prompt` 是两个不同字段。
- 页面提供只读展开项展示当前固定的 System Prompt，便于核对 context/event 角色约束。

默认示例：

```text
这是一段机器人操作视频的关键帧序列，请描述机器人完成的任务，使用中文描述其任务。
可选的任务动作有：拿起/移动/放下。
描述动作时需要保留以下信息：动作、交互物件、地点信息。
描述示例：准备执行 XX 动作/从桌上拿起瓶子/移动瓶子/将瓶子放入格子中。
```

### 3.4 页面可编辑配置

页面配置弹窗只展示允许修改的字段。字段路径以服务入口配置为准，后端再组装为各模块请求。

| 配置字段 | 默认值 |
|---|---:|
| `robot_config.main_time_topic` | 必填，无默认值 |
| `parser_config.insert.max_tor_time_sec` | `0.2` |
| `data_check_config.data_detection.sudden_change_config.window_time_sec` | `0.5` |
| `data_check_config.data_detection.sudden_change_config.z_score` | `3` |
| `data_check_config.data_detection.sudden_change_config.sudden_time_sec` | `0.066666667` |
| `data_check_config.data_detection.sudden_change_config.step_time_sec` | `0.5` |
| `data_check_config.data_detection.sudden_change_config.zcr_ratio` | `0.4` |
| `data_check_config.data_detection.extreme_value_config.degree` | `0.01` |
| `data_check_config.data_detection.extreme_value_config.expansion_coef` | `0.2` |
| `data_check_config.data_detection.extreme_value_config.min_tor` | `1e-4` |
| `data_check_config.image_detection.luminance` | `10` |
| `data_check_config.image_detection.window_time_sec` | `1.0` |
| `data_check_config.image_detection.lap_var` | `150` |
| `data_check_config.image_detection.z_score` | `2` |
| `data_check_config.image_detection.resize_length` | `860` |
| `data_check_config.image_detection.resize_width` | `640` |
| `data_check_config.image_detection.SSIM` | `0.7` |
| `data_check_config.image_detection.pixel_mae` | `5` |
| `data_check_config.image_detection.moving_area_ratio` | `0.05` |
| `data_check_config.merge_policy.min_low_quality_time_sec` | `0.166666667` |
| `data_check_config.merge_policy.max_gap_time_sec` | `0.2` |
| `event_labeling_config.sampling.params.fixed_frame_len` | `20` |
| `event_labeling_config.sampling.params.context_frame_len` | `2` |

当前 `AutoLabelingService.run()` 已支持传入 `parser_config`、`data_check_config`、`event_generation_config` 和 `event_labeling_config`。服务使用递归合并策略：传入字段覆盖默认值，未传字段继续使用默认值。命令行可通过 `--pipeline-config-path` 传入这些配置段。

页面通过 `POST /api/v1/jobs/{job_id}/run` 提交配置覆盖值。字段范围、请求 schema 和模块映射以 `doc/BackendDoc.md` 第 10、12 节为准。

图像 resize 默认值统一为 860×640；如果请求显式传入 `resize_length` 或 `resize_width`，以传入值为准。

### 3.5 页面不展示的固定配置

- Parser：文件类型、对齐方法、输出格式开关。
- DataCheck：`eps=1e-9`、`fps=30`、共享 Savitzky-Golay 平滑参数及各检测器 enable 开关。
- TriggerDetection：`end_effector + clinear + Pelt` 及 HSMM 默认参数。
- EventGeneration：`pass_through + adjacent_by_topic`。
- EventLabeling：`fixed_sequence`、VLM model、System Prompt、`layerId=l2`。

固定值由后端 `app/core/defaults.py` 统一提供。前端通过 `GET /api/v1/config` 获取默认值，不在页面代码中维护另一套默认参数。

## 4. 页面展示内容

### 4.1 当前工作项与处理进度

- 展示工作项 ID、MCAP 文件名、文件大小、上传状态和上传进度。
- 展示当前状态、处理阶段、百分比、提示信息和错误摘要。
- 处理阶段包括：校验输入、解析 MCAP、生成视频、数据质检、事件生成、VLM 标注和保存结果。
- 进度仅按当前单 MCAP 工作流计算。
- 页面不提供历史工作项列表和工作项切换。

阶段权重固定为：校验 0–5%、解析 5–25%、视频 25–40%、数据质检 40–60%、事件生成 60–65%、VLM 65–95%、保存 95–100%。VLM 阶段按已完成 event 数计算。

### 4.2 视频播放区域

- 同时展示当前 MCAP 中所有配置为输出视频的摄像头。
- 多个视频共用播放、暂停和相对时间进度。
- 每个视频显示 `camera_key` 和原始 camera topic。
- 支持拖动共享进度条定位时间。
- 支持逐帧前进和后退，按 30 FPS 计算，每次移动约 `1/30` 秒。
- 点击 event 卡片时，所有视频跳转到该 event 起始时间。
- 如果视频 metadata 尚未加载完成，页面先保存待跳转时间，并在 `loadedmetadata` 后再次应用。
- 视频仅按正常速度播放，不提供变速、全屏和单视角放大。
- 多摄像头时长不一致时，以 `main_time_topic` 对应视频为主时间轴。

后端使用 PyAV/libx264 生成 H.264、`yuv420p`、30 FPS MP4，保存到 `{workspace_root}/{job_id}/videos/{camera_key}.mp4`，通过支持 HTTP Range 的视频接口访问。主视频驱动播放；非主视频提前结束时保持最后一帧。

### 4.3 时间轴与标注轨道

- 视频播放进度轨道。
- 一级标注轨道：仅保留占位，不提供合并功能。
- 二级标注轨道：展示 event 区间。
- 三级标注轨道：展示数据和图像异常区间。
- 所有轨道共享播放时间游标。
- 当前不提供时间轴缩放和滚动。
- 标注区间颜色透明度为 40%，重叠区域通过颜色叠加加深。
- Event 复核区的 camera 筛选与二级 Event 轨道联动；选择一个或多个 `baseline_camera_key` 后，轨道只显示这些 camera 的 Event，避免不同来源区间重叠干扰判断。
- 播放游标与所有区间都基于扣除左侧轨道名称后的同一个内容宽度计算，保证相同秒数落在相同横坐标。

颜色规范：pending 黄色、accepted 绿色、rejected 淡红色、数据异常紫色、图像异常青色，透明度统一为 40%；播放游标使用蓝色。

### 4.4 Event 信息

页面展示：

- event 总数。
- 起止时间，使用相对时间，单位秒。
- `topic_key` 和 `source_topic`。
- `prompt`：event 动作摘要，不是 VLM 输入 Prompt。
- `description`。
- `baseline_camera_key`。
- `action_state`。
- `review_status`：`pending | accepted | rejected`。

`review_status` 是自动标注 event 的顶层属性，初始值由 EventLabeler 写为 `pending`。它不是算法参数，而是人工复核状态：

- `pending`：尚未人工确认，作为新生成 event 的默认状态。
- `accepted`：人工确认保留，进入默认导出结果。
- `rejected`：人工确认舍弃，仍在复核页面保留，但不进入默认导出结果。

它与 `action_state` 不同：`action_state` 描述机器人动作成功、失败或无法判断；`review_status` 描述人工是否接受这条标注。人工修改后的状态保存在 `reviewed.json`，默认导出只包含 `accepted` event。

`review_status` 通过 event PATCH 接口修改，并原子保存到 `reviewed.json`。API 字段继续使用 `prompt` 保持输出兼容，页面标签显示为“动作摘要”。

## 5. 用户可执行操作

### 5.1 文件与任务

- 输入 MCAP 和 robot config。
- 校验文件路径和配置。
- 编辑允许开放的算法参数。
- 输入或修改 VLM Prompt。
- 启动自动标注。
- 创建新工作项并覆盖当前工作项。

### 5.2 视频

- 播放或暂停。
- 拖动共享进度条。
- 逐帧前进或后退。
- 点击 event 后跳转到起始时间。

### 5.3 Event 复核

右侧区域以标签页呈现 Event、数据异常和图像异常复核。Event 播放命中时自动高亮并将卡片滚动到列表中央，用户点击的卡片保留选中高亮。两类异常页按单一 topic 筛选，展示起止时间、`anomaly_name`、`descs`，接受/舍弃/恢复待审状态仅在页面内生效，不参与导出。

- 按 `baseline_camera_key` 多选筛选 event。
- 编辑 event 起止时间、动作摘要、`description` 和 `action_state`。
- 显式保存修改。
- 接受或舍弃 event。
- 将已舍弃 event 恢复为 `pending`。
- 被舍弃 event 仍保留在当前工作区，但导出时不包含。

存在未保存修改时，跳转其他 event、刷新页面、上传新任务或重跑均弹出二次确认。文本和时间编辑显式保存；接受、舍弃和恢复待审立即保存。

### 5.4 导出

- 导出当前工作项中所有 `accepted` event。
- 输出为 JSON，结构参考 `tests/train_data_1.annotations.json`。
- 没有已接受 event 时提示用户，可选择导出空 `response` 或取消。

## 6. MVP 操作流程

1. 用户在其他主机的浏览器中打开 Demo HTTP 地址。
2. 用户选择单个 MCAP 和 robot config，前端通过 HTTP 上传文件内容。
3. 后端生成 `job<雪花 ID>`、创建临时工作目录，并将上传内容流式写入 `input/`。
4. 上传完成后，后端校验文件格式、配置内容和必需 topic。
5. 用户确认页面参数和 VLM Prompt，启动自动标注。
6. 后端串行执行解析、视频生成、数据质检、事件生成、VLM 标注和结果落盘。
7. 前端每 1–2 秒查询当前工作项状态。
8. 视频生成后，前端显示多摄像头播放器；标注尚未完成时允许时间轴为空。
9. 标注完成后，前端加载 event、异常区间和视频元数据。
10. 用户编辑、接受或舍弃 event，并显式保存。
11. 用户导出当前已接受的标注 JSON。

视频生成完成后立即允许播放，不等待 DataCheck、VLM 和标注保存全部完成。

## 7. 实时更新信息

- 当前工作项状态、阶段、百分比和提示。
- 每个摄像头的视频生成状态。
- VLM 已完成 event 数量和 event 总数。
- 自动生成的 event 和异常区间。
- event 保存、复核和导出状态。

MVP 使用 1–2 秒轮询，不要求 WebSocket、SSE 或分布式任务系统。

后端 `JobSummary` 返回阶段、进度、VLM 完成数、warning 和错误。前台页面每 1 秒轮询，后台标签页每 5 秒轮询；连续失败时按 2、4、8、15 秒退避。

## 8. 数据和文件保存边界

### 8.1 内存状态

- 当前工作项 ID、状态、阶段、进度和错误。
- 当前生效配置和 VLM Prompt。
- 当前 event、异常区间和复核状态。

服务重启后内存状态丢失，MVP 不恢复未完成工作项。

### 8.2 本地临时工作目录

```text
{workspace_root}/{job_id}/
├── input/source.mcap
├── config/robot.json
├── videos/{camera_key}.mp4
├── annotations/raw.json
├── annotations/reviewed.json
└── export/annotations.json
```

- 上传的原始 MCAP 保存在当前工作项目录的 `input/` 下。
- 视频、原始标注、复核后标注和导出文件保存在当前工作项目录。
- 创建新工作项时清理旧目录；服务正常退出时可以清理当前临时目录。
- 不使用数据库和 S3，不提供历史记录。

`workspace_root` 默认 `/tmp/auto-labeling-demo`，通过 `AUTO_LABEL_WORKSPACE_ROOT` 修改。服务启动、正常退出和创建新工作项时清理旧目录；清理失败记录日志。导出文件由浏览器下载，不复制到服务器其他目录。

### 8.3 前端会话状态

- 当前工作项 ID。
- 当前播放时间和筛选条件。
- 尚未提交的 event 编辑内容。

前端使用 `sessionStorage` 保存当前工作项 ID。页面刷新时查询 `/api/v1/jobs/current`；后端重启或任务不存在时清除失效 ID。

## 9. 异常与空状态

### 9.1 输入与配置

- 未提供 MCAP 或 robot config：禁用启动按钮并提示。
- 路径不存在、后端无读取权限、文件为空或不是 MCAP：拒绝启动。
- robot config 不是合法 JSON、必需字段缺失或必需 topic 不存在：展示明确错误。
- 上传文件名必须清理路径字符，后端生成安全的实际存储文件名。
- 上传文件超过 5 GB、连接中断或临时磁盘空间不足时，任务上传失败并清理 `.part` 文件。

后端校验 `.mcap` 扩展名及 MCAP 可读性，robot config 必须是 UTF-8 JSON。客户端文件名只用于显示，服务器固定保存为 `source.mcap` 和 `robot.json`；临时目录创建前检查至少 6 GiB 可用空间。

### 9.2 处理

- 运行中禁用重复启动。
- 创建新工作项时若当前任务仍在运行，必须先提示并确认。
- MCAP 解析、视频生成或 VLM 调用失败：状态变为 `failed`，展示失败阶段和错误摘要。
- 单个摄像头视频生成失败：显示 camera topic。
- 没有 event：展示空状态，视频仍可播放。

主摄像头视频失败时任务失败；非主摄像头失败时记录 warning 并继续。MVP 不支持取消正在运行的算法，运行期间禁止覆盖、删除和重复启动。

### 9.3 编辑与导出

- event 时间满足 `0 <= start < end <= main_video_duration_sec`。
- 保存失败时保留用户输入并允许重试。
- 导出失败时展示错误并允许重试。
- 被拒绝的 event 不进入导出结果。

### 9.4 页面恢复

- 页面刷新后可根据当前工作项 ID 重新查询内存状态。
- 后端已重启或工作项已被清理时，页面回到未创建任务状态。

## 10. 当前 Demo 暂不实现

- 多 MCAP 拼接和标注合并。
- 历史工作项列表和工作项切换。
- 数据库和 S3 持久化。
- 通过浏览器提交客户端本地绝对路径。
- 多任务并发、任务队列和服务重启恢复。
- 一级 event 合并和基于异常区间裁减 event。
- 多人协作、编辑冲突、完整修改历史和撤销/重做。
- 登录、权限和审批流程。
- 时间轴缩放、滚动、视频变速和移动端适配。

## 11. 第一版实施基线

- 后端：FastAPI + Uvicorn 单 worker + 后台单线程。
- 前端：Vue 3 + TypeScript + Vite + Element Plus + Pinia + Vue Router + Axios，与现有 VLA 前端技术栈保持一致。
- 上传：单次 multipart，MCAP 最大 5 GiB，不断点续传。
- 视频：PyAV/libx264 H.264 MP4，支持 HTTP Range。
- 状态：内存单工作项，1 秒/5 秒轮询。
- 存储：本地临时目录，不使用数据库和 S3。
- 网络：监听 `0.0.0.0:8000`，FastAPI 同源托管前端，仅供可信局域网使用。
- 配置：后端默认值为唯一来源，resize 默认 860×640，显式传入值优先。
- 复核：event 初始 pending；编辑显式保存，复核状态即时保存；只导出 accepted。
- 详细 API、状态码、错误模型和字段规范以 `doc/BackendDoc.md` 为准。

## 12. 更新记录

- 2026-07-14：按通用轻量方案补完全部前后端决策，与 BackendDoc/FrontendDoc 的第一版可运行设计对齐。
- 2026-07-14：确定前后端同机部署并向其他主机提供 HTTP 页面；MCAP 和 robot config 改为上传到后端本地临时目录，自动生成 event 增加 `review_status=pending`。
- 2026-07-14：根据单 MCAP 轻量 Demo 方案重构页面清单；移除 MVP 中的数据库、S3、批次和多工作项设计，校正配置字段层级并明确实施边界。
