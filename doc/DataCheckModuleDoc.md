# 数据质检模块设计文档

## 文档状态

- 文档类型：模块设计文档
- 所属流程：数据质检阶段
- 当前阶段：MVP阶段
- MVP 范围：对文档解析环节生成的state/action数据进行运动突变检测和极值超限检测，对生成的图像进行质量检测与过滤，并对相邻的异常帧/区间进行合并

## 功能描述

数据质检模块负责检测图像和机器人数据中的低质量片段，输出结构化异常记录，并合并相邻异常区间
当前阶段只需要开发运动突变检测，极值超限检测和图像质量检测，未来会添加更多的检测项目，比如状态-动作对齐检测，机器人运动学检测，方向对齐性检测等

## 输入

当前阶段，本模块的输入有两个来源：
1. 来源于文档解析模块输出的"timestamp_list", "image_list", "state_list", "action_list", "state_vector_list", "action_vector_list", "state_schema", "action_schema"
2. 来源于外部请求的参数信息。具体接受的参数见`接口定义`部分

MVP 阶段 `action_list`、`action_vector_list`、`action_schema` 只作为上游 Parser 产物保留，不参与状态-动作一致性检测；状态-动作一致性检测留到后续版本。

## 输出

本模块输出数据质检结果，供事件生成模块过滤低质量片段、供前端或人工复核模块展示异常原因。

MVP 阶段输出包括：

- `data_anomaly_ranges`：机器人数据异常区间，包括运动突变、极值超限和特殊值检测结果。
- `img_anomaly_ranges`：图像质量异常区间，包括黑帧、模糊帧、损坏帧和静止帧检测结果。

当未检测到异常时，仍然输出完整结构，其中异常区间列表为空

MVP 阶段不直接写入最终存储；输出对象由服务编排层决定是否保存为文件、数据库记录或传递给下游模块。

## 架构设计

本模块采用工厂模式加抽象基类的整体架构。

核心抽象：
- `DataCheckBase`: 数据检测基类。不同的数据检测项目实现不同检测器，比如运动突变检测实例，极值超限检测实例
- `ImageCheckBase`: 图像检测基类。不同的图像检测项目实现不同检测器，比如运动帧图像的质量检测实例
- `MergeBase`: 合并方法基类。不同的合并策略实现不同合并器

第一版实现：
- `MovingWindowDetector`: 对机器人数据采用“滑动窗口”的方式进行突变点检测
- `ExtremeValueDetector`： 对机器人数据进行极值检测
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
        "parser_info": parser_input
    },
    "data_detection": {
        "sudden_change_config": {
            "enable": true,
            "window_len": 15,
            "z_score": 3,
            "sudden_len":2,
            "step_len": 15,
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
        "window_len": 30,
        "lap_var": 150,
        "z_score": 2,
        "resize_length": 860,
        "resize_width": 640,
        "SSIM": 0.7,
        "pixel_mae": 5,
        "moving_area_ratio": 0.05
    },
    "merge_policy": {
        "min_low_quality_frames": 5,
        "max_gap_frames": 6
    }
}
```

### 字段说明
#### basic
- `task_id`：业务任务 ID，由服务入口传入或生成。
- `job_id`：运行任务 ID，由服务编排层生成。MVP 阶段可为空。
- `eps`: 微小增量用于防止计算时分母为0
- `parser_info`: 来自文档解析模块的输出对象

#### data_detection.sudden_change_config item
- `enable`: 是否启用突变检测模块 
- `window_len`: 突变检测的窗口长度 
- `z_score`: z分数阈值
- `sudden_len`: 尖峰帧阈值，当一个突变信号持续时间少于等于该阈值时，才会被视为spike类型突变
- `step_len`: 平台期阈值，当一个突变信号产生且数值保持新水平高度至少阈值时间后，才会被视为step类型
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

#### merge_policy
- `enable`: 
- `min_low_quality_frames`: 最小异常片段连续帧。如果一个异常片段的持续时间（帧的数量）少于该值，则认为片段为瞬间噪声，不视为异常片段
- `max_gap_frames`: 最大异常片段间隔帧。如果两个异常片段质检的间隔大于该值，则不进行合并；小于等于该值，则将两个异常片段合并为一个更长的异常片段


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
4. 数据融合
5. 格式化输出

### 机器人运动速度，加速度，加加速度的计算
1. 基于`state_schema`信息，定位"parser"类型为f"pose{x}d"的topic
2. 对每一个步骤一从获取的topic，从`state_list`中提取对应topic的数据
3. 对于每一个符合要求的state数据列表，提取state数据的前三位[x, y, z]，然后对位置数据进行平滑处理
4. 基于平滑后的位置数据，分别计算一阶导数（速度），二阶导数（加速度）和三阶导数（加加速度）
5. 将计算得到的运动数据，按照`timestamp_list`的时间戳信息进行对齐。如果无法计算开始时的运动信息，则默认开始时，速度，加速度和加加速度均为0

### 突变检测规则
目前实现滑动窗口法
1. 计算当前时刻Tn的过去`window_len`帧的运动数据信息，分别计算速度，加速度和加加速度的区间均值(range_mean)和区间标准差(range_std)
2. 计算当前时刻Tn的运动数据Z分数， Z = | Xn - range_mean| / (range_std + `eps`)
3. 如果Z分数大于`z_score`，视为突变
4. 对突变点进行分类
    - 瞬时突变（spike）：仅持续极短时间(`sudden_len`)的极大值，且数据在突然出现极高的Z值后迅速回落
    - 台阶跳变(step): 数据突然出现极大Z值，且运动数据在突变后保持一段时间(`step_len`)
    - 振荡(Oscillation): 在窗口区间内频繁正负波动。计算窗口区间的数据正负切换次数，如果大于`window_len` * `zcr_ratio`
5. 为每一帧生成突变检测结果，形成检测输出
6. 对于所有符合要求的state topic，重复上述过程

### 极值检测规则
包含极限值检测和特殊值检测
#### 极限值检测
1. 基于`degree`数据，计算百分比取值的上下限
2. 基于计算得到的运动数据（速度，加速度，加加速度），结合`expansion_coef`和`min_tor`, 计算运动数据的正常区间
3. 结合正常区间信息，对所有数据进行评估，为每一帧数据生成检测结果，形成检测输出
4. 对于所有符合要求的state topic，重复上述过程

#### 特殊值检测
用于检测运动和夹爪动作的特殊点
1. 全0速点检测
    - 结合'min_tor',找出运动数据（速度，加速度，加加速度）均为0的帧，考虑传感器精度，将 0 ± `min_tor` 的范围均视为0速
    - 输出检测结果
    - 对于所有符合要求的state topic，重复上述过程

2. 末端执行器状态跳变点检测
    - 基于`state_schema`信息，定位"parser"类型为f"gripper"的topic
    - 从`state_list`中提取对应topic的数据，并进行平滑处理
    - 对处理后的数据执行突变检测，并记录台阶跳变（step）的时间点
    - 为每一帧生成突变检测结果（是否step），形成检测输出
    - 对于所有符合要求的state topic，重复上述过程

#### 运动检测结果类型
异常编码统一采用事件生成模块的编码规则：数据异常使用 `1000-1999`，图像异常使用 `2000-2999`。

- `normal`: 0, 代表正常帧
- `spike`: 1001, 代表出现瞬时突变的帧
- `step`: 1002, 代表出现台阶跳变的帧
- `oscillation`: 1003, 代表出现振荡现象的帧
- `end_zero`: 1004，代表末端执行器全0速的帧
- `end_step`: 1005, 代表出现末端执行器的动作参数出现台阶跳变的帧

### 图像质量检测规则
#### 黑帧检测规则
- 计算当前图像的亮度/像素平均值，如果平均值低于`luminance`，视为黑帧

#### 模糊帧检测规则
和数据突变检测类似，使用窗口滑动法
- 对当前时刻的帧图像进行resize操作
- 对所有resize后的图像计算拉普拉斯方差，如果拉普拉斯方差小于`lap_var`，判定为模糊帧
- 如果图像的拉普拉斯方差大于绝对判定阈值：则对当前时刻Tn的过去`window_len`帧图像进行resize操作，并计算区间均值(range_mean)和区间标准差(range_std)
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
- 同类异常帧融合：连续出现至少"min_low_quality_frames"帧，才会被视为一个连续的异常，并进行融合
- 同类异常区间融合：两个异常区间如果间隔帧少于等于"max_gap_frames"，则可以认为是连续的异常区间，可以融合成一个更长的异常区间
3. 暂时不对异类异常区间进行融合（比如`spike`类型异常区间区间和`oscillation`类型异常区间融合）

## 数据 schema
1. 检测类结果输出两个对象： check_detail, check_list, 分别用于记录检测细节和时序检测结果.
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
            "anomaly_code": 1005,
            "anomaly_name": "end_step",
            "topic": "left_arm_state",
            "desc": "检测发现末端执行器动作(left_arm_state)出现台阶跳变"
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
    "data_anomaly_ranges": data_anomaly_ranges,
    "img_anomaly_ranges": img_anomaly_ranges
}
```

## 异常处理规则

MVP 阶段默认采用 fail-fast 策略。

### 直接失败

以下情况直接失败，并返回明确错误原因：

- 请求体不是合法 JSON 或缺少 `basic`、`data_detection`、`image_detection`、`merge_policy` 等必需配置段。
- `basic.task_id` 缺失。
- `basic.parser_info` 缺失，或不包含 `timestamp_list`、`image_list`、`state_list`、`action_list`、`state_schema`、`action_schema` 等必需字段。
- `timestamp_list` 为空。
- `timestamp_list` 不是按时间单调递增排列。
- `timestamp_list`、`image_list`、`state_list`、`action_list` 长度不一致。
- `state_schema` 或 `action_schema` 中声明的 key 无法在对应的 `state_list` 或 `action_list` 中找到。
- 启用机器人数据检测时，没有任何可用于运动检测的 `pose{x}d` state topic。
- 启用图像检测时，没有任何可用于图像检测的 camera 数据。
- 检测配置参数非法，例如窗口长度小于等于 0、阈值为负数、`degree` 不在 `(0, 1)` 范围内、`zcr_ratio` 不在 `[0, 1]` 范围内。
- `merge_policy.min_low_quality_frames` 或 `merge_policy.max_gap_frames` 小于 0。

### 可退化处理

以下情况不直接中断任务，但必须记录 warning，并在输出中保留可追踪信息：

- 某个非必需 topic 无法参与检测：跳过该 topic，继续处理其它 topic。
- 某个 topic 的有效帧数不足以形成完整滑动窗口：该 topic 不输出突变检测结果，或输出全 0 `check_list`，并记录原因。
- 速度、加速度、加加速度在序列起始位置无法计算：按当前设计默认填充 0。
- 单帧图像无法解码或缺少 decoded array：标记为 `corrupted`，继续处理后续图像。
- 静止帧检测缺少可用前一帧，或前一帧已存在黑帧、模糊帧、损坏帧等质量问题：跳过当前帧的静止帧判断，不将其判定为静止帧。
- 某个检测器未启用：输出该检测器对应的空结果，不视为错误。

### 空结果约定

如果输入合法但未检测到异常，模块仍然必须输出完整结构：

```python
{
    "check_list": [0, 0, 0, ...],
    "check_detail": {},
    "data_anomaly_ranges": [],
    "img_anomaly_ranges": []
}
```

如果某一类检测未启用，则对应异常区间列表为空。

### 日志字段

所有错误和 warning 日志必须包含以下字段，便于定位问题：

- `task_id`
- `job_id`
- 检测器名称
- topic 名称
- 帧 index
- `timestamp_ns`
- 异常类型
- 错误原因

### 输出一致性要求

- `check_list` 长度必须与 `timestamp_list` 长度一致。
- `check_detail` 的 key 必须是 `check_list` 中异常帧的 index。
- `data_anomaly_ranges` 和 `img_anomaly_ranges` 中的起止 index 必须能映射回 `timestamp_list`。
- 区间输出必须满足 `start_timestamp_index <= end_timestamp_index`。
- 区间输出必须满足 `start_timestamp_ns <= end_timestamp_ns`。

## MVP 验收标准

1. 使用 `tests/train_data_1.mcap` 经过 Parser 模块生成的上游产物，可以完成一次数据质检流程。
2. 在启用机器人数据检测时，模块能基于 `pose{x}d` state topic 计算速度、加速度和加加速度。
3. 突变检测能输出逐帧 `check_list` 和 `check_detail`，并能区分 `spike`、`step`、`oscillation` 三类异常。
4. 极值检测能基于 `degree`、`expansion_coef`、`min_tor` 生成正常区间，并输出超限异常结果。
5. 特殊值检测能识别末端执行器全 0 速点和 gripper step 跳变点。
6. 在启用图像检测时，模块能输出黑帧、模糊帧、损坏帧和静止帧检测结果。
7. 数据融合能按 `min_low_quality_frames` 过滤短异常片段，并按 `max_gap_frames` 合并同类相邻异常区间。
8. 正常输入且没有异常时，模块输出空的 `data_anomaly_ranges` 和 `img_anomaly_ranges`，并保持 `check_list` 全 0。
9. `check_list` 长度始终等于 `timestamp_list` 长度。
10. `check_detail` 中的异常 index 能映射到 `timestamp_list` 中的时间戳。
11. `data_anomaly_ranges` 和 `img_anomaly_ranges` 中的起止 index、起止时间戳单调合法。
12. 融合后的 `data_anomaly_ranges` 和 `img_anomaly_ranges` 必须包含 `topics` 和 `descs`，用于保留多 topic 异常来源和描述。
13. 缺少 `parser_info`、`timestamp_list` 为空、列表长度不一致、必需 schema key 缺失时，模块直接失败并返回明确错误。
14. 非必需 topic 无法检测、滑动窗口帧数不足、单帧图像无法解码等可退化场景不会中断整个任务，并会记录 warning。
15. 当 `data_detection` 或 `image_detection` 关闭时，对应检测结果为空，但模块仍能输出合法结构。
16. 所有失败和 warning 日志都包含 `task_id`、`job_id`、检测器名称、topic、帧 index、`timestamp_ns` 和错误原因。

## 更新记录

- 2026-07-10：统一异常编码规则，融合后异常区间新增 `topics` 和 `descs` 字段。
- 2026-07-09：补充输出摘要，修正字段命名、伪代码示例、timestamp 区间字段和 action 检测范围说明。
- 2026-07-07：统一文档格式，补充待设计章节。
