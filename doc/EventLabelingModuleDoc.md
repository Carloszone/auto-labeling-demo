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
1. 来自编排层的 `timestamp_list` 和 `video_paths`；独立调用模块时仍兼容 Parser 原始 `image_list`
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
            "sampling_frame_gap": 5,
            "context_frame_len": 2
        }
    },
    "vlm_params": {
        "model": "qwen/qwen3.5-9b",
        "system_prompt": """
        [Role]
        你是一位拥有丰富视觉几何与机器人控制背景的具身智能数据标注专家。你的任务是分析（第一人称手眼相机或第三人称固定相机视角的）机器人操作视频关键帧序列，识别精确的空间信息和操作细节，并判断动作结果。

        [1. 核心动作状态机 (Action Primitives)]
        你必须严格根据机械臂末端执行器（EOAT/夹爪）的物理状态和运动趋势，将机器人的动作分类为以下基础状态：
        - 测试：夹爪进行一次或多次的开合动作，没有其他行为。
        - 靠近：夹爪处于张开（非夹持）状态，且正在向目标物体移动。
        - 拿起：夹爪尝试夹取物体；夹爪状态由张开（非夹持）转为闭合（夹持）。
        - 移动：夹爪保持闭合（夹持）状态，并带着夹持的物体进行平移或旋转。
        - 放下：夹爪松开物体；状态由闭合（夹持）转为张开（非夹持）以释放物体。
        - 远离（复位）：夹爪处于张开（非夹持）状态，并向远离物体的方向移动。特殊情况：如果夹爪尝试回到启动前的初始位姿，则分类为“复位”。

        *未完成动作的标注规则*：如果视频片段中的动作尝试了但未完成或失败（如正在靠近但未接触，抓了但滑脱），在描述动作意图时必须加上“尝试”二字（例如：“尝试拿起”、“尝试靠近”）。

        [2. 场景与属性规范 (Scene & Attribute Rules)]
        在描述环境细节与交互时，必须严格遵守以下注意力层级，避免受到干扰：
        1. 聚焦“前景交互轴”：绝对优先关注机器人夹爪（EOAT）与目标交互物件。必须准确识别它们的视觉属性（颜色、材质、形状、状态）。
        2. 描述“直接环境”：描述交互发生的支撑面（如木制桌面、黑色垫子）和相关的局部容器（如黑色收纳盒、托盘）。
        3. 忽略“背景干扰”：绝对忽略背景中走动的人员、远处的架子、闪烁的光影或与当前操作无关的设备。除非它们与机器人发生了物理碰撞，否则不要在描述中提及。

        [3. 视角与相对关系 (Spatial Perspective)]
        - 坐标系约束：描述方位时（如“左/中/右”、“前方/后方”、“上/下”），必须始终以机器人本体坐标系为基准。
        - 手眼相机（Eye-in-Hand）规则：当相机安装在机械臂末端时，夹爪在画面中的相对位置基本保持不变。“靠近”物体在视觉上表现为目标物体和背景在画面中逐渐放大；“远离”则表现为目标物体和背景逐渐缩小。不能因为夹爪在画面中没移动，就判定为“无意义动作”。
        - 遮挡处理：即使机器人暂时遮挡了物体的一部分，也要根据前后帧的逻辑推断物体状态，不要因为暂时性的视觉遮挡而判定“物体消失”。

        [4. 结果判定规则 (Action State Ruler)]
        - 成功 (1)：机器人成功执行了当前意图（如稳定抓取目标物并将其移动，或顺利放下）并维持了稳定状态。
        - 失败 (-1)：发生以下情况之一即判定为失败：触碰但未能抓起（滑脱）；抓取偏离中心导致姿态不稳定；机器人在移动过程中意外丢失/掉落目标。
        - 无法判断 (0)：视频片段截断不完整，或者动作无法清晰归类至上述状态。

        [5. 输出格式与规范 (Output Schema)]
        动作描述必须用词精炼保留以下信息要素：[动作/尝试动作] + [交互物件（带属性）]。
        - 动作描述示例1：夹爪靠近签字笔
        - 动作描述示例2：夹爪尝试放下签字笔

        环境细节描述必须严格保留以下信息要素：[动作/尝试动作] + [交互物件（带属性）] + [地点/环境]。
        - 细节示例1：“尝试靠近木制桌面上的黄色方块”、“尝试将黄色瓶子放入黑色的盒子中”
        - 细节示例2：“黑色的夹爪在木制桌面上方缓慢下降，尝试靠近并拿起静止在黑色垫子上的黄色方块。桌面上另有3个红色球体处于静止状态。”

        【严厉警告】必须严格且仅以 JSON 格式输出，不要包含任何思考过程、解释、Markdown代码块标记（如 ```json ）或其他自然语言对话！
        {
        "action_summary": "简洁的动作摘要，格式必须包含 [动作/尝试动作] + [交互物件]",
        "action_state": 1,
        "detailed_description": "环境细节描述。描述详细的场景、环境属性、前景交互目标与机械臂动作的细节描述，必须严格区分颜色、形状、相对空间位置，彻底忽略背景人员干扰"
        }
        """,
        "input_prompt": "这是一段机器人操作视频的关键帧序列，请描述机器人完成的任务，使用中文描述其进行的动作和环境细节信息。"
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
- `fixed_frame_len`: 事件区间内部允许传给 VLM 的 event 图片数上限，默认 20；最终上限为 `min(fixed_frame_len, 20)`
- `sampling_frame_gap`: 事件区间首选采样间隔帧数，默认 5。先从事件起点开始每隔该帧数采样；若候选图片数超过 event 图片上限，则将有效间隔调整为 `ceil(事件区间帧数 / event图片上限)`，确保最终数量不超限
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
curl http://192.168.23.106:1234/api/v1/chat \
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
    ],
    "store": false,
    "reasoning": "off",
    "temperature": 0.7,
    "max_output_tokens": 1024
}'
```

参数字段说明：
- `model`: 选择的 VLM 模型，目前只支持 "qwen/qwen3.5-9b"
- `system_prompt`: 对话的system_prompt设定信息
- `input`: 对话的用户prompt输入信息
- `store`: 是否由 LM Studio 保存 prediction history。Event 之间相互独立，默认必须为 `false`，避免无用的会话状态和 prediction history 写入
- `reasoning`: 推理模式，默认 `off`
- `temperature`: 采样温度，默认 `0.7`
- `max_output_tokens`: 最大输出 token 数，默认 `1024`


## 功能逻辑
1. 请求解析。提取`parser_info`和`generation_info`中的信息
2. 事件区间校验。校验 `generation_info.event_periods` 与 `parser_info.timestamp_list` 的边界一致性
3. 事件关键帧采样。结合事件区间和 `parser_info.video_paths`，按固定 30 FPS 视频的零基帧索引读取关键帧；独立调用时兼容 `image_list`
4. 事件标注。将关键帧序列传入 VLM 模型，获取事件标注信息
5. 格式化输出

### 事件区间消费规则

事件片段由事件生成模块统一构造。本模块只校验并消费按摄像头 topic 分组的 `generation_info.event_periods`，不得重新对 `event_points` 配对，以避免上下游出现两套切分规则。半闭半开边界和 topic 内相邻节点配对逻辑以 `EventGenerationModuleDoc.md` 为准。

### 事件关键帧采样规则
固定序列长度方案
- 每个事件区间只采样其 `topic_key` 指定的单个摄像头；一次 VLM 请求不得混入其他图像 topic
- event 图片上限为 `min(fixed_frame_len, 20)`；默认 `fixed_frame_len=20`
- 首先按 `sampling_frame_gap` 从事件起点等间隔采样；只有候选图片数超限时才自动扩大采样间隔
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

单个 Event 调用 VLM 发生 HTTP、超时、连接或响应解析异常时，不中断 EventLabeling 和完整 Pipeline。该 Event 输出失败标注：

```json
{
  "action_summary": "VLM请求失败（错误类型或HTTP状态码）",
  "action_state": -1,
  "detailed_description": "详细错误信息",
  "review_status": "pending"
}
```

HTTP 错误的详细描述包含状态码、reason 和服务端响应正文。HTTP 成功但模型输出解析或字段校验失败时，详细描述同时包含错误原因和模型原始 message 内容；若无法提取 message，则记录原始响应信封。`description` 最终截断为最多 4000 字符。错误仍写入日志，进度回调按已处理 Event 正常推进，后续 Event 继续请求 VLM。

MVP 阶段默认采用 fail-fast 策略，但允许无可标注事件片段时正常返回空结果。

### 必须报错的情况

- 请求缺少 `basic`、`parser_info`、`generation_info`、`sampling`、`vlm_params` 或 `task_id`。
- `parser_info` 缺少 `timestamp_list`，或同时缺少 `video_paths` 和兼容输入 `image_list`，或 `timestamp_list` 为空。
- `generation_info` 缺少 `event_periods`，或 `event_periods` 不是按 topic 分组的字典。
- `timestamp_list` 不是按时间递增排列，或 `event_periods` 中的起止 index/时间戳与主时间轴不一致。
- `video_paths` 中缺少被采样 topic、无法精确定位目标帧，或图像无法编码为 VLM 请求需要的 base64 / data URL。兼容模式下对应规则适用于 `image_list`。
- 采样参数非法，例如 `fixed_frame_len` 或 `sampling_frame_gap` 不是正整数、`context_frame_len` 不是非负整数，或 `mode` 未注册。
- 事件区间非法，例如起止顺序错误、范围越界或区间相互重叠。
- `vlm_params.model` 为空，或 VLM 服务地址、模型名称、请求参数不可用。
- VLM 返回内容无法解析为 JSON，且经过一次修复后仍不满足输出字段要求。

### 可以降级处理的情况

- `event_periods` 为空时，返回 `response: []`，不调用 VLM，不视为异常。
- 某个事件片段按 `sampling_frame_gap` 采样后不超过 `min(fixed_frame_len, 20)` 时，保留该间隔采样结果；超限时自动扩大间隔。靠近文件首尾、上下文不足 2 帧时使用实际可用帧，不越界补帧。
- 多个 camera topic 可用时，每个事件只采样自身 `topic_key`；该 topic 缺少图像时直接报错，不回退到其他相机。
- 单个事件片段调用 VLM 发生 HTTP、超时、连接或响应解析异常时，生成 `action_state=-1` 的失败标注并继续处理其他片段；EventLabeling 输入结构非法等模块级错误仍直接失败。

### 日志要求

- 报错日志需要包含 `task_id`、`job_id`、片段 `id`、事件节点 `order_id`、时间戳范围和错误原因。
- VLM 采样日志必须记录 `task_id`、camera topic、事件起止 index、事件原始帧数、event 实际采样数、前后上下文数和 event 上限 20；VLM 调用日志记录模型名称、请求耗时和解析结果状态。HTTP 错误还需记录状态码、响应类型、请求图片数、请求体大小和截断后的服务端错误正文。
- 每次调用记录实际 System Prompt、User Prompt、按顺序排列的帧 index/角色/时间戳清单，以及解析后的 VLM 输出；图片 Base64 不写入日志。
- 当返回空结果、跳过视角或采样帧不足时，需要记录对应原因，便于前端和人工复核定位。

## MVP 验收标准

1. 使用 `tests/train_data_1.mcap` 或其上游产物可以跑通。
2. 能够接收 `timestamp_list`、固定帧率 MP4 的 `video_paths` 和事件生成模块输出的 `event_periods`，并兼容 Parser 原始 `image_list`。
3. 能够校验并消费半闭半开事件区间，不在本模块内重复执行节点筛选或配对。
4. 能够按 `fixed_sequence` 策略为每个事件片段生成固定长度或不超过固定长度的关键帧序列。
5. 能够调用 VLM 服务，并从模型返回中提取 `action_summary`、`action_state`、`detailed_description` 等字段。
6. 能够将 VLM 返回格式化为 `response` 列表，且每个元素包含片段时间信息、`topic_key`、`source_topic`、`id`、`prompt`、`layerId`、`category`、`attributes`、`description`、`baseline_camera_key`、`action_state`、`review_status`，其中 `review_status` 初始值为 `pending`。
7. 当 `event_periods` 为空时，能够返回 `response: []`，不调用 VLM。
8. 当输入字段缺失、时间戳非法、采样参数非法或图像编码失败时，能够抛出明确的模块级错误；单 Event 的 VLM 请求或解析失败时输出明确的失败标注，不生成伪造的动作结论。
9. 至少包含单元测试或等价验证用例，覆盖正常标注、空事件区间、采样帧不足、非法输入、VLM 返回 JSON 修复失败五类场景。

## 更新记录

- 2026-07-16：VLM 请求默认增加 `store: false`，Event 标注使用无状态调用，不保存 LM Studio prediction history。
- 2026-07-16：VLM 响应解析或字段校验失败时，失败标注的 `description` 增加原始模型 message，便于定位非法 `action_state` 等输出问题。
- 2026-07-16：新增 `sampling_frame_gap`；event 默认按固定帧间隔采样，候选数超过 `fixed_frame_len` 和 20 张硬上限时自动扩大间隔。
- 2026-07-16：默认 System Prompt 增加“测试”动作状态，并将 `action_summary` 精简为动作与交互物件；`detailed_description` 继续承载地点和环境细节。本地 LM Studio VLM 接口为 `http://192.168.23.106:1234/api/v1/chat`。
- 2026-07-15：VLM 单 Event 异常改为输出 `action_state=-1` 的错误标注并继续批次，不再导致完整 Pipeline 失败。
- 2026-07-14：强化 context/event 角色约束；只允许依据 event 帧归类当前动作，并增加采样顺序校验及 VLM 输入输出摘要日志。
- 2026-07-14：自动生成的每个 event 增加顶层字段 `review_status`，初始值固定为 `pending`。
- 2026-07-13：多摄像头改为逐 topic 单独调用 VLM；event 图片设置 20 张硬上限，长事件均匀采样，上下文不计入上限；`baseline_camera_key` 改为当前事件 topic。
- 2026-07-13：`fixed_len` 更名为 `fixed_frame_len`；每个事件区间前后各追加 2 个上下文帧并标记 sample role，事件区间时间边界保持不变。
- 2026-07-13：`event_periods` 改为按摄像头 topic 分组；输出标注保留 `topic_key` 与 `source_topic`，供后续筛选。
- 2026-07-13：事件区间生成职责迁移到 EventGeneration；本模块改为只消费分 topic 的 `event_periods` 并执行采样、VLM 调用和标注格式化。
- 2026-07-13：明确半闭半开事件片段的末帧边界规则，事件终点为最后一帧时不前移。
- 2026-07-10：补充输出、异常处理规则和 MVP 验收标准，修正事件生成模块命名和输出字段说明。
- 2026-07-07：统一文档格式，补充待设计章节。
