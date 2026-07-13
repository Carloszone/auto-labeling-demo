# 事件生成模块设计文档

## 文档状态

- 文档类型：模块设计文档
- 所属流程：事件生成与切分阶段
- 当前阶段：MVP 设计
- MVP 范围：消费 DataCheck 输出的无标签变化节点，按摄像头 topic 独立生成事件节点与事件区间

## 功能描述

事件生成模块是“检测事实”与“业务事件”之间的策略层。DataCheck 负责检测变化位置，本模块负责将合法 trigger 转为事件节点，并构造事件区间。

本模块不读取原始 MCAP，不重复执行平滑、clinear 或 HSMM，也不从质量异常区间推导节点。当前不执行额外业务筛选，不为节点增加“运动开始”“恢复稳定”等阶段标签。

多个摄像头 topic 必须保持相互独立：节点按 `topic_key` 分组、组内排序和配对，禁止跨 topic 组成区间。

## 输入

1. DataCheck 输出的 `trigger_points`。
2. Parser 输出的 `timestamp_list`，用于校验节点并生成半闭半开区间。
3. 节点筛选策略和区间配对策略。

`data_anomaly_ranges` 和 `img_anomaly_ranges` 不作为事件节点来源。

## 输出

```text
{
    "task_id": "TASK-001",
    "job_id": "JOB-001",
    "event_points": {
        "left_image": {
            1: {
                "timestamp_ns": "1000000000",
                "timestamp_sec": "1.000000000",
                "timestamp_index": 30,
                "topic_key": "left_image",
                "source_topic": "/gripper/camera_fisheye_l/color/image_raw",
                "source_trigger_index": 0
            },
            2: {
                "timestamp_ns": "2000000000",
                "timestamp_sec": "2.000000000",
                "timestamp_index": 60,
                "topic_key": "left_image",
                "source_topic": "/gripper/camera_fisheye_l/color/image_raw",
                "source_trigger_index": 1
            }
        }
    },
    "event_periods": {
        "left_image": [
            {
                "start_event_order_id": 1,
                "end_event_order_id": 2,
                "topic_key": "left_image",
                "source_topic": "/gripper/camera_fisheye_l/color/image_raw",
                "start_index": 30,
                "end_index": 59,
                "startTimeNs": "1000000000",
                "endTimeNs": "1966666667"
            }
        ]
    }
}
```

- `event_points`：按摄像头 `topic_key` 分组；每组内部按时间排序，`order_id` 从 1 开始。
- `event_periods`：按同一 `topic_key` 分组的半闭半开区间列表。
- `topic_key` / `source_topic`：区间所属摄像头的结构化 key 和原始 topic，供后续筛选。
- `source_trigger_index`：输入 `trigger_points` 中的位置，用于追溯。

空输入返回 `event_points: {}` 和 `event_periods: {}`。某个 topic 只有一个节点时，保留该 topic 的节点，区间列表为空。

## 架构设计

核心抽象：

- `TriggerFilterBase`：决定候选 trigger 是否成为事件节点。
- `EventPairingBase`：决定同一 topic 内的节点如何形成区间。
- `EventGenerationBase`：组织校验、分组、筛选、排序、配对和格式化。

MVP 实现：

- `PassThroughTriggerFilter`：不附加业务筛选条件，接受所有 schema 合法的无标签变化节点。
- `AdjacentByTopicPairing`：按摄像头 topic 分组，并对组内相邻节点依次配对。
- `TriggerEventGenerator`：串联上述流程。

## 接口定义

```text
{
    "basic": {
        "task_id": "TASK-001",
        "job_id": "JOB-001",
        "check_info": check_input,
        "parser_info": parser_input
    },
    "point_policy": {"mode": "pass_through"},
    "pairing_policy": {"mode": "adjacent_by_topic"}
}
```

- `check_info.trigger_points`：DataCheck 输出的无标签变化节点。
- `parser_info.timestamp_list`：Parser 主时间轴。
- `point_policy.mode`：MVP 仅支持 `pass_through`。
- `pairing_policy.mode`：MVP 仅支持 `adjacent_by_topic`。

最小持续时间由 DataCheck 的 HSMM `min_duration_sec` 控制，本模块不维护第二套同义长度参数。

## 功能逻辑

1. 校验 `trigger_points`、`timestamp_list` 和策略参数。
2. 校验每个 trigger 的 index 与时间戳是否匹配主时间轴。
3. 使用 `PassThroughTriggerFilter` 接受合法 trigger。
4. 按 `topic_key` 分组；每组内部按时间排序，对同 topic、同 index 的重复节点去重。
5. 在各 topic 内独立分配连续 `order_id`，生成 `event_points`。
6. 使用 `AdjacentByTopicPairing` 生成分组后的 `event_periods`。
7. 保留摄像头 topic 和 trigger 来源追溯信息。

## 事件节点规则

1. 节点只代表变化位置，不添加运动阶段标签。
2. 当前无额外筛选规则，schema 和时间位置合法的 trigger 全部成为事件节点。
3. 图像质量异常和机器人数据质量异常不直接生成事件节点。
4. 节点不进行距离合并；同 topic、同 index 的完全重复节点只保留一个。
5. 不同摄像头 topic 的节点分别编号和输出。

## 事件区间配对规则

1. 先按摄像头 `topic_key` 将节点划分为相互独立的时间序列。
2. 每个 topic 内，将第 1、2 个节点组成第 1 段，第 2、3 个节点组成第 2 段，以此类推。
3. 因此某个 topic 有 N 个节点时，固定生成 N−1 个事件区间。
4. 不允许使用其他 topic 的节点作为当前 topic 区间的起点或终点。
5. 区间采用半闭半开语义：通常输出到后一个节点的前一帧；若后一个节点位于最后一帧，允许使用最后一帧。

## 数据 schema

### event_points

- 第一层 key：摄像头 `topic_key`。
- 第二层 key：topic 内从 1 开始的 `order_id`。
- 节点字段：`timestamp_ns`、`timestamp_sec`、`timestamp_index`、`topic_key`、`source_topic`、`source_trigger_index`、`evidence`。

### event_periods

- 第一层 key：摄像头 `topic_key`。
- 值：该 topic 独立生成的区间列表。
- 区间字段：`start_event_order_id`、`end_event_order_id`、`topic_key`、`source_topic`、`start_index`、`end_index` 及标准时间字段。

## 异常处理规则

### 必须报错

- 缺少 `basic`、`task_id`、`check_info.trigger_points`、`parser_info.timestamp_list` 或策略配置。
- trigger 缺少 topic、时间戳或 index。
- `timestamp_list` 为空或非严格递增，trigger index 越界，或时间戳与 index 不一致。
- `point_policy.mode` 或 `pairing_policy.mode` 未注册。

### 可退化处理

- trigger 为空：返回两个空字典。
- 同 topic、同 index 的 trigger 重复：去重并保留首个来源。
- 某 topic 只有一个节点：保留节点，返回该 topic 的空区间列表。

## MVP 验收标准

1. 能消费 DataCheck 的无标签 `trigger_points`。
2. 能校验 trigger 与主时间轴的一致性。
3. 能按摄像头 topic 独立排序、编号和输出节点。
4. 每个 topic 的 N 个节点严格生成 N−1 个相邻区间。
5. 不同 topic 的节点不会交叉配对。
6. 节点与区间均保留 `topic_key` 和 `source_topic`。
7. 空输入、单节点和非法输入有明确结果或错误。
8. 单元测试覆盖多 topic 分组、相邻配对、排序去重、空输入和非法输入。

## 更新记录

- 2026-07-13：节点取消“运动开始/恢复稳定”标签；输出按摄像头 topic 分组，每个 topic 内以相邻节点生成 N−1 个区间，禁止跨 topic 配对。
- 2026-07-13：重构模块边界，改为消费 DataCheck 的 `trigger_points`，并新增 trigger 筛选与区间生成职责。
- 2026-07-10：明确事件生成模块负责连接检测结果与下游事件标注。
- 2026-07-07：统一文档格式，补充待设计章节。
