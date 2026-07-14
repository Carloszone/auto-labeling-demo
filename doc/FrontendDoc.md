# 前端功能模块设计文档

## 1. 文档状态与目标

- 文档类型：可实施的 Demo 前端设计
- 所属流程：自动标注可视化与人工复核
- 当前阶段：MVP 实施基线
- 核心目标：让不了解算法实现的用户通过一个页面完成上传、配置、运行、查看和导出

前端只服务当前单工作项，不实现历史任务、数据库页面或生产级管理能力。

## 2. 技术栈

- Vue 3 + TypeScript，使用 Composition API 和 `<script setup>`。
- Vite 负责开发和构建。
- Element Plus 提供上传、表单、按钮、进度、提示和抽屉等基础组件。
- Axios 作为 API client，并使用 `onUploadProgress` 展示大文件上传进度。
- Pinia 管理工作项、配置、结果、轮询和页面共享状态。
- Vue `ref/reactive/computed` 管理组件内部的播放、筛选和未保存编辑状态。
- Vue Router 只保留单页 Demo 路由，并为后续嵌入现有平台预留路由入口。
- 原生 `<video>` 播放 MP4。
- 原生 SVG 实现时间轴，不引入重型时间轴库。

选择 SVG 时间轴是因为 MVP 不需要缩放、拖拽裁剪和复杂编辑。区间只需按 `start_sec/duration_sec` 换算为百分比位置即可。

## 3. 构建与部署

开发环境：

- Vite 监听 `0.0.0.0:5173`。
- `/api` 代理到 `http://127.0.0.1:8000`。
- 开发环境通过代理保持同源调用习惯。

演示环境：

- `npm run build` 输出到 `frontend/dist`。
- FastAPI 托管 `frontend/dist`。
- 用户访问 `http://<server-ip>:8000`。
- API 和页面同源，不需要 CORS 配置。

前端只面向可信局域网，暂不实现登录。页面顶部显示“内部 Demo，请勿上传敏感数据”。

## 4. 页面布局

目标分辨率 1920×1080，不做移动端适配。

```text
┌──────────────────────────────────────────────────────────────┐
│ 顶部操作区：上传、配置、Prompt、运行、进度、导出（112 px） │
├──────────────────────────────────────┬───────────────────────┤
│ 视频区（约 1248×640）                │ Event 复核区（672 px）│
│                                      │                       │
├──────────────────────────────────────┤                       │
│ 播放控制与时间轴（约 1248×328）      │                       │
└──────────────────────────────────────┴───────────────────────┘
```

- 左侧宽度约 65%，右侧约 35%。
- 视频网格按相机数自动布局：1 个全宽、2 个双列、3–4 个 2×2。
- Event 复核区独立纵向滚动。
- 页面区域不折叠。

## 5. 颜色规范

| 内容 | 颜色 | 说明 |
|---|---|---|
| 主色/播放游标 | `#409eff` | Element Plus 默认蓝 |
| Event pending | `rgba(250, 173, 20, 0.40)` | 黄色 |
| Event accepted | `rgba(82, 196, 26, 0.40)` | 绿色 |
| Event rejected | `rgba(255, 77, 79, 0.28)` | 红色，降低强调 |
| 数据异常 | `rgba(114, 46, 209, 0.40)` | 紫色 |
| 图像异常 | `rgba(19, 194, 194, 0.40)` | 青色 |
| 错误 | `#ff4d4f` | 红色 |
| warning | `#faad14` | 黄色 |

区间重叠时 SVG 直接叠加半透明矩形，重叠区域自然加深。

## 6. 组件结构

```text
AutoLabelingDemoPage
├── DemoNotice
├── JobToolbar
│   ├── FileUploadPanel
│   ├── ConfigDrawerButton
│   ├── PromptEditor
│   ├── RunButton
│   ├── JobProgress
│   └── ExportButton
├── Workspace
│   ├── MediaWorkspace
│   │   ├── MultiCameraPlayer
│   │   │   └── CameraPlayer[]
│   │   ├── PlaybackControls
│   │   └── AnnotationTimeline
│   │       ├── PlaybackTrack
│   │       ├── L1PlaceholderTrack
│   │       ├── EventTrack
│   │       └── AnomalyTrack[]
│   └── EventReviewPanel
│       ├── EventSummary
│       ├── CameraFilter
│       └── EventCard[]
└── ErrorDetailsModal
```

## 7. 页面状态

### 7.1 服务端状态

由 Pinia store 管理：

- `configStore`：后端默认配置和能力。
- `jobStore`：当前工作项概要、上传、启动、轮询和删除。
- `resultStore`：视频、event、异常区间、复核和导出。

每个异步请求在发出时记录 `job_id`。响应返回后只有在 ID 与当前工作项一致时才能写入 store。创建新工作项后取消旧请求并清空旧结果，避免过期响应覆盖新任务。

### 7.2 本地状态

- `selectedMcap`、`selectedRobotConfig`。
- `uploadProgress`、`uploadStatus`。
- `pipelineOverrides`、`inputPrompt`。
- `currentTimeSec`、`isPlaying`。
- `selectedCameraKeys`。
- `editingEventId`、`eventDraft`、`hasUnsavedChanges`。
- `uploadAbortController`。

重置规则：

- 创建新工作项成功后重置视频、筛选、event 草稿和导出状态。
- job ID 变化时取消所有旧 job 请求。
- 后端返回 `JOB_NOT_FOUND` 时删除 sessionStorage 中的 job ID并回到初始页面。

## 8. 上传设计

### 8.1 文件选择

- MCAP：只允许一个 `.mcap`，前端上限 5 GiB。
- robot config：只允许一个 `.json`，前端上限 2 MiB。
- 两个文件均选择后启用“上传并创建工作项”。
- 文件选择使用 Element Plus `el-upload` 的手动模式，不自动发请求。

### 8.2 请求

前端构造一个 `FormData`：

```text
mcap=<File>
robot_config=<File>
```

调用 `POST /api/v1/jobs`。Axios `onUploadProgress` 使用 `loaded/total` 计算 0–100%。

### 8.3 上传交互

- 上传期间禁用文件重新选择、运行和导出。
- 提供“取消上传”按钮，使用 AbortController 中止 HTTP 请求。
- 用户取消、网络中断或超时后显示“上传失败，请重新上传”。MVP 从 0 重新上传。
- HTTP 上传请求不设置前端短超时；使用 `timeout: 0`，由服务端和网络环境控制。
- 后端返回 413 时显示“MCAP 超过 5 GiB”。
- 上传成功后显示 job ID、文件名、大小和 `ready_to_run` 状态。

## 9. 配置与 Prompt

### 9.1 默认值

页面加载时请求 `GET /api/v1/config`。所有表单默认值来自后端响应，前端不维护另一套算法默认常量。

### 9.2 配置抽屉

使用 Element Plus `el-drawer` + `el-form`。字段控件：

- `main_time_topic`：`el-select`，选项来自上传 robot config 的 `cameras[].topic`；上传成功后后端也返回可选 camera topics。
- 整数和浮点数：`el-input-number`，按字段显示范围、步长和单位。
- Prompt：`el-input type="textarea"`，最多 8000 字符。

字段和范围以 `BackendDoc.md` 第 10 节为准。`rotation` 不展示，因为当前 Parser 未消费该参数。

操作：

- “应用”：只更新前端覆盖值，不立即运行。
- “恢复默认”：恢复 `GET /config` 返回值。
- “取消”：丢弃本次未应用的配置编辑。

### 9.3 启动请求

上传和基础校验完成后，用户检查配置和 Prompt，再点击“开始自动标注”。页面调用：

```text
POST /api/v1/jobs/{job_id}/run
```

采用明确的两步流程，不在上传完成后自动运行，便于用户确认 `main_time_topic` 和算法参数。

## 10. 进度与轮询

- `ready_to_run`：不轮询或每 10 秒确认一次。
- `running` 且页面可见：每 1 秒轮询 JobSummary。
- `running` 且页面在后台：每 5 秒轮询。
- `ready/failed`：停止状态轮询。
- 视频阶段完成后请求 `/result`，即使标注尚未完成也显示已生成视频。
- `ready` 后刷新完整结果。

连续请求失败策略：

1. 前两次按原频率重试。
2. 第 3 次开始显示“连接不稳定”。
3. 使用 2、4、8、15 秒退避，最大 15 秒。
4. 任意一次成功后恢复正常轮询。

所有请求使用 AbortController。组件卸载、job ID 变化或发起新请求时取消旧请求。

## 11. 视频播放

### 11.1 加载

- `<video preload="metadata">`。
- URL 来自 `CameraInfo.video_url`。
- 后端支持 Range，浏览器可以拖动和按需加载。
- 视频生成中显示 Skeleton。
- 主视频失败显示阻塞错误；非主视频失败显示独立 warning 卡片。

### 11.2 同步

- `main_camera_key` 对应视频是唯一播放时钟。
- 主视频 `timeupdate` 更新 `currentTimeSec` 和时间轴游标。
- 播放/暂停同时作用于所有可用视频。
- 每 250 ms 检查非主视频误差；误差大于 0.1 秒时设置其 `currentTime=currentTimeSec`。
- 非主视频长度不足或播放结束时保持最后一帧，不阻塞主视频。
- 用户拖动时先暂停所有视频，设置所有视频时间，再按拖动前状态决定是否恢复播放。

### 11.3 逐帧

- 逐帧前进/后退前先暂停。
- 每次移动 `1/30` 秒。
- 结果限制在 `[0, duration_sec]`。

## 12. 时间轴

- 时间轴宽度使用 ResizeObserver 获取。
- 区间位置：`x = start_sec / duration_sec * width`。
- 区间宽度：`(end_sec - start_sec) / duration_sec * width`，最小显示宽度 2 px。
- 一级轨道显示灰色占位和“暂未实现”。
- 二级轨道按 review_status 使用第 5 节颜色。
- 数据异常和图像异常使用独立轨道。
- 点击 event 区间选中相应卡片并跳转到起点。
- 当前版本不支持缩放、横向滚动、拖拽改变边界和区间裁剪。

## 13. Event 复核

### 13.1 展示

- 顶部显示总数及 pending/accepted/rejected 数量。
- `baseline_camera_key` 使用 Checkbox Group 多选筛选，默认全选。
- 卡片显示时间、topic、动作摘要、action_state、review_status 和 description。
- 字段名 `prompt` 在 UI 上显示为“动作摘要”，API 字段保持 `prompt`。

### 13.2 编辑

允许编辑：

- `start_sec`、`end_sec`
- `prompt`
- `description`
- `action_state`

文本和时间编辑采用显式保存：

- 点击“编辑”进入草稿状态。
- 点击“保存”调用 PATCH。
- 点击“取消”恢复服务端值。
- 切换 event、刷新页面、上传新任务或重跑前，如有未保存修改，使用 `ElMessageBox.confirm` 二次确认。

### 13.3 复核状态

- “接受”“舍弃”“恢复待审”按钮立即调用 PATCH，只提交 `review_status`。
- 请求期间禁用当前卡片状态按钮。
- 失败时恢复原状态并显示错误。
- `rejected` event 仍在列表和时间轴中显示，但默认导出不包含。

## 14. 会话恢复

- 使用 `sessionStorage` 保存 `current_job_id`。
- 页面加载时先请求 `/api/v1/jobs/current`。
- 返回同一 job 时恢复状态和结果。
- 返回 404 时清除 sessionStorage，显示初始页面。
- 服务重启不恢复任务，这是预期行为。
- 播放时间、筛选条件和未保存草稿不跨刷新恢复。

## 15. API Client

```text
getConfig()
createJob(formData, onUploadProgress, signal)
getCurrentJob(signal)
getJob(jobId, signal)
runJob(jobId, request)
getJobResult(jobId, signal)
updateEvent(jobId, eventId, patch)
getVideoUrl(jobId, cameraKey)
downloadExport(jobId)
deleteJob(jobId)
```

- JSON 请求默认超时 30 秒。
- 上传请求不设置前端超时。
- 视频由 `<video>` 直接访问，不通过 Axios 下载 Blob。
- 错误拦截器解析统一 `error.code/message/request_id`。
- 用户提示显示 message；“查看详情”弹窗显示 code 和 request_id，方便定位日志。

## 16. 异常和空状态

| 场景 | 页面行为 |
|---|---|
| 未选择两个文件 | 禁用上传按钮 |
| MCAP 超过 5 GiB | 文件选择后立即提示并移除 |
| 上传中断 | 保留文件选择，允许重新上传 |
| robot config 非法 | 显示后端字段错误 |
| 当前任务运行中 | 禁用上传、重跑和删除 |
| 主视频失败 | 视频区显示阻塞错误 |
| 非主视频失败 | 对应位置显示 warning，其他视频继续 |
| VLM/算法失败 | 进度区显示失败阶段、错误和 request ID |
| 没有 event | 显示“未生成事件”，视频仍可播放 |
| event 保存失败 | 保留草稿并允许重试 |
| 没有 accepted event | 导出前确认；确认后下载空 response JSON |
| 后端重启 | 清除当前 job，回到上传状态 |

成功提示使用 `ElMessage`，3 秒自动消失；阻塞错误使用 `el-alert` 或 `el-result` 常驻显示；诊断信息可以一键复制。

## 17. 与后端接口映射

| 页面动作 | API |
|---|---|
| 加载默认配置 | `GET /api/v1/config` |
| 上传并创建 | `POST /api/v1/jobs` |
| 恢复当前任务 | `GET /api/v1/jobs/current` |
| 查询进度 | `GET /api/v1/jobs/{job_id}` |
| 启动/重跑 | `POST /api/v1/jobs/{job_id}/run` |
| 加载视频和标注 | `GET /api/v1/jobs/{job_id}/result` |
| 编辑/复核 event | `PATCH /api/v1/jobs/{job_id}/events/{event_id}` |
| 播放视频 | `GET /api/v1/jobs/{job_id}/videos/{camera_key}` |
| 导出 | `GET /api/v1/jobs/{job_id}/export` |
| 删除 | `DELETE /api/v1/jobs/{job_id}` |

## 18. 实现顺序

1. Vite/Vue 3/TypeScript 工程、API client、Pinia 和页面框架。
2. 双文件上传、上传进度和 JobSummary。
3. 配置抽屉、Prompt 和启动流程。
4. 轮询、进度、错误和会话恢复。
5. 多视频播放、同步和逐帧。
6. SVG 时间轴和 event 列表。
7. event 编辑、复核和导出。
8. 使用测试 MCAP 完成浏览器端到端验证。

## 19. 验收标准

1. 其他主机可以通过 HTTP 打开页面。
2. 可以上传单个 MCAP 和 robot config，并看到上传进度。
3. 可以查看和覆盖后端提供的算法参数及 Prompt。
4. 可以启动算法并查看阶段和进度。
5. 视频生成后可提前播放，多相机基本同步。
6. 可以播放、暂停、拖动和逐帧。
7. 可以查看 event、数据异常和图像异常区间。
8. 可以筛选、编辑、接受、舍弃和恢复 event。
9. 可以下载 accepted event JSON。
10. 主要失败场景都有明确反馈和 request ID。

## 20. 更新记录

- 2026-07-14：技术栈调整为与现有 VLA 前端一致的 Vue 3、TypeScript、Vite、Element Plus、Pinia、Vue Router 和 Axios。
- 2026-07-14：补完布局、上传、配置、轮询、视频同步、时间轴、复核、会话和 API 映射，作为第一版实施基线。
- 2026-07-14：确定前后端同机部署并通过 HTTP 向其他主机提供页面；输入改为文件上传。
- 2026-07-14：调整为单 MCAP、本地临时文件和单工作项页面。
- 2026-07-07：统一文档格式。
