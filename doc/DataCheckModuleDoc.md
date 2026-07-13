# 数据质检模块设计文档

## 文档状态

- 文档类型：模块设计文档
- 所属流程：数据质检阶段
- 当前阶段：MVP阶段
- MVP 范围：对 Parser 输出的 state/action 数据进行运动突变检测和极值超限检测，对图像进行质量检测与过滤；同时从与事件高度相关的 topic 数据中检测并记录 trigger 候选点。trigger 是客观检测事实，不属于质量异常，不参与异常区间融合，也不在本模块内决定是否成为最终事件节点。

## 功能描述

数据质检模块包含“质量检测”和“trigger 检测”两条相互独立的处理支路：质量检测负责发现图像及机器人数据中的低质量片段并输出结构化异常记录；trigger 检测负责根据 topic 时序信号识别客观变化点，供事件生成模块进行业务筛选。

当前阶段实现运动突变检测、极值超限检测、图像质量检测和末端执行器状态变化点检测。未来可扩展状态-动作对齐检测、机器人运动学检测、方向对齐性检测和更多 trigger 检测器。

## 输入

当前阶段，本模块的输入有两个来源：
1. 来源于文档解析模块输出的"timestamp_list", "image_list", "state_list", "action_list", "state_vector_list", "action_vector_list", "state_schema", "action_schema"
2. 来源于外部请求的参数信息。具体接受的参数见`接口定义`部分

MVP 阶段 `action_list`、`action_vector_list`、`action_schema` 只作为上游 Parser 产物保留，不参与状态-动作一致性检测；状态-动作一致性检测留到后续版本。

## 输出

本模块输出两类结果：质量异常记录供前端、人工复核或后续清洗流程使用；`trigger_points` 供事件生成模块进行节点策略判断。两类结果互不替代。

MVP 阶段输出包括：

- `data_anomaly_ranges`：机器人数据质量异常区间，包括运动突变和极值超限检测结果。
- `img_anomaly_ranges`：图像质量异常区间，包括黑帧、模糊帧、损坏帧和静止帧检测结果。
- `trigger_points`：trigger 候选点列表，记录检测器从 topic 数据中发现的客观变化点及其证据。该字段不等价于最终 `event_points`。

当未检测到异常时，仍然输出完整结构，其中异常区间列表为空

MVP 阶段不直接写入最终存储；输出对象由服务编排层决定是否保存为文件、数据库记录或传递给下游模块。

## 架构设计

本模块采用工厂模式加抽象基类的整体架构。

核心抽象：
- `DataCheckBase`: 数据检测基类。不同的数据检测项目实现不同检测器，比如运动突变检测实例，极值超限检测实例
- `ImageCheckBase`: 图像检测基类。不同的图像检测项目实现不同检测器，比如运动帧图像的质量检测实例
- `TriggerCheckBase`: trigger 检测基类。不同的信号变化点检测方法实现不同检测器，例如末端执行器状态变化检测
- `MergeBase`: 合并方法基类。不同的合并策略实现不同合并器

第一版实现：
- `MovingWindowDetector`: 对机器人数据采用“滑动窗口”的方式进行突变点检测
- `ExtremeValueDetector`： 对机器人数据进行极值检测
- `EndEffectorDetector`: 使用末端执行器 topic 数据检测状态变化 trigger
- `ImageQualityDetector`： 对图像进行质量检测
- `NearMerger`： 实现异常低质量片段的相邻合并策略

## 接口定义

### 请求体格式：

以下示例为伪代码，其中 `parser_input` 代表输入解析模块的输出对象。

```text
{
    "basic": {
        "task_id": "TASK-001",
        "job_id": "JOB-001",
        "eps": 1e-9,
        "fps": 30,
        "parser_info": parser_input,
        "smooth": {
            "method": "savgol",
            "window_frame_length": 10,
            "polyorder": 3
        }
    },
    "data_detection": {
        "sudden_change_config": {
            "enable": true,
            "window_time_sec": 0.5,
            "z_score": 3,
            "sudden_time_sec": 0.066666667,
            "step_time_sec": 0.5,
            "zcr_ratio": 0.4
        },
        "extreme_value_config": {
            "enable": true,
            "degree": 0.01,
            "expansion_coef": 0.2,
            "min_tor": 1e-4
        }
    },
    "image_detection": {
        "enable": true,
        "luminance": 10,
        "window_time_sec": 1.0,
        "lap_var": 150,
        "z_score": 2,
        "resize_length": 860,
        "resize_width": 640,
        "SSIM": 0.7,
        "pixel_mae": 5,
        "moving_area_ratio": 0.05
    },
    "trigger_detection": {
        "mode": "end_effector",
        "params": {
            "model": "clinear",
            "algorithm": "Pelt",
            "pen": 15,
            "min_duration_sec": 0.666666667,
            "jump_frames": 1,
            "state_count": 3,
            "feature_window_sec": 0.166666667,
            "stay_probability": 0.995,
            "candidate_sigma_sec": 1.333333333,
            "candidate_bonus": 1
        }
    },
    "merge_policy": {
        "min_low_quality_time_sec": 0.166666667,
        "max_gap_time_sec": 0.2
    }
}
```

### 字段说明
#### basic
- `task_id`：业务任务 ID，由服务入口传入或生成。
- `job_id`：运行任务 ID，由服务编排层生成。MVP 阶段可为空。
- `eps`: 微小增量用于防止计算时分母为0
- `fps`: 秒参数换算为内部帧数时使用的帧率，MVP 固定为 30
- `parser_info`: 来自文档解析模块的输出对象
- `smooth`: DataCheck 数值时序数据共享的平滑配置。MVP 使用 Savitzky-Golay；窗口继续以帧表达，因此字段名为 `window_frame_length = 10`，`polyorder = 3`

#### data_detection.sudden_change_config item
- `enable`: 是否启用突变检测模块 
- `window_time_sec`: 突变检测窗口时长，单位秒，默认 0.5 秒（30 FPS 下为 15 帧）
- `z_score`: z分数阈值
- `sudden_time_sec`: 尖峰持续时间阈值，单位秒，默认约 0.067 秒（2 帧）
- `step_time_sec`: 平台期持续时间阈值，单位秒，默认 0.5 秒（15 帧）
- `zcr_ratio`: 过零次数比例，用于判断振荡(oscillation)突变

#### data_detection.extreme_value_config item
- `enable`:  是否启用极值检测模块
- `degree`: 估算极值时的采样百分比，正常百分比区间为(min(degree, 1-degree), max(degree, 1-degree))
- `expansion_coef`: 膨胀系数
- `min_tor`: 最小容忍度

#### image_detection
- `enable`: 是否启用图像质检检索模块
- `luminance`: 黑帧检测中，图像亮度阈值
- `lap_var`: 模糊帧检测中，拉普拉斯方差阈值
- `z_score`: 模糊帧检测中，Z分数阈值
- `resize_length`: 模糊检测中，图像resize长度
- `resize_width`: 模糊检测中，图像resize宽度
- `SSIM`: 损坏帧检测中，SSIM的绝对阈值
- `pixel_mae`: 静止帧检测中，像素平均误阈值
- `moving_area_ratio`: 静止帧检测中，真实运动像素比例阈值

#### trigger_detection
- `mode`: trigger 检测策略。MVP 仅支持 `end_effector`，用于检测末端执行器状态变化点
- `params`: 策略参数，不同的策略可能需要传入不同的参数
- `params.model`: ruptures 候选点模型，MVP 固定为 `clinear`，用于寻找分段线性趋势边界
- `params.algorithm`: ruptures 中使用的变化点检测算法，MVP 默认为 `Pelt`
- `params.pen`: 算法惩罚系数，MVP 默认为 15
- `params.min_duration_sec`: 最小状态持续时间，单位秒。该参数同时约束 ruptures 候选分段和 HSMM 状态持续时间，默认约 0.667 秒，内部按 30 FPS 换算为 20 帧
- `params.jump_frames`: ruptures 搜索步长。该参数由算法按离散样本执行，继续使用帧，默认 1 帧
- `params.state_count`: HSMM 基础状态数，MVP 默认为 3，聚类后自动识别稳定状态，其余状态统一视为运动过程
- `params.feature_window_sec`: 局部角度变化范围的尾随窗口，单位秒，默认约 0.167 秒（5 帧）
- `params.stay_probability`: HSMM 成熟状态的自转移先验，MVP 默认为 0.995
- `params.candidate_sigma_sec` / `params.candidate_bonus`: clinear 候选点对 HSMM 状态切换的软先验时间范围（秒）和权重

检测器默认继承 `basic.smooth`。后续若某个检测器需要不同的平滑方法，可以增加检测器级可选覆盖参数；未配置时仍使用 `basic.smooth`。

#### merge_policy
- `min_low_quality_time_sec`: 最小异常片段持续时间，单位秒，默认约 0.167 秒（5 帧）
- `max_gap_time_sec`: 可合并异常片段的最大间隔，单位秒，默认 0.2 秒（6 帧）

## 功能逻辑
1. 请求解析。 判断是否进行数据检测。如果启用数据检测模块，解析和提取`parser_info`中的信息
2. 机器人数据检测。MVP 阶段使用 `parser_info` 的 `timestamp_list`、`state_list`、`state_schema` 进行数据检测；`action_list`、`action_vector_list`、`action_schema` 暂不参与检测，仅保留给后续状态-动作一致性检测使用
    - 计算机器人运动速度，加速度，加加速度
    - 针对运动速度，加速度，加加速度进行突变检测，并记录突变检测结果
    - 针对运动速度，加速度，加加速度进行极值检测，并记录极值检测结果
    - 输出检测结果
3. 图像质量检测：对图像按时间序列依次处理
    - resize图像，统一图像size
    - 对图像进行黑帧检测
    - 对图像进行模糊帧检测
    - 对图像进行损坏帧检测
    - 对图像进行静止帧检测
4. trigger 检测：从符合配置的 topic 时序数据中检索并记录 trigger 候选点及检测证据
5. 数据融合：只融合质量异常，不融合 trigger
6. 分别格式化异常结果和 `trigger_points`

### 机器人运动速度，加速度，加加速度的计算
1. 基于`state_schema`信息，定位"parser"类型为f"pose{x}d"的topic
2. 对每一个步骤一从获取的topic，从`state_list`中提取对应topic的数据
3. 对于每一个符合要求的 state 数据列表，提取 state 数据的前三位 `[x, y, z]`，然后按 `basic.smooth` 对位置数据进行平滑处理
4. 基于平滑后的位置数据，分别计算一阶导数（速度），二阶导数（加速度）和三阶导数（加加速度）
5. 将计算得到的运动数据，按照`timestamp_list`的时间戳信息进行对齐。如果无法计算开始时的运动信息，则默认开始时，速度，加速度和加加速度均为0

### 突变检测规则
目前实现滑动窗口法
1. 将 `window_time_sec` 按 `fps=30` 换算为内部帧数，计算当前时刻过去窗口内运动数据的均值和标准差
2. 计算当前时刻Tn的运动数据Z分数， Z = | Xn - range_mean| / (range_std + `eps`)
3. 如果Z分数大于`z_score`，视为突变
4. 对突变点进行分类
    - 瞬时突变（spike）：仅持续不超过 `sudden_time_sec` 的极大值，且随后迅速回落
    - 台阶跳变(step): 数据突然出现极大Z值，且运动数据在突变后保持至少 `step_time_sec`
    - 振荡(Oscillation): 在窗口区间内频繁正负波动，切换比例超过 `zcr_ratio`
5. 为每一帧生成突变检测结果，形成检测输出
6. 对于所有符合要求的state topic，重复上述过程

### 极值检测规则
MVP 仅包含极限值检测；末端执行器状态变化由独立的 trigger 检测流程处理。
#### 极限值检测
1. 基于`degree`数据，计算百分比取值的上下限
2. 基于计算得到的运动数据（速度，加速度，加加速度），结合`expansion_coef`和`min_tor`, 计算运动数据的正常区间
3. 结合正常区间信息，对所有数据进行评估，为每一帧数据生成检测结果，形成检测输出
4. 对于所有符合要求的state topic，重复上述过程

### trigger 检测

trigger 检测回答“topic 数据中发生了什么变化”，不回答“该变化是否应切分事件”。最终事件节点的选择由事件生成模块完成。

MVP 使用 `clinear + HSMM` 检测末端执行器的状态变化边界：

1. 基于 `state_schema` 定位 `role = "end_effector"` 的 topic。
2. 从已经对齐到主图像时间轴的 `state_list` 提取 `angle`，优先读取 topic 上报的 `velocity`；缺少速度字段时才按主时间轴对平滑角度求导。
3. 按共享配置 `basic.smooth` 平滑角度，并以速度绝对值和尾随窗口内的角度变化范围构造运动特征；特征只做稳健标准化，不写死目标角度。
4. 使用 ruptures `Pelt(model="clinear")` 生成候选边界。候选只作为状态切换软先验，不直接输出为 trigger；序列末尾边界 `len(signal)` 被忽略。
5. 对运动特征自动聚类出 3 个基础状态，将特征均值最低的状态识别为稳定状态，其余状态统一映射为运动状态。
6. 将 `min_duration_sec` 按 30 FPS 换算为内部帧数，并同时约束 clinear 分段和 HSMM 状态的最短持续时间，以抑制过度切分。
7. 稳定状态与非稳定状态之间的每次切换均只输出为无语义标签的变化节点，不增加“运动开始”或“恢复稳定”等 `trigger_type`。
8. 通过 state topic 的 `group` 将变化节点关联到同组的摄像头 topic；每个摄像头分别输出节点。检测来源保存在 `evidence.detection_topic_key` 和 `evidence.detection_source_topic` 中。
9. 对所有符合条件的末端执行器 topic 重复上述过程，将结果汇总为 `trigger_points`。trigger 不写入 `check_list` / `check_detail`，也不进入异常区间融合。

#### 运动检测结果类型
质量检测结果使用独立的异常编码：数据异常使用 `1000-1999`，图像异常使用 `2000-2999`。

- `normal`: 0, 代表正常帧
- `spike`: 1001, 代表出现瞬时突变的帧
- `step`: 1002, 代表出现台阶跳变的帧
- `oscillation`: 1003, 代表出现振荡现象的帧
trigger 不复用质量异常编码，也不携带运动阶段类型；其语义仅为“该摄像头关联的末端执行器在此处出现状态边界”。

### 图像质量检测规则
#### 黑帧检测规则
- 计算当前图像的亮度/像素平均值，如果平均值低于`luminance`，视为黑帧

#### 模糊帧检测规则
和数据突变检测类似，使用窗口滑动法
- 对当前时刻的帧图像进行resize操作
- 对所有resize后的图像计算拉普拉斯方差，如果拉普拉斯方差小于`lap_var`，判定为模糊帧
- 如果图像的拉普拉斯方差大于绝对判定阈值：按 `window_time_sec` 对过去时间窗口内图像计算区间均值和标准差
- 计算当前时刻Tn的图像拉普拉斯方差Z分数， Z = | Xn - range_mean| / (range_std + `eps`)
- 如果Z分数大于`z_score`，同样视为模糊帧

#### 损坏帧检测规则
- 读取视频或图像时，如果发现损坏帧，直接进行归类标记
- 记录和维度最新的非损坏帧位置，并用该帧图像作为对照参与结构相似度计算
- 计算当前时刻Tn和最新的非损坏帧图像的结构相似度（SSIM）
- 如果计算得到SSIM值大于`SSIM`视为非损坏帧，否则视为损坏帧
- 初始化污染的应对方案：如果视频的第一帧不存在其他图像问题（黑帧，模糊帧，损坏帧），则视第一帧为正常帧

#### 静止帧检测规则
1. 如果存在前一帧(Tn-1)且Tn-1没有质量问题：
- 对相邻的两帧图像(Tn, Tn-1)转为灰度图，然后计算两帧图像的绝对差值矩阵
- 求整个矩阵的平均值，如果均值小于等于`pixel_mae`，视为静止帧
- 统计当前帧图像中的“真实运动像素”比例。如果小于"moving_area_ratio"，视为静止帧
2. 如果前一帧不存在或前一帧存在质量问题（损坏帧，黑帧，模糊帧等），不进行禁止帧检测，当前帧不会被判定为静止帧

#### 图像检测结果类型
- `normal`: 0, 代表正常帧
- `black`: 2001, 代表黑帧
- `blur`: 2002, 代表模糊帧
- `corrupted`: 2003，代表损坏帧
- `still`: 2004, 代表静止帧


### 数据融合规则
1. 运动数据类/图像类异常分别处理
2. 同类型异常融合
- 同类异常帧融合：连续持续至少 `min_low_quality_time_sec` 才视为异常片段
- 同类异常区间融合：两个异常区间的时间间隔小于等于 `max_gap_time_sec` 时进行融合
3. 暂时不对异类异常区间进行融合（比如`spike`类型异常区间区间和`oscillation`类型异常区间融合）

## 数据 schema
1. 逐帧质量检测输出 `check_list` 和 `check_detail`；trigger 检测独立输出 `trigger_points`。
`check_list`: list, 记录每一帧是否被检出异常，异常帧为1,正常帧为0， 列表长度和`timestamp_list`的长度相等；
示例： 
```
check_list = [0, 0, 1, 0, 1, 0, 1, ....]
```

`check_detail`: 字典，以`check_list`中的异常帧索引(index)为键，记录了对应帧的异常信息；
示例：
```
check_detail = {
    2: [
        {
            "anomaly_code": 1001,
            "anomaly_name": "spike",
            "topic": "left_arm_state",
            "desc": "检测发现速度(left_arm_state)存在瞬时突变"
        },
        {
            "anomaly_code": 2002,
            "anomaly_name": "blur",
            "topic": "right_image",
            "desc": "检测发现图像(right_image)存在模糊帧"
        },
        ...
    ],
    ...
}
```
注意：同一帧可能存在多种异常问题

`trigger_points`: 列表，记录各 trigger 检测器发现的候选点。它们是事件生成模块的输入，不是最终事件节点。
示例
```
[
    {
        "topic_key": "left_image",
        "source_topic": "/gripper/camera_fisheye_l/color/image_raw",
        "timestamp_ns": "1000000000",
        "timestamp_sec": "1.000000000",
        "timestamp_index": 3,
        "evidence": {
            "model": "clinear+hsmm",
            "detection_topic_key": "left_gripper",
            "detection_source_topic": "/gripper/gripper_l/data",
            "nearest_clinear_index": 3,
            "angle": 0.42,
            "velocity": 0.18,
            "min_duration_sec": 0.666666667
        }
    }
]
```

2. 数据融合后的最终输出格式

```
data_anomaly_ranges = [
    {
        "start_timestamp_ns": "1000000000",
        "start_timestamp_sec": "1.000000000",
        "start_timestamp_index": 3,
        "end_timestamp_ns": "1200000000",
        "end_timestamp_sec": "1.200000000",
        "end_timestamp_index": 10,
        "anomaly_code": 1003,
        "anomaly_name": "oscillation",
        "topics": ["left_arm_state", "right_arm_state"],
        "descs": {
            "left_arm_state": ["检测发现速度(left_arm_state)存在振荡"],
            "right_arm_state": ["检测发现速度(right_arm_state)存在振荡"]
        }
    },
    ...
]

img_anomaly_ranges = [
    {
        "start_timestamp_ns": "1000000000",
        "start_timestamp_sec": "1.000000000",
        "start_timestamp_index": 3,
        "end_timestamp_ns": "1200000000",
        "end_timestamp_sec": "1.200000000",
        "end_timestamp_index": 10,
        "anomaly_code": 2003,
        "anomaly_name": "corrupted",
        "topics": ["right_image"],
        "descs": {
            "right_image": ["检测发现图像(right_image)存在损坏帧"]
        }
    },
    ...
]
```

字段说明：
- `anomaly_code`: 异常类型代码
- `anomaly_name`: 异常类型名称
- `topics`: 发生该类异常的 topic 名称列表。融合后的异常片段可能来自多个 topic，因此使用列表承载。
- `descs`: 异常描述字典。键为 topic 名称，值为该 topic 对应的异常描述列表，格式为 f"检测发现{数据类型}({topic名称}){异常类型名称}"。
- `start_timestamp_ns`: 异常区间的起点时间戳，字符串格式，单位纳秒
- `start_timestamp_sec`: 异常区间的起点时间戳，字符串格式，单位秒
- `start_timestamp_index`: 异常区间的起点时间戳在`timestamp_list`中的索引
- `end_timestamp_ns`: 异常区间的终点时间戳，字符串格式，单位纳秒
- `end_timestamp_sec`: 异常区间的终点时间戳，字符串格式，单位秒
- `end_timestamp_index`: 异常区间的终点时间戳在`timestamp_list`中的索引

## 模块输出格式

```python
{
    "check_list": check_list,
    "check_detail": check_detail,
    "data_anomaly_ranges": data_anomaly_ranges,
    "img_anomaly_ranges": img_anomaly_ranges,
    "trigger_points": trigger_points
}
```

## 异常处理规则

MVP 阶段默认采用 fail-fast 策略。

### 直接失败

以下情况直接失败，并返回明确错误原因：

- 请求体不是合法 JSON 或缺少 `basic`、`data_detection`、`image_detection`、`trigger_detection`、`merge_policy` 等必需配置段。
- `basic.task_id` 缺失。
- `basic.parser_info` 缺失，或不包含 `timestamp_list`、`image_list`、`state_list`、`action_list`、`state_schema`、`action_schema` 等必需字段。
- `timestamp_list` 为空。
- `timestamp_list` 不是按时间单调递增排列。
- `timestamp_list`、`image_list`、`state_list`、`action_list` 长度不一致。
- `state_schema` 或 `action_schema` 中声明的 key 无法在对应的 `state_list` 或 `action_list` 中找到。
- 启用机器人数据检测时，没有任何可用于运动检测的 `pose{x}d` state topic。
- 启用图像检测时，没有任何可用于图像检测的 camera 数据。
- 检测配置参数非法，例如窗口长度小于等于 0、阈值为负数、`degree` 不在 `(0, 1)` 范围内、`zcr_ratio` 不在 `[0, 1]` 范围内。
- `merge_policy.min_low_quality_time_sec` 或 `merge_policy.max_gap_time_sec` 小于 0。
- trigger 检测配置非法，例如 `mode` 未注册、`pen` 为负数或 `min_duration_sec` 不为正数。

### 可退化处理

以下情况不直接中断任务，但必须记录 warning，并在输出中保留可追踪信息：

- 某个非必需 topic 无法参与检测：跳过该 topic，继续处理其它 topic。
- 某个 topic 的有效帧数不足以形成完整滑动窗口：该 topic 不输出突变检测结果，或输出全 0 `check_list`，并记录原因。
- 速度、加速度、加加速度在序列起始位置无法计算：按当前设计默认填充 0。
- 单帧图像无法解码或缺少 decoded array：标记为 `corrupted`，继续处理后续图像。
- 静止帧检测缺少可用前一帧，或前一帧已存在黑帧、模糊帧、损坏帧等质量问题：跳过当前帧的静止帧判断，不将其判定为静止帧。
- 某个检测器未启用：输出该检测器对应的空结果，不视为错误。
- 某个非必需末端执行器 topic 数据不足以执行变化点检测：跳过该 topic，并记录 warning。

### 空结果约定

如果输入合法但未检测到异常和 trigger，模块仍然必须输出完整结构：

```python
{
    "check_list": [0, 0, 0, ...],
    "check_detail": {},
    "data_anomaly_ranges": [],
    "img_anomaly_ranges": [],
    "trigger_points": []
}
```

如果某一类检测未启用，则对应异常区间或 trigger 列表为空。

### 日志字段

所有错误和 warning 日志必须包含以下字段，便于定位问题：

- `task_id`
- `job_id`
- 检测器名称
- topic 名称
- 帧 index
- `timestamp_ns`
- 异常类型；对于无标签 trigger 记录检测器模型和来源 topic
- 错误原因

### 输出一致性要求

- `check_list` 长度必须与 `timestamp_list` 长度一致。
- `check_detail` 的 key 必须是 `check_list` 中异常帧的 index。
- `data_anomaly_ranges` 和 `img_anomaly_ranges` 中的起止 index 必须能映射回 `timestamp_list`。
- `trigger_points` 中的 `timestamp_index` 和时间戳必须能映射回同一条 `timestamp_list` 记录。
- 区间输出必须满足 `start_timestamp_index <= end_timestamp_index`。
- 区间输出必须满足 `start_timestamp_ns <= end_timestamp_ns`。

## MVP 验收标准

1. 使用 `tests/train_data_1.mcap` 经过 Parser 模块生成的上游产物，可以完成一次数据质检流程。
2. 在启用机器人数据检测时，模块能基于 `pose{x}d` state topic 计算速度、加速度和加加速度。
3. 突变检测能输出逐帧 `check_list` 和 `check_detail`，并能区分 `spike`、`step`、`oscillation` 三类异常。
4. 极值检测能基于 `degree`、`expansion_coef`、`min_tor` 生成正常区间，并输出超限异常结果。
5. `EndEffectorDetector` 能从 `role = "end_effector"` 的 topic 检测状态变化 trigger，并输出独立的 `trigger_points`。
6. 在启用图像检测时，模块能输出黑帧、模糊帧、损坏帧和静止帧检测结果。
7. 数据融合能按 `min_low_quality_time_sec` 过滤短异常片段，并按 `max_gap_time_sec` 合并同类相邻异常区间。
8. 正常输入且没有异常或 trigger 时，模块输出空的 `data_anomaly_ranges`、`img_anomaly_ranges` 和 `trigger_points`，并保持 `check_list` 全 0。
9. `check_list` 长度始终等于 `timestamp_list` 长度。
10. `check_detail` 中的异常 index 能映射到 `timestamp_list` 中的时间戳。
11. `data_anomaly_ranges` 和 `img_anomaly_ranges` 中的起止 index、起止时间戳单调合法。
12. 融合后的 `data_anomaly_ranges` 和 `img_anomaly_ranges` 必须包含 `topics` 和 `descs`，用于保留多 topic 异常来源和描述。
13. 缺少 `parser_info`、`timestamp_list` 为空、列表长度不一致、必需 schema key 缺失时，模块直接失败并返回明确错误。
14. 非必需 topic 无法检测、滑动窗口帧数不足、单帧图像无法解码等可退化场景不会中断整个任务，并会记录 warning。
15. 当 `data_detection` 或 `image_detection` 关闭时，对应检测结果为空，但模块仍能输出合法结构。
16. 所有失败和 warning 日志都包含 `task_id`、`job_id`、检测器名称、topic、帧 index、`timestamp_ns` 和错误原因。
17. trigger 不写入异常 `check_list` / `check_detail`，不参与异常区间融合，也不在本模块内被判定为最终事件节点。

## 更新记录

- 2026-07-13：时间配置统一使用秒并以 `_sec` 命名，内部固定按 30 FPS 换算；仍按帧执行的平滑窗口和搜索步长改为带 `frame` 的字段名。
- 2026-07-13：变化节点取消“运动开始/恢复稳定”标签；节点按摄像头 topic 独立输出，并在 evidence 中保留末端执行器检测来源。
- 2026-07-13：末端执行器 trigger 检测改为 `clinear + 三状态 HSMM`；输入先对齐到主图像帧时间轴，`min_duration_sec` 统一约束候选分段与状态最短持续时间。
- 2026-07-13：将 `smooth` 恢复为 `basic` 下的共享预处理参数，统一供运动数据检测和 trigger 检测使用，并预留检测器级覆盖能力。
- 2026-07-13：新增 `TriggerCheckBase` / `EndEffectorDetector` 和 ruptures 变化点检测流程；明确输出为候选 `trigger_points`，与质量异常及最终事件节点解耦。
- 2026-07-10：统一异常编码规则，融合后异常区间新增 `topics` 和 `descs` 字段。
- 2026-07-09：补充输出摘要，修正字段命名、伪代码示例、timestamp 区间字段和 action 检测范围说明。
- 2026-07-07：统一文档格式，补充待设计章节。
