# 自动标注模块开发需求

## 文档状态

- 文档类型：项目级开发需求
- 当前阶段：MVP 设计
- MVP 范围：以本地 MCAP 文件为输入，串行跑通核心算法流程并输出最终事件标注 JSON
- 默认测试输入：`tests/train_data_1.mcap`
- 默认测试机器人配置：`tests/train_data_1.json`

## 开发目标

构建一个机器人 VLA 项目数据自动质检与标注核心算法服务，用于 VLA 训练数据的清洗和准备。

MVP 阶段先聚焦本地 MCAP 文件输入，不考虑远程下载、前端交互、数据库任务管理和并发调度。核心流程需要完成解析、质量/trigger 检测、事件节点与区间生成、事件标注，最终输出可供训练数据清洗、人工复核和后续数据转换使用的标注 JSON。

参数约定：所有持续时间、窗口时长、间隔和容差配置统一使用秒，并以 `_sec` 结尾；内部固定按 `fps=30` 换算为对齐帧数。必须继续以离散帧表达的参数，名称中包含 `frame`，例如 `window_frame_length`、`jump_frames`、`fixed_frame_len` 和 `context_frame_len`。

第一版目标如下：

1. 输入解析：解析视频、图像和机器人日志数据，统一成后续模块可消费的结构化时间序列。
2. Topic 配置输入：MVP 阶段不开发 Topic 检测模块，camera、state、action 等 topic 信息由外部请求或配置显式输入。
3. 数据质检：检测机器人数据和图像质量异常；同时从事件相关 topic 中检测客观变化 trigger，分别输出异常记录和 `trigger_points`。
4. 事件生成与切分：基于策略将 trigger 筛选为事件节点；按摄像头 topic 分组，并在各 topic 内将相邻节点配对为 `event_periods`，禁止跨 topic 配对。
5. 事件标注：每个 `event_period` 只采样所属摄像头并单独调用 VLM；event 图片最多 20 张，另加区间前后上下文，生成最终标注 JSON。

## 非目标

MVP 阶段暂不要求：

- 后端 API、数据库任务状态管理、并发调度和任务抢占。
- 远程文件下载、对象存储上传下载和多文件格式完整覆盖。
- 性能优化和大规模吞吐指标。
- 前端页面和人工标注平台交互闭环。
- 自动修复低质量数据。
- Topic 检测模块开发；MVP 阶段 topic 信息必须由外部请求或配置输入。

## 执行流程

1. 输入解析阶段：读取本地 MCAP 文件，对视频、图像和机器人数据进行解析、排序、同步和对齐，生成后续处理需要的数据结构。
2. Topic 配置输入阶段：从外部请求或配置中读取 camera、state、action 等 topic 信息；MVP 阶段不根据机器人数据自动推理 topic。
3. 数据质检阶段：检测并融合机器人数据及图像质量异常；使用 topic 相关检测器输出独立的 trigger 候选点。
4. 事件生成阶段：使用可替换策略将 trigger 筛选为事件节点，并生成标准事件区间。
5. 事件标注阶段：消费事件区间，对图像采样后调用 VLM，生成事件标签、描述和最终标注 JSON。

## 并发与过程控制

第一版不要求后端任务系统和并发控制，全流程以本地函数或命令行方式串行跑通即可。

后续引入并发控制时，由服务编排层生成运行时字段，例如 `job_id`、子任务 ID、重试次数、任务状态等，并将这些字段注入各模块请求。

## 配置层级说明

核心算法服务入口配置是全流程编排层配置，包含全局字段和各模块字段。

模块级配置与服务级配置可能结构相似，但二者层级不同：

- 服务级配置描述一次完整本地自动标注任务的请求。
- 模块级配置描述单个模块实际运行时需要的请求。
- 编排层负责将全局字段、运行时生成字段和模块配置组合成模块请求。

例如 `task_id` 属于全局字段，需要被多个模块复用；未来的 `job_id` 可能由服务在请求到达后生成，也不应强行写入某个单独模块配置内部。

## 服务入口配置

核心算法服务入口配置为 JSON 格式，基本结构如下：

```json
{
  "basic_config": {
    "task_id": "自动生成或外部传入的任务 ID"
  },
  "parser_config": {},
  "topic_config": {
    "camera": {},
    "state": [],
    "action": []
  },
  "data_check_config": {},
  "event_generation_config": {},
  "event_labeling_config": {}
}
```

字段说明：

- `basic_config`：全流程共享字段，例如 `task_id`，未来可扩展 `job_id`、运行环境、用户信息等。
- `parser_config`：输入解析模块配置。
- `topic_config`：外部输入的 topic 配置，MVP 阶段由请求方显式提供，不由服务自动检测生成。
- `data_check_config`：数据质检模块配置。
- `event_generation_config`：事件生成模块配置。
- `event_labeling_config`：事件标注模块配置。

## 模块请求组装原则

模块执行时，编排层按以下原则组装请求：

1. 全局字段来自 `basic_config`。
2. 模块私有字段来自对应模块配置。
3. Topic 信息来自外部输入的 `topic_config`，由编排层注入 Parser、数据质检、事件生成等需要 topic 的模块请求。
4. 运行时字段由编排层生成，例如 `job_id`、本地 MCAP 路径、临时输出目录等。
5. 模块之间只通过明确的结构化产物传递数据，不直接读取彼此内部状态。

Parser 模块请求示例：

```json
{
  "basic": {
    "task_id": "TASK-001",
    "job_id": "JOB-001"
  },
  "parser": {},
  "align": {},
  "insert": {},
  "output_format": {}
}
```

## 模块文档

- 后端设计文档：`doc/BackendDoc.md`
- 前端设计文档：`doc/Frontend.md`
- 输入解析模块：`doc/ParserModuleDoc.md`
- Topic 检测模块：`doc/TopicDetectionModuleDoc.md`（MVP 阶段不开发，topic 信息由外部输入）
- 数据质检模块：`doc/DataCheckModuleDoc.md`
- 事件生成模块：`doc/EventGenerationModuleDoc.md`
- 事件标注模块：`doc/EventLabelingModuleDoc.md`

## 项目文件架构小结

当前仓库处于设计文档阶段，实际代码开发时建议按“编排层 + 业务模块 + 外部集成 + 数据 schema”的方式组织文件。MVP 阶段优先实现后端串行流程和核心业务模块；Topic 检测模块、前端模块和更完整的后端任务系统先保留目录位置，不作为 MVP 必须开发内容。

建议项目结构如下：

```text
auto-labeling-demo/
  app/
    main.py
    api/
      routes/
        labeling_tasks.py
    core/
      config.py
      exceptions.py
      logging.py
    schemas/
      common.py
      parser.py
      data_check.py
      event_generation.py
      event_labeling.py
      topic_detection.py
    services/
      orchestrator.py
      vlm_client.py
    modules/
      parser/
        base.py
        mcap_parser.py
        aligner.py
        inserter.py
      data_check/
        base.py
        data_detectors.py
        image_detectors.py
        merger.py
      event_generation/
        base.py
        anomaly_generator.py
      event_labeling/
        range_generator.py
        sampler.py
        vlm_labeler.py
      topic_detection/
        README.md
    frontend/
      README.md
  tests/
    fixtures/
    test_parser.py
    test_data_check.py
    test_event_generation.py
    test_event_labeling.py
  schemas/
    request_examples/
    response_examples/
  doc/
```

目录关系说明：

- `app/main.py`：核心算法服务入口。MVP 可提供本地命令行或函数调用入口，传入本地 MCAP 路径并输出最终标注 JSON。
- `app/api/`：后端接口层预留位置，负责后续接收任务请求、返回任务状态和结果。MVP 阶段不要求实现。
- `app/core/`：通用基础设施，包括配置、异常类型、日志格式和运行时常量。
- `app/schemas/`：请求、响应和模块中间产物的数据结构定义。时间戳字段对外统一使用字符串格式。
- `app/services/orchestrator.py`：核心算法编排层，负责按 Parser、DataCheck、EventGeneration、EventLabeling 的顺序组装请求并串行调用模块。
- `app/services/vlm_client.py`：VLM 服务调用封装，避免事件标注模块直接依赖具体 HTTP 接口。
- `app/modules/parser/`：输入解析模块，对应 `doc/ParserModuleDoc.md`。
- `app/modules/data_check/`：数据质检模块，对应 `doc/DataCheckModuleDoc.md`。
- `app/modules/event_generation/`：事件生成模块，对应 `doc/EventGenerationModuleDoc.md`。
- `app/modules/event_labeling/`：事件标注模块，对应 `doc/EventLabelingModuleDoc.md`。
- `app/modules/topic_detection/`：Topic 检测模块预留位置。MVP 阶段不开发具体算法，只保留 README 或接口占位；topic 信息由 `topic_config` 外部输入。
- `app/frontend/`：前端模块预留位置。MVP 阶段可以不开发完整前端；未来用于任务创建、配置展示、质检结果查看和事件标注复核。
- `tests/`：模块测试和端到端流程测试。MVP 默认使用本地 `tests/train_data_1.mcap` 和 `tests/train_data_1.json` 作为测试样例；大体积 MCAP 文件不进入 Git，测试数据应通过本地路径、下载脚本或小型 fixture 管理。
- `schemas/`：跨服务对接示例和稳定 JSON schema，可存放外部请求与输出示例，便于前后端、外部服务和测试复用。

MVP 阶段建议优先落地的代码路径：

1. `app/core/`
2. `app/schemas/`
3. `app/modules/parser/`
4. `app/modules/data_check/`
5. `app/modules/event_generation/`
6. `app/modules/event_labeling/`
7. `app/services/orchestrator.py`
8. `tests/`

后续阶段再补充 `app/modules/topic_detection/`、`app/api/` 的完整任务系统、`app/frontend/`、远程文件下载和更完整的存储/并发控制能力。


### 运行时输入路径原则

`tests/train_data_1.mcap` 和 `tests/train_data_1.json` 只作为 MVP 默认测试样例。核心算法服务实现时不得将 MCAP 路径、机器人配置 JSON 路径、topic 名称或机器人型号写死在代码中。服务入口需要通过请求参数、命令行参数或配置对象接收：

- `mcap_path`：本次待处理的本地 MCAP 文件路径。
- `robot_config_path`：本次待处理 MCAP 对应的机器人/topic 配置 JSON 路径。
- `output_path`：最终标注 JSON 的输出路径，可选；未传入时由编排层生成默认输出位置。

解析模块和下游模块只能消费编排层读取后的结构化配置，不直接依赖固定测试文件名。后续新增不同型号机器人时，应通过新增配置 JSON 适配，不修改核心算法流程代码。

## 代码说明与注释规则

MVP 阶段开始实现代码后，所有脚本代码必须遵守以下说明规则：

1. 所有函数和类都必须添加说明。Python 代码中优先使用 docstring，说明该函数/类的用途、关键输入、关键输出和可能抛出的主要异常。
2. 对外暴露的入口函数、模块基类、工厂类、数据 schema 和核心算法类需要写完整说明；内部小工具函数也至少需要一句说明其职责。
3. 当脚本用于实现某个特定功能或完整流程时，需要在代码中用注释说明该功能的实现步骤，例如“读取配置 -> 解析 MCAP -> 时间轴对齐 -> 数据质检 -> 事件生成 -> 事件标注 -> 输出 JSON”。
4. 注释应解释业务意图、算法步骤、边界条件和非显然的实现原因，不写无信息量的重复性注释。
5. 复杂算法或多阶段处理逻辑需要在关键代码块前添加步骤注释，保证后续开发者可以按注释理解处理顺序和数据流向。
6. 当实现与文档设计存在差异时，必须在代码注释或 docstring 中说明原因，并优先同步更新对应设计文档。

## MVP 验收标准

MVP 阶段以“可重复跑通完整流程”为验收目标。

必须满足：

1. 服务入口可以接收本地 MCAP 路径和机器人配置 JSON 路径；使用默认测试样例 `tests/train_data_1.mcap` / `tests/train_data_1.json` 可以完成一次核心算法流程。
2. Parser 模块能识别配置中的 camera、state、action topic。
3. Parser 模块能输出按主时间轴对齐的 `timestamp`、`image`、`state`、`action` 数据。
4. 输出 timestamp 单调递增。
5. 必需 topic 未在外部配置中提供时失败；可选 topic 缺失时按模块配置处理。
6. 数据质检模块能分别输出结构化异常记录和 `trigger_points`，即使没有结果也要输出结构完整的空集合。
7. 事件生成模块能将 trigger 转换为按摄像头 topic 分组的 `event_points` 和 `event_periods`；每个 topic 的 N 个节点生成 N−1 个区间。
8. 事件标注模块能消费 `event_periods`，输出每个事件的标签和描述字段，并生成最终标注 JSON。
9. 核心算法流程运行失败时能定位到失败模块、文件、topic 和错误原因。

## 技术栈与依赖

### 核心算法 MVP 必需依赖

```text
python==3.11.15
pytest>=8.0.0
av==17.1.0
opencv-python==4.13.0.92
numpy==2.4.6
pyarrow>=17.0.0
datasets>=3.6.0
Pillow>=12.0.0
mcap==1.3.1
mcap-ros2-support==0.5.7
lz4==4.4.5
zstandard==0.25.0
json-repair==0.61.2
ruptures==1.1.10
scipy==1.17.1
```

### 后续后端/存储能力预留依赖

以下依赖不属于本地 MCAP 到最终标注 JSON 的 MVP 必需范围，后续开发后端 API、数据库任务管理、对象存储、认证鉴权时再启用。

```text
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
sqlalchemy>=2.0.0
psycopg[binary]>=3.2.0
alembic>=1.13.0
pydantic-settings>=2.4.0
python-multipart>=0.0.9
minio>=7.2.0
snowflake-id>=1.0.2
bcrypt>=5.0.0
PyJWT>=2.13.0
```

## 环境配置

本地开发/测试虚拟环境为 `/home/carlos-ssd/venvs/vla-main`，使用 `source /home/carlos-ssd/venvs/vla-main/bin/activate` 启用虚拟环境。

核心算法测试默认使用 `tests/train_data_1.mcap` 和对应机器人/topic 配置；Demo 服务实际运行时可上传 1–32 个属于同一连续任务的 MCAP。文件在本地临时目录处理，不考虑 S3/MinIO 下载或数据库状态记录；后端仅在进程内保存一个当前工作项和最近 5 个成功结果。VLM 调用配置以 `doc/EventLabelingModuleDoc.md` 中的 `vlm_params` 和服务接口为准；服务入口至少需要能传入或读取 `model`、`system_prompt`、`input_prompt`、VLM 地址和超时时间。

## 更新记录

- 2026-07-15：Demo 支持多 MCAP 时间戳排序、对齐拼接和固定帧率视频；加入最近 5 次成功结果切换，并将单 Event VLM 异常调整为失败标注后继续执行。
- 2026-07-13：明确 DataCheck 输出 trigger 检测事实、EventGeneration 负责节点筛选与区间配对、EventLabeling 负责采样和 VLM 标注的模块边界。
- 2026-07-10：新增代码说明与注释规则，要求所有函数/类添加说明，并为脚本流程补充步骤注释。
- 2026-07-10：根据 MVP 范围澄清，将开发目标收敛为本地 MCAP 输入到最终标注 JSON 输出的核心算法服务。
- 2026-07-10：补充项目文件架构小结，明确 MVP 模块、Topic 检测预留位置和未来前后端目录关系。
- 2026-07-09：明确 MVP 阶段不开发 Topic 检测模块，topic 信息由外部请求或配置输入。
- 2026-07-07：补充配置层级说明、模块请求组装原则和 MVP 验收标准。
