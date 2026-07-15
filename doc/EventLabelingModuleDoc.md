# 事件标注模块设计文档

## 文档状态

- 文档类型：模块设计文档
- 所属流程：事件标注阶段
- 当前阶段：MVP 设计
- MVP 范围：基于前序模块的输出，实现对事件的标注和描述，并输出

## 功能描述

事件标注模块负责将事件片段中的视频图像和上下文信息传入 VLM 模型，结合 prompt 输出事件标签和自然语言描述。

MVP 阶段只负责消费事件生成模块已经构造好的事件区间，完成固定长度采样、VLM 调用和标注格式化；节点筛选与事件区间配对不属于本模块职责。

## 输入

本模块的输入有三个来源：
1. 来自文档解析模块的"timestamp_list", "image_list"
2. 来自事件生成模块的 `event_periods`
3. 来源于外部请求的参数信息。具体接受的参数见`接口定义`部分


## 输出

事件标注模块输出结构化事件标注结果。MVP 阶段最终对外输出字段为 `response`，其值为事件标注列表。

```text
{
    "task_id": "TASK-001",
    "job_id": "JOB-001",
    "response": [
        {
            "id": "seg_1782290078574",
            "startSec": "6.120",
            "startTimeNs": "1776150112442719000",
            "endSec": "8.362",
            "endTimeNs": "1776150114684113300",
            "topic_key": "right_image",
            "source_topic": "/gripper/camera_fisheye_r/color/image_raw",
            "prompt": "pick the bottle",
            "layerId": "l2",
            "category": "detail",
            "attributes": {
                "scene": "tabletop",
                "sceneTags": []
            },
            "description": "pick the blue bottle on the table",
            "baseline_camera_key": "right_image",
            "action_state": 1,
            "review_status": "pending"
        }
    ]
}
```

- `response`：事件标注结果列表。每个元素对应一个事件片段，包含片段时间信息、动作标签、描述信息、动作状态和初始人工复核状态。
- `task_id` / `job_id`：透传请求中的任务标识，方便服务编排层关联上下游结果。
- 输出文件或存储位置：MVP 阶段不强制定义独立文件格式，由服务编排层将该字典对象作为事件标注阶段结果传递给调用方；如需要落盘，可存储为 JSON 文件或任务结果表中的结构化字段。
- 下游模块消费方式：前端或人工复核模块读取 `response`，展示每个事件片段的时间范围、动作摘要、动作状态和自然语言描述。若没有可标注事件片段，返回空列表 `response: []`。

## 架构设计
本模块采用工厂模式加抽象基类的整体架构。

核心抽象：  
- `EventSamplingBase`: 事件采样基类。不同的采样规则实现不同的采样实例
- `EventLabelingBase`: 事件标注基类。不同的事件标注策略实现不同生成器实例


第一版实现：
- `FixedSequence`: 固定长度的采样实例
- `VLMLabelingGenerator`: 调用VLM模型获取事件的标注信息

## 接口定义
### 模块请求接口
以下示例为伪代码，其中`parse_input`和`generation_input`分别代表文档解析模块和事件生成模块的输出对象。
```text
{
    "basic": {
        "task_id": "TASK-001",
        "job_id": "JOB-001",
        "parser_info": parse_input,
        "generation_info": generation_input
    },
    "sampling": {
        "mode": "fixed_sequence",
        "params": {
            "fixed_frame_len": 20,
            "context_frame_len": 2
        }
    },
    "vlm_params": {
        "model": "qwen/qwen3.5-9b",
        "system_prompt": """
        [Role]
        你是一位拥有丰富视觉几何与机器人控制背景的专家。你的任务是分析机器人操作的视频的关键帧序列，提取动作意图和空间细节，并判断动作是否成功。

        [Goal]
        生成简洁的动作摘要（Action Summary）。
        判断动作操作是否成功(action_state)。
        提供详尽的场景描述（Detailed Description），包括空间位置、对象状态和交互关系。

        [Constraint]
        空间严谨性：描述位置时使用“左/中/右”、“前方/后方”、“上/下”等方位词，必须以机器人视角为基准。
        动作专注性：仅描述机器人与末端工具（EOAT）相关的操作，忽略不相关的背景人员走动和行为。
        描述粒度：对于场景中的多个同类对象，必须区分颜色、形状或位置特征，避免模糊称呼。
        遮挡处理：“即使当机器人遮挡了物体的一部分，也要根据前后帧的逻辑预测物体的状态并进行描述，不要因为暂时性的遮挡说‘物体消失了’。”
        相对关系：对于手眼相机（eys-in-hand）而言,靠近和远离物体的表现形式通常为相对位置不变，而背景和目标物体放大或缩小


        [Action State Ruler]
        成功 (1)：机器人末端执行器（EOAT）稳定抓取目标物并将其移动至指定位姿或维持抓取状态。
        失败 (-1)：发生以下情况之一即判定为失败：
        触碰但未能抓起（滑脱）。
        抓取偏离目标中心导致姿态不稳定。
        机器人在动作过程中丢失目标。
        
        特殊规则：
        无法判断(0):无法判断机器人末端执行器动作是否成功的情况，这可能是机器人行动片段不完整或操作无法归类导致的

        [Output Format]
        必须严格以 JSON 格式输出，不要包含任何额外对话：
        {
        "action_summary": "string",
        "action_state": "integer, 1/-1/0",
        "detailed_description": "string"
        }
        """,
        "input_prompt": "你的任务是..."
    },
    "output": {
        "layerId": "l2"

    }
}
```

字段说明
#### basic
- `task_id`：业务任务 ID，由服务入口传入或生成。
- `job_id`：运行任务 ID，由服务编排层生成。MVP 阶段可为空。
- `parser_info`: 文档解析模块的输出对象
- `generation_info`: 事件生成模块的输出对象

#### sampling
- `mode`: 选择的采样模式
- `params`: 选定的采样模式需要的参数信息。基于采样模式差异，需要的参数名称和数量可能不同
- `fixed_frame_len`: 事件区间内部目标采样帧数。该参数继续使用帧，因此名称包含 `frame`；无论配置值多大，VLM 请求中的 event 图片均受 20 张硬上限约束
- `context_frame_len`: 事件区间前、后各自追加的上下文帧数，默认 2；上下文只传入 VLM，不改变事件区间边界

#### vlm_params
- `model`: 选择的模型名称
- `system_prompt`: 调用时的system_prompt
- `input_prompt`: 调用时的user_prompt

#### output
- `layerId`: 动作层级ID，默认为"l2"
- `category`: 动作层级分类,默认为"detail"
- `attributes.scene`: 场景信息，默认为"tabletop"
- `attributes.sceneTags`: 分类标签信息，默认为[]

### VLM模型服务调用接口示例
```
curl http://192.168.23.9:1234/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen/qwen3.5-9b",
    "system_prompt": "You answer only in rhymes.",
    "input": [
    {
        "type": "text",
        "content": "这是一段机器人操作视频的关键帧序列，请描述机器人完成的任务，用以下格式回答..."
    },
    {
        "type": "text",
        "content": "right_image context_before frame 1, timestamp: 0.000 seconds"
    },
    {
        "type": "image",
        "data_url": "data:image/jpeg;base64,IMAGE_1_BASE64"
    },
    {
        "type": "text",
        "content": "right_image event frame 2, timestamp: 0.100 seconds"
    },
    {
        "type": "image",
        "data_url": "data:image/jpeg;base64,IMAGE_2_BASE64"
    },
    {
        "type": "text",
        "content": "right_image context_after frame 3, timestamp: 0.200 seconds"
    },
    {
        "type": "image",
        "data_url": "data:image/jpeg;base64,IMAGE_3_BASE64"
    }
    ]
}'
```

参数字段说明：
- `model`: 选择的 VLM 模型，目前只支持 "qwen/qwen3.5-9b"
- `system_prompt`: 对话的system_prompt设定信息
- `input`: 对话的用户prompt输入信息


## 功能逻辑
1. 请求解析。提取`parser_info`和`generation_info`中的信息
2. 事件区间校验。校验 `generation_info.event_periods` 与 `parser_info.timestamp_list` 的边界一致性
3. 事件关键帧采样。结合事件区间和 `parser_info.image_list`，生成每一个事件片段的关键帧序列
4. 事件标注。将关键帧序列传入 VLM 模型，获取事件标注信息
5. 格式化输出

### 事件区间消费规则

事件片段由事件生成模块统一构造。本模块只校验并消费按摄像头 topic 分组的 `generation_info.event_periods`，不得重新对 `event_points` 配对，以避免上下游出现两套切分规则。半闭半开边界和 topic 内相邻节点配对逻辑以 `EventGenerationModuleDoc.md` 为准。

### 事件关键帧采样规则
固定序列长度方案
- 每个事件区间只采样其 `topic_key` 指定的单个摄像头；一次 VLM 请求不得混入其他图像 topic
- event 图片目标数量为 `fixed_frame_len`，默认 20，并设置 20 张硬上限
- 当事件帧数不超过目标数量时保留全部事件帧；事件较长时在完整事件区间上均匀采样，包含首尾帧，最多输出 20 张 event 图片
- 在事件区间之前追加最多 `context_frame_len=2` 个相邻帧，标记为 `context_before`
- 在事件区间之后追加最多 `context_frame_len=2` 个相邻帧，标记为 `context_after`
- 上下文图片不计入 20 张 event 图片上限，因此一次单视角请求最多包含 24 张图片
- 区间内采样帧标记为 `event`。上下文帧不修改 `start_index`、`end_index` 或最终标注时间字段
- 按 `context_before → event → context_after` 的时间顺序排列
- 采样完成后校验所有帧 index 严格按时间递增且不重复；不满足时拒绝调用 VLM
- 输出采样获取的关键帧图像序列
- 多个图像 topic 各自形成独立 VLM 请求，VLM 只描述当前单一视角下的机器人任务

### 事件标注流程
- 使用请求体中的VLM模型参数和提取的采样规则，请求VLM模型
- System Prompt 必须明确：只有 `event` 帧定义当前事件；`context_before/context_after` 仅用于理解事件边界，不得将只出现在上下文帧中的相邻动作归类为当前事件
- 当 `event` 帧本身不足以证明完整操作或结果时，要求模型输出 `action_state=0`，不得根据上下文补全动作
- 检查和修复模型返回对象的JSON
- 提取模型返回信息
- 格式化输出

## 数据 schema
### 事件片段输出格式
```
event_periods = {
  "left_image": [{
        "topic_key": "left_image",
        "source_topic": "/gripper/camera_fisheye_l/color/image_raw",
        "startSec": "6.12",
        "start_time": "1776150112.442719000",
        "startTimeNs": "1776150112442719000",
        "startTimestampNs": "1776150112442719000", 
        "episodeStartTimeNs": "6120000000",
        "episode_start_time": "6.120000000",
        "timeline_start_sec": "6.120000000",
        "endSec": "8.362",
        "end_time": "1776150114.684113300",
        "endTimeNs": "1776150114684113300",
        "endTimestampNs": "1776150114684113300",
        "episodeEndTimeNs": "8362000000",
        "episode_end_time": "8.362000000",
        "timeline_end_sec": "8.362000000"
    }]
}
```

字段说明
- `startSec`: 片段起点时间相对"timestamp_list"起始时间的相对时间，字符串格式，保留三位小数，单位秒; 
- `start_time`: 片段起点时间的时间戳，字符串格式，单位秒
- `startTimeNs`: 片段起点时间的时间戳，字符串格式，单位纳秒
- `startTimestampNs`: 片段起点时间的时间戳，字符串格式，单位纳秒。和`startTimeNs`保持一致
- `episodeStartTimeNs`: 片段起点时间的相对时间，字符串格式，单位纳秒。由`startSec` * 1e9得到
- `episode_start_time`: 片段起点时间的相对时间，字符串格式，单位秒。由`episodeStartTimeNs`转换得到，尾部保留9位小数
- `timeline_start_sec`: 片段起点时间的相对时间，字符串格式，单位秒。和`episode_start_time`保持一致
- `endSec`: 片段终点时间相对"timestamp_list"起始时间的相对时间，字符串格式，保留三位小数，单位秒; 
- `end_time`: 片段终点时间的时间戳，字符串格式，单位秒
- `endTimeNs`: 片段终点时间的时间戳，字符串格式，单位纳秒
- `endTimestampNs`: 片段终点时间的时间戳，字符串格式，单位纳秒。和`endTimeNs`保持一致
- `episodeEndTimeNs`: 片段终点时间的相对时间，字符串格式，单位纳秒。由`endSec` * 1e9得到
- `episode_end_time`: 片段终点时间的相对时间，字符串格式，单位秒。由`episodeEndTimeNs`转换得到，尾部保留9位小数
- `timeline_end_sec`: 片段终点时间的相对时间，字符串格式，单位秒。和`episode_end_time`保持一致


### 事件关键帧采样输出格式
```
image_samples = {
    "topic_name": [
        {"frame_index": 10, "sample_role": "context_before", "image_base64": "..."},
        {"frame_index": 12, "sample_role": "event", "image_base64": "..."},
        {"frame_index": 20, "sample_role": "context_after", "image_base64": "..."}
    ]
    ...
}
```

字段说明
- `topic_name`: 视频来源的topic
- `frame_index`: 主时间轴帧 index
- `sample_role`: `context_before`、`event` 或 `context_after`，VLM 请求文本中同步携带该角色
- `image_base64`: 图像的二进制编码，b"..."


### 事件标注输出格式
```
event_annotation = [
    {
        "startSec": "6.12",
        "start_time": "1776150112.442719000",
        "startTimeNs": "1776150112442719000",
        "startTimestampNs": "1776150112442719000", 
        "episodeStartTimeNs": "6120000000",
        "episode_start_time": "6.120000000",
        "timeline_start_sec": "6.120000000",
        "endSec": "8.362",
        "end_time": "1776150114.684113300",
        "endTimeNs": "1776150114684113300",
        "endTimestampNs": "1776150114684113300",
        "episodeEndTimeNs": "8362000000",
        "episode_end_time": "8.362000000",
        "timeline_end_sec": "8.362000000",

        "id": "seg_1782290078574",
        "prompt": "pick the bottle",
        "layerId": "l2",
        "category": "detail",
        "attributes": {
            "scene": "tabletop",
            "sceneTags": []
        },
        "description": "pick the blue bottle on the table",
        "baseline_camera_key": "right_image",
        "action_state": 1,
        "review_status": "pending"
    }
]
```


字段说明
- `startSec`到`timeline_end_sec`: 片段的时间信息，见`事件片段输出格式`
- `id`: 片段ID，由"seg"和13位雪花ID组成，由"_"相连
- `prompt`: 视频片段动作摘要，对应 VLM 返回的 `action_summary`
- `layerId`: 动作层级 ID，默认来自请求体 `output.layerId`，MVP 阶段默认为 `"l2"`
- `category`: 动作层级分类，默认来自请求体 `output.category`，MVP 阶段默认为 `"detail"`
- `attributes.scene`: 场景信息，默认来自请求体 `output.attributes.scene`，MVP 阶段默认为 `"tabletop"`
- `attributes.sceneTags`: 分类标签信息，默认来自请求体 `output.attributes.sceneTags`，MVP 阶段默认为空列表
- `description`: VLM 输出的自然语言事件描述
- `baseline_camera_key`: 当前单视角 VLM 请求使用的相机 key，与事件区间的 `topic_key` 一致
- `action_state`: 动作状态，`1` 表示成功，`-1` 表示失败，`0` 表示无法判断
- `review_status`: 人工复核状态；EventLabeler 生成 event 时固定输出 `"pending"`，后续由复核服务修改为 `accepted` 或 `rejected`

### 模块输出格式
```
{
    "task_id": "TASK-001",
    "job_id": "JOB-001",
    "response": event_annotation
}
```


## 异常处理规则

MVP 阶段默认采用 fail-fast 策略，但允许无可标注事件片段时正常返回空结果。

### 必须报错的情况

- 请求缺少 `basic`、`parser_info`、`generation_info`、`sampling`、`vlm_params` 或 `task_id`。
- `parser_info` 缺少 `timestamp_list` 或 `image_list`，或 `timestamp_list` 为空。
- `generation_info` 缺少 `event_periods`，或 `event_periods` 不是按 topic 分组的字典。
- `timestamp_list` 不是按时间递增排列，或 `event_periods` 中的起止 index/时间戳与主时间轴不一致。
- `image_list` 中缺少被采样 topic 的图像数据，或图像数据无法编码为 VLM 请求需要的 base64 / data URL。
- 采样参数非法，例如 `fixed_frame_len` 不是正整数、`context_frame_len` 不是非负整数，或 `mode` 未注册。
- 事件区间非法，例如起止顺序错误、范围越界或区间相互重叠。
- `vlm_params.model` 为空，或 VLM 服务地址、模型名称、请求参数不可用。
- VLM 返回内容无法解析为 JSON，且经过一次修复后仍不满足输出字段要求。

### 可以降级处理的情况

- `event_periods` 为空时，返回 `response: []`，不调用 VLM，不视为异常。
- 某个事件片段帧数不超过 `min(fixed_frame_len, 20)` 时，保留该片段全部可用帧；靠近文件首尾、上下文不足 2 帧时使用实际可用帧，不越界补帧。
- 多个 camera topic 可用时，每个事件只采样自身 `topic_key`；该 topic 缺少图像时直接报错，不回退到其他相机。
- 单个事件片段调用 VLM 失败时，MVP 阶段默认 fail-fast；后续可扩展为片段级失败记录并继续处理其他片段。

### 日志要求

- 报错日志需要包含 `task_id`、`job_id`、片段 `id`、事件节点 `order_id`、时间戳范围和错误原因。
- VLM 采样日志必须记录 `task_id`、camera topic、事件起止 index、事件原始帧数、event 实际采样数、前后上下文数和 event 上限 20；VLM 调用日志记录模型名称、请求耗时和解析结果状态。
- 每次调用记录实际 System Prompt、User Prompt、按顺序排列的帧 index/角色/时间戳清单，以及解析后的 VLM 输出；图片 Base64 不写入日志。
- 当返回空结果、跳过视角或采样帧不足时，需要记录对应原因，便于前端和人工复核定位。

## MVP 验收标准

1. 使用 `tests/train_data_1.mcap` 或其上游产物可以跑通。
2. 能够接收 Parser 输出的 `timestamp_list`、`image_list` 和事件生成模块输出的 `event_periods`。
3. 能够校验并消费半闭半开事件区间，不在本模块内重复执行节点筛选或配对。
4. 能够按 `fixed_sequence` 策略为每个事件片段生成固定长度或不超过固定长度的关键帧序列。
5. 能够调用 VLM 服务，并从模型返回中提取 `action_summary`、`action_state`、`detailed_description` 等字段。
6. 能够将 VLM 返回格式化为 `response` 列表，且每个元素包含片段时间信息、`topic_key`、`source_topic`、`id`、`prompt`、`layerId`、`category`、`attributes`、`description`、`baseline_camera_key`、`action_state`、`review_status`，其中 `review_status` 初始值为 `pending`。
7. 当 `event_periods` 为空时，能够返回 `response: []`，不调用 VLM。
8. 当输入字段缺失、时间戳非法、采样参数非法、图像编码失败或 VLM 返回非法时，能够抛出明确错误，不生成不可信标注结果。
9. 至少包含单元测试或等价验证用例，覆盖正常标注、空事件区间、采样帧不足、非法输入、VLM 返回 JSON 修复失败五类场景。

## 更新记录

- 2026-07-14：强化 context/event 角色约束；只允许依据 event 帧归类当前动作，并增加采样顺序校验及 VLM 输入输出摘要日志。
- 2026-07-14：自动生成的每个 event 增加顶层字段 `review_status`，初始值固定为 `pending`。
- 2026-07-13：多摄像头改为逐 topic 单独调用 VLM；event 图片设置 20 张硬上限，长事件均匀采样，上下文不计入上限；`baseline_camera_key` 改为当前事件 topic。
- 2026-07-13：`fixed_len` 更名为 `fixed_frame_len`；每个事件区间前后各追加 2 个上下文帧并标记 sample role，事件区间时间边界保持不变。
- 2026-07-13：`event_periods` 改为按摄像头 topic 分组；输出标注保留 `topic_key` 与 `source_topic`，供后续筛选。
- 2026-07-13：事件区间生成职责迁移到 EventGeneration；本模块改为只消费分 topic 的 `event_periods` 并执行采样、VLM 调用和标注格式化。
- 2026-07-13：明确半闭半开事件片段的末帧边界规则，事件终点为最后一帧时不前移。
- 2026-07-10：补充输出、异常处理规则和 MVP 验收标准，修正事件生成模块命名和输出字段说明。
- 2026-07-07：统一文档格式，补充待设计章节。
