# 输入解析模块设计文档

## 文档状态

- 文档类型：模块设计文档
- 所属流程：输入解析阶段
- 当前阶段：MVP 设计
- MVP 范围：支持单个或多个 MCAP 文件解析、排序、对齐和插值

## 功能描述

输入解析模块负责将输入文件解析为按主时间轴对齐的结构化数据。

本模块只保证文件解析、topic 数据抽取、时间戳排序、时间同步和插值结果准确。事件切分由后续事件生成模块负责，Parser 不负责判断事件边界。

当前阶段仅支持 MCAP 文件。后续可扩展支持视频加机器人日志组合文件、LeRobot 数据集等格式。

## 架构设计

本模块采用工厂模式加抽象基类的整体架构。

核心抽象：

- `ParserBase`：文件解析基类。不同文件类型实现不同解析器，例如 `McapParser`。
- `AlignBase`：数据对齐基类。不同对齐策略实现不同对齐器。
- `InsertBase`：数据插值基类。不同插值算法实现不同插值器。

第一版实现：

- `McapParser`：解析单个或多个 MCAP 文件。
- `ConditionalInsertAligner`：基于主时间轴进行条件插值对齐。
- `DefaultInserter`：实现图像最近邻、普通向量线性插值、`pose7d` 专用插值。

后续如果需要替换姿态插值算法，可以新增插值实例并注册到插值工厂，不需要改动 Parser 主流程。

## 模块请求格式

服务编排层会将 `basic_config`、`parser_config` 和运行时字段组合成 Parser 模块请求。

```json
{
  "basic": {
    "task_id": "TASK-001",
    "job_id": "JOB-001"
  },
  "parser": {
    "folder_path": "本地目录或 S3 地址",
    "file_type": "mcap",
    "topics": "xxx.json"
  },
  "align": {
    "main_time_topic": "/camera/wrist_left/image",
    "method": "conditional_insert"
  },
  "insert": {
    "rotation": "ZYX",
    "max_tor_time_sec": 0.2
  },
  "output_format": {
    "include_vector_view": true,
    "include_component_schema": true
  }
}
```

### topic内容
```json
{
    "cameras": [
        {
            "name": "wrist_left",
            "topic": "/camera/wrist_left/image",
            "role": "image",
            "parser": "image",
            "format": "raw",
            "encoding": "bgr8",
            "width": 640,
            "height": 480,
            "required": true,
            "missing_policy": "error"
        }
    ],
        "state": [
        {
            "name": "left_arm.pose",
            "topic": "/left_arm/tcp_pose",
            "role": "state",
            "parser": "pose7d",
            "dtype": "float32",
            "shape": [7],
            "fields": [
            "position.x",
            "position.y",
            "position.z",
            "orientation.x",
            "orientation.y",
            "orientation.z",
            "orientation.w"
            ],
            "required": true,
            "missing_policy": "error"
        }
    ],
        "action": [
        {
        "name": "right_gripper",
        "topic": "/gripper/gripper_r/data",
        "role": "gripper",
        "parser": "gripper",
        "dtype": "float32",
        "fields": ["angle"],
        "shape": [1],
        "required": false,
        "sync_policy": "3",
        "missing_policy": "zero",
        "group": "right"
        }
    ]
}
```

## 字段说明

### basic

- `task_id`：业务任务 ID，由服务入口传入或生成。
- `job_id`：运行任务 ID，由服务编排层生成。MVP 阶段可为空。

### parser

- `folder_path`：输入文件所在目录。本地路径直接读取；S3 地址需要先调用后端下载模块获取本地临时目录。
- `file_type`：输入文件类型。MVP 阶段仅支持 `mcap`。
- `topics.cameras`：图像 topic 配置。
- `topics.state`：状态 topic 配置。
- `topics.action`：动作 topic 配置。

### topic item

- `name`：输出中的稳定 key，用于区分多臂、多夹爪、底盘等多个同类型 topic。
- `topic`：原始 topic 名称。
- `role`：topic 角色，例如 `image`、`state`、`action`、`joint_state`、`gripper`。
- `parser`：解析模式，例如 `image`、`vector`、`pose7d`。
- `dtype`：输出数据类型，默认 `float32`。
- `shape`：输出向量形状。
- `fields`：字段抽取路径，支持属性取值和下标取值。
- `missing_policy`：缺失值处理策略。
- `format`：图像格式，例如 `raw`、`jpeg`、`png`。
- `encoding`：图像 encoding，例如 `rgb8`、`bgr8`、`mono8`。
- `width`、`height`：图像尺寸。

## 字段抽取规则

字段路径只描述 message 内部字段，不需要和 topic 字符串拼接。

示例：

```text
topic = "/example/topic/data"
fields = ["position.x", "data[2]", "orientation.w"]
```

表示从 `/example/topic/data` 对应 message 中读取：

```text
message.position.x
message.data[2]
message.orientation.w
```

## missing_policy

MVP 阶段仅支持以下策略：

- `error`：缺失时直接报错。
- `zero`：缺失时填充 0。
- `previous`：缺失时填充前值；如果前值不存在，退化为 `zero`。

第一版不支持 `null`，因为输出需要保持数值结构稳定，便于 numpy、parquet 和训练数据消费。

## 时间单位约定

模块内部计算使用原始纳秒时间戳，避免高精度对齐时出现浮点精度损失。

对齐阈值 `insert.max_tor_time_sec` 的单位为秒，MVP 默认 0.2 秒；实现内部再转换为纳秒进行精确比较。

模块对外输出同时保留两种时间表示：

- `timestamp_ns`：字符串格式的纳秒时间戳，用于排序、对齐、插值、去重和下游精确计算；参与计算时由实现层转换为整数。
- `timestamp_sec`：字符串格式的秒级时间戳，用于日志、人工检查和前端展示。

如果下游只能接收一种时间，优先使用 `timestamp_ns`。

所有模块对外输出的时间戳字段统一使用字符串格式，包括纳秒时间戳和秒级时间戳。需要排序、比较、插值或精度计算时，由模块内部显式转换为整数或高精度数值，计算完成后再转换为字符串输出。

## 文档解析流程

1. 判断 `folder_path` 是否为 S3 地址。
2. 如果是 S3 地址，调用后端下载模块下载到本地临时目录。
3. 根据 `file_type` 选择解析器。MVP 阶段仅允许 `mcap`。
4. 根据 `topics` 配置读取图像、state、action 等数据。
5. 所有 topic 数据按 `timestamp_ns` 排序。
6. 输出解析后的 `images` 和 `values`。

## 多 MCAP 合并规则

MVP 阶段支持单个或多个 MCAP 文件。

合并规则：

1. 正式解析前先扫描每个文件 `main_time_topic` 的最早、最晚记录时间戳；文件名序号和上传顺序不得作为排序依据。
2. 以主相机最早时间戳升序处理；只有时间范围完全相同时，上传序号才可作为确定性仲裁键。
3. 连续区间直接拼接。重叠区间保留时间排序靠前 MCAP 的数据，裁掉后一个 MCAP 的重合主相机帧，并记录丢弃数量。
4. 小于内部阈值的缺口允许图像补帧和运动插值；达到或超过阈值直接失败。
5. 从全局第一条主相机记录开始，按整数帧号建立 30 FPS 相对纳秒时间轴，禁止通过浮点时间累加。
6. 所有图像 topic 独立生成对齐序列，但共用全局帧号；所有相机 MP4 使用相同帧数和恒定帧率。
7. 输出保留 `source_mcap`、`source_timestamp_ns`、`source_frame_index`、补帧标记和文件边界清单，保证可追溯。
8. 单个 MCAP 内同 topic、同 `timestamp_ns` 的重复消息仍直接失败。

重复数据示例：

```text
file_a.mcap: /camera/front @ 1000000000ns
file_b.mcap: /camera/front @ 1000000000ns
```

或同一个 MCAP 内：

```text
/camera/front @ 1000000000ns frame_A
/camera/front @ 1000000000ns frame_B
```

跨文件相同时间范围按照“时间排序靠前文件优先”处理；单文件内部同 topic、同 timestamp 重复直接失败，避免静默污染训练数据。

内部固定参数包括 `fps`、最大文件数量、连续间隔、视频补帧阈值、运动插值阈值、重叠策略和大缺口策略。这些参数不属于页面配置，不通过前端展示或修改。

## 数据对齐流程

1. 使用 `align.main_time_topic` 作为主时间轴。
2. 只保留主时间轴中有效的主帧。
3. 其他 camera topic 使用最近邻图像。
4. state/action topic 根据 `insert.max_tor_time_sec` 判断使用最近邻还是插值。
5. 输出四个并行列表，并保证同一 index 的数据来自同一个主帧时间。

## 插值规则

### 图像插值

图像使用最近邻策略，不做图像内容插值。

解析阶段需要保留原始图像信息：二进制数据、图像格式、图像 encoding、图像 width 和 height，以及可选 decoded array。

黑帧、重复帧、静止帧等质量问题由后续数据质检模块处理。

### 普通向量插值

对一维或多维数值向量使用线性插值。

如果最近数据点与主帧时间差小于等于 `insert.max_tor_time_sec`，直接采用最近邻值。

### pose7d 插值

当 `parser` 为 `pose7d` 时，数据结构为：

```text
[x, y, z, qx, qy, qz, qw]
```

MVP 阶段为保持与后续算法一致，采用以下策略：

1. `[x, y, z]` 使用线性插值。
2. `[qx, qy, qz, qw]` 先按 `insert.rotation` 转成欧拉角。
3. 对欧拉角进行旋转插值。
4. 再按 `insert.rotation` 转回四元数。
5. 拼接为 `[new_x, new_y, new_z, new_qx, new_qy, new_qz, new_qw]`。

风险说明：欧拉角插值可能存在角度跳变和万向节锁风险。后续可新增基于四元数 SLERP 的插值实例，并通过插值工厂注册切换。

## 解析输出 schema

解析后输出两个字典对象，分别保存图像和数值信息。

```python
images = {
    "topic": [
        {
            "timestamp_ns": "1000000000",
            "timestamp_sec": "1.000000000",
            "raw": b"...",
            "format": "raw",
            "encoding": "bgr8",
            "width": 640,
            "height": 480,
            "array": None
        }
    ]
}

values = {
    "topic": [
        {
            "timestamp_ns": "1000000000",
            "timestamp_sec": "1.000000000",
            "value": np.ndarray([...])
        }
    ]
}
```

## 对齐输出 schema

输出四个并行列表，所有列表依据 index 对齐。

```python
timestamp_list = [
    {
        "timestamp_ns": "1000000000",
        "timestamp_sec": "1.000000000"
    }
]

image_list = [
    {
        "wrist_left": {
            "raw": b"...",
            "format": "raw",
            "encoding": "bgr8",
            "width": 640,
            "height": 480,
            "array": None,
            "source_topic": "/camera/wrist_left/image"
        }
    }
]

state_list = [
    {
        "left_arm.pose": np.ndarray([...]),
        "right_arm.pose": np.ndarray([...])
    }
]

action_list = [
    {
        "left_arm.target_pose": np.ndarray([...])
    }
]
```

如果 `output_format.include_vector_view` 为 `true`，额外输出按配置顺序拼接后的 vector view：

```python
state_vector_list = [np.ndarray([...])]
action_vector_list = [np.ndarray([...])]
```

如果 `output_format.include_component_schema` 为 `true`，额外输出组件 schema：

```python
state_schema = [
    {
        "key": "left_arm.pose",
        "topic": "/left_arm/tcp_pose",
        "role": "joint_state",
        "parser": "pose7d",
        "fields": ["pose.position.x", "pose.position.y", "pose.position.z"],
        "group": "left",
        "offset": 0,
        "shape": [7],
        "dtype": "float32"
    }
]

camera_schema = [
    {
        "key": "left_image",
        "topic": "/gripper/camera_fisheye_l/color/image_raw",
        "role": "image",
        "group": "left"
    }
]
```

## 模块输出格式

```python
{
    "timestamp_list": timestamp_list,
    "image_list": image_list,
    "state_list": state_list,
    "action_list": action_list,
    "state_vector_list": state_vector_list,
    "action_vector_list": action_vector_list,
    "camera_schema": camera_schema,
    "state_schema": state_schema,
    "action_schema": action_schema
}
```

`camera_schema` 用于保留摄像头 key、原始 topic 和左右分组关系。`state_vector_list`、`action_vector_list`、`state_schema`、`action_schema` 是否输出由 `output_format` 控制。

## 异常处理规则

MVP 阶段采用 fail-fast 策略。

直接失败：

- 配置不是合法 JSON。
- `file_type` 不是 `mcap`。
- 输入路径不存在。
- 必需 topic 缺失。
- 主时间轴 topic 缺失。
- 相邻 MCAP 主相机时间缺口达到或超过内部允许阈值。
- 同 topic 同 timestamp 重复。
- `insert.max_tor_time_sec` 不是非负秒数。

可退化处理：

- 可选 topic 缺失：按 `missing_policy` 处理。
- 可选字段缺失：按 `missing_policy` 处理。
- 图像无法解码：保留 raw bytes，记录异常，交由数据质检模块处理。

错误日志必须包含：`task_id`、`job_id`、文件名、topic、`timestamp_ns`、错误类型和错误原因。

## MVP 验收标准

1. 可以通过运行时传入的本地 MCAP 路径完成解析；默认测试样例为 `tests/train_data_1.mcap`。
2. 可以处理单个 MCAP 和多个 MCAP 输入。
3. 输出 topic 数据按 `timestamp_ns` 排序。
4. 主时间轴输出单调递增。
5. camera、state、action 都能按配置输出。
6. `missing_policy` 的 `error`、`zero`、`previous` 都有测试覆盖。
7. 多 MCAP 重叠时间段保留前一个文件并记录后一个文件的丢弃帧数。
8. 大缺口报错，小缺口按规则补帧和插值。
9. 同一 MCAP 内同 topic 同 timestamp 重复会报错。
10. 文件名或上传顺序与记录时间顺序不一致时，始终按主相机记录时间排序。

## 更新记录

- 2026-07-15：确定多 MCAP 时间戳预扫描、前文件优先重叠裁剪、小缺口补齐、大缺口失败和统一 30 FPS 相对时间轴；全部新增策略均为前端不可见固定参数。
- 2026-07-13：对齐容差由纳秒参数 `max_tor_time` 改为秒参数 `max_tor_time_sec`。
- 2026-07-13：输出新增 `camera_schema`，组件 schema 新增 `role`、`fields` 和 `group`，支持下游按同组摄像头独立生成事件时间线。
- 2026-07-10：修正 `state_schema` 示例中的字段分隔符。
- 2026-07-07：补充配置层级、时间单位、多 MCAP 合并、图像 raw 信息、missing_policy、异常策略和 MVP 验收标准。
