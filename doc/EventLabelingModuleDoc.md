# 事件标注模块设计文档

## 文档状态

- 文档类型：模块设计文档
- 所属流程：事件标注阶段
- 当前阶段：MVP 设计
- MVP 范围：基于前序模块的输出，实现对事件的标注和描述，并输出

## 功能描述

事件标注模块负责将事件片段中的视频图像和上下文信息传入 VLM 模型，结合 prompt 输出事件标签和自然语言描述。

MVP 阶段，只需要实现半闭半开规则下的片段生成和固定长度的采样

## 输入

本模块的输入有三个来源：
1. 来自文档解析模块的"timestamp_list", "image_list"
2. 来自事件生成模块的"event_points"
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
            "prompt": "pick the bottle",
            "layerId": "l2",
            "category": "detail",
            "attributes": {
                "scene": "tabletop",
                "sceneTags": []
            },
            "description": "pick the blue bottle on the table",
            "baseline_camera_key": "/camera/wrist_left/image",
            "action_state": 1
        }
    ]
}
```

- `response`：事件标注结果列表。每个元素对应一个事件片段，包含片段时间信息、动作标签、描述信息和动作状态。
- `task_id` / `job_id`：透传请求中的任务标识，方便服务编排层关联上下游结果。
- 输出文件或存储位置：MVP 阶段不强制定义独立文件格式，由服务编排层将该字典对象作为事件标注阶段结果传递给调用方；如需要落盘，可存储为 JSON 文件或任务结果表中的结构化字段。
- 下游模块消费方式：前端或人工复核模块读取 `response`，展示每个事件片段的时间范围、动作摘要、动作状态和自然语言描述。若没有可标注事件片段，返回空列表 `response: []`。

## 架构设计
本模块采用工厂模式加抽象基类的整体架构。

核心抽象：  
- `EventRangeBase`: 事件片段生成基类。不同的生成规则实现不同的生成实例
- `EventSamplingBase`: 事件采样基类。不同的采样规则实现不同的采样实例
- `EventLabelingBase`: 事件标注基类。不同的事件标注策略实现不同生成器实例


第一版实现：
- `CloseOpenRange`: 半闭半开规则的事件片段生成实例
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
            "fixed_len": 10
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
        动作专注性：仅描述机器人与末端工具（EOAT）相关的操作，忽略不相关的背景人员走动。
        描述粒度：对于场景中的多个同类对象，必须区分颜色、形状或位置特征，避免模糊称呼。
        遮挡处理：“即使当机器人遮挡了物体的一部分，也要根据前后帧的逻辑预测物体的状态并进行描述，不要因为暂时性的遮挡说‘物体消失了’。”

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
- `fixed_len`: 固定长度模式的采样参数，控制采样序列的最终长度

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
        "content": "Frame 1, timestamp: 0.000 seconds"
    },
    {
        "type": "image",
        "data_url": "data:image/jpeg;base64,IMAGE_1_BASE64"
    },
    {
        "type": "text",
        "content": "Frame 2, timestamp: 0.100 seconds"
    },
    {
        "type": "image",
        "data_url": "data:image/jpeg;base64,IMAGE_2_BASE64"
    },
    {
        "type": "text",
        "content": "Frame 3, timestamp: 0.200 seconds"
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
2. 事件片段生成。基于`generation_info`中的"event_points"，生成事件片段
3. 事件关键帧采样。结合步骤二的事件片段信息和`parser_info`的"image_list"，生成每一个事件片段的关键帧序列
4. 事件标注。将关键帧序列传入VLM模型，获取事件标注信息
5. 格式化输出。

### 事件片段生成规则
- 基于`generation_info`中的"event_points"，和`parser_info`的"timestamp_list"信息，依次将各个事件节点按照“半闭半开”规则生成事件片段
- “半闭半开”规则：对于一个事件片段，其时间范围起点为起点时间戳，终点为终点前一帧的时间戳（最后一个片段除外）

“半闭半开”规则示例：
假设某个视频的起止时间戳有20个, 其有三个事件节点，分别在5, 10, 15三个位置，则
第一个片段为0~4， 第二个片段为5~9, 第三个片段为10～14, 第四个片段在15～20

### 事件关键帧采样规则
固定序列长度方案
- 设定一个序列长度，计算采样间隔。如果实际帧数少于序列长度，则全部加入采样序列
- 按时间顺序依次进行采样，将采样的图像按次序排列成列表
- 输出采样获取的关键帧图像序列
- 对于每一个视频topic，分别进行采样，并生成输出

### 事件标注流程
- 使用请求体中的VLM模型参数和提取的采样规则，请求VLM模型
- 检查和修复模型返回对象的JSON
- 提取模型返回信息
- 格式化输出

## 数据 schema
### 事件片段输出格式
```
event_periods = [
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
        "timeline_end_sec": "8.362000000"
    }
]
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
    "topic_name": [(index, image_base64), ...]
    ...
}
```

字段说明
- `topic_name`: 视频来源的topic
- `index`: 序列index
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
        "baseline_camera_key": "/camera/wrist_left/image",
        "action_state": 1
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
- `baseline_camera_key`: 作为标注基准的图像 topic，MVP 阶段从参与采样的 camera topic 中选择一个主视角
- `action_state`: 动作状态，`1` 表示成功，`-1` 表示失败，`0` 表示无法判断

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
- `generation_info` 缺少 `event_points`，或 `event_points` 不是字典结构。
- `timestamp_list` 不是按时间递增排列，或 `event_points` 中的 `timestamp_index` 越界。
- `image_list` 中缺少被采样 topic 的图像数据，或图像数据无法编码为 VLM 请求需要的 base64 / data URL。
- 采样参数非法，例如 `fixed_len` 不是正整数，或 `mode` 不在已注册采样策略中。
- 片段生成参数非法，例如事件节点顺序与时间戳顺序不一致，或片段起止索引无法形成合法半闭半开区间。
- `vlm_params.model` 为空，或 VLM 服务地址、模型名称、请求参数不可用。
- VLM 返回内容无法解析为 JSON，且经过一次修复后仍不满足输出字段要求。

### 可以降级处理的情况

- `event_points` 为空时，返回 `response: []`，不调用 VLM，不视为异常。
- 某个事件片段帧数少于 `fixed_len` 时，保留该片段全部可用帧作为采样结果。
- 多个 camera topic 可用时，允许只选择配置中的主视角作为 `baseline_camera_key`；其他视角缺失时记录日志，不影响主视角可标注片段。
- 单个事件片段调用 VLM 失败时，MVP 阶段默认 fail-fast；后续可扩展为片段级失败记录并继续处理其他片段。

### 日志要求

- 报错日志需要包含 `task_id`、`job_id`、片段 `id`、事件节点 `order_id`、时间戳范围和错误原因。
- VLM 调用日志需要记录模型名称、采样帧数量、参与采样的 camera topic、请求耗时和解析结果状态。
- 当返回空结果、跳过视角或采样帧不足时，需要记录对应原因，便于前端和人工复核定位。

## MVP 验收标准

1. 使用 `tests/train_data_1.mcap` 或其上游产物可以跑通。
2. 能够接收 Parser 输出的 `timestamp_list`、`image_list` 和事件生成模块输出的 `event_points`。
3. 能够按照半闭半开规则生成事件片段，且片段时间字段满足数据 schema。
4. 能够按 `fixed_sequence` 策略为每个事件片段生成固定长度或不超过固定长度的关键帧序列。
5. 能够调用 VLM 服务，并从模型返回中提取 `action_summary`、`action_state`、`detailed_description` 等字段。
6. 能够将 VLM 返回格式化为 `response` 列表，且每个元素包含片段时间信息、`id`、`prompt`、`layerId`、`category`、`attributes`、`description`、`baseline_camera_key`、`action_state`。
7. 当 `event_points` 为空时，能够返回 `response: []`，不调用 VLM。
8. 当输入字段缺失、时间戳非法、采样参数非法、图像编码失败或 VLM 返回非法时，能够抛出明确错误，不生成不可信标注结果。
9. 至少包含单元测试或等价验证用例，覆盖正常标注、空事件节点、采样帧不足、非法输入、VLM 返回 JSON 修复失败五类场景。

## 更新记录

- 2026-07-10：补充输出、异常处理规则和 MVP 验收标准，修正事件生成模块命名和输出字段说明。
- 2026-07-07：统一文档格式，补充待设计章节。
