# 事件生成模块设计文档

## 文档状态

- 文档类型：模块设计文档
- 所属流程：事件生成阶段
- 当前阶段：MVP 设计
- MVP 范围：基于前序模块的输出，实现对事件（`event`）的节点识别

## 功能描述

事件生成模块负责基于机器人 topic 数据和质检结果生成事件节点与事件时间段，为后续事件标注提供输入。
当前阶段只需要实现基于异常区间信息生成事件节点的方法

## 输入

本模块的输入有三个来源：
1. 来自文档解析模块的"timestamp_list"
2. 来自数据检测模块的"data_anomaly_ranges", "img_anomaly_ranges"
3. 来源于外部请求的参数信息。具体接受的参数见`接口定义`部分

## 输出

事件生成模块输出一个字典对象，MVP 阶段核心字段为 `event_points`。

```text
{
    "task_id": "TASK-001",
    "job_id": "JOB-001",
    "event_points": {
        1: {
            "timestamp_ns": 1000000000,
            "timestamp_sec": 1.000000000,
            "timestamp_index": 3,
            "anomaly_code": 1001,
            "anomaly_name": "oscillation",
            "source": "data",
            "is_merged": false
        }
    }
}
```

- `event_points`：事件节点集合。键为事件节点的顺序编号 `order_id`，使用 int 类型，值为事件节点信息。事件节点必须按 `timestamp_ns` 从小到大排序，`order_id` 与排序结果一一对应。
- `task_id` / `job_id`：透传请求中的任务标识，方便服务编排层关联上下游结果。
- 输出文件或存储位置：MVP 阶段不强制定义独立文件格式，由服务编排层将该字典对象作为事件生成阶段结果传递给下游；如需要落盘，可存储为 JSON 文件或任务结果表中的结构化字段。
- 下游模块消费方式：事件标注模块读取 `event_points`，根据相邻事件节点或业务规则生成待标注的事件片段。若 `event_points` 为空，下游应按无可用事件节点处理。

## 架构设计

本模块采用工厂模式加抽象基类的整体架构。

核心抽象：
- `EventGenerationBase`: 事件检索基类。不同的事件生成策略实现不同生成器实例

第一版实现：
- `AnomalyGenerator`: 基于异常区间生成事件节点

## 接口定义
以下示例为伪代码，其中 `check_input`代表数据质检模块的输出对象。

```text
{
    "basic": {
        "task_id": "TASK-001",
        "job_id": "JOB-001",
        "check_info": check_input
    },
    "params": {
        "mode": "anomaly",
        "merge_range": 30
    }
}
```

### 字段说明
#### basic
- `task_id`：业务任务 ID，由服务入口传入或生成。
- `job_id`：运行任务 ID，由服务编排层生成。MVP 阶段可为空。
- `check_info`: 数据质检模块的输出对象

#### params
- `mode`: 事件节点生成方式
- `merge_range`: 进行节点合并时允许的最大帧间隔，单位为帧数。MVP 阶段默认值为 30，表示两个事件节点间隔在 30 帧以内时进行合并。对于 fps=30 的视频，30 帧约等于 1 秒。

## 功能逻辑
1. 请求解析，提取`check_info`中的信息
2. 事件节点生成。提取`check_info`中的"data_anomaly_ranges"和"img_anomaly_ranges"信息，生成事件节点
3. 事件节点生成完毕后，依次对事件节点进行合并
4. 格式化输出

### 事件节点的生成规则
1. 对于数据异常区间"data_anomaly_ranges", 事件节点的判定规则为：
    - 末端执行器全0速异常区间的起止点
    - 末端执行器的动作参数出现台阶跳变的异常区间的起止点
2. 对于图像异常区间"img_anomaly_ranges"，事件节点的判定规则为：
    - 静止帧区间的起止点
3. 事件节点合并规则
    - 如果两个事件节点的帧间隔小于或等于 `merge_range`，则将两个节点合并。合并后保留两者中时间更早的那个节点。

## 数据 schema
模块输出为字典对象，包含 `task_id`、`job_id`、`event_points` 三类字段。
`event_points` 的键值为字典格式，事件节点的顺序编号 `order_id` 是字典的 int 类型键，节点信息为对应的值。MVP 阶段只输出事件节点，不直接生成或输出事件时间段。

示例：
```
event_points = {
    1: {
        "timestamp_ns": 1000000000,
        "timestamp_sec": 1.000000000,
        "timestamp_index": 3,
        "anomaly_code": 1001,
        "anomaly_name": "oscillation",
        "source": "data",
        "is_merged": true
    }
}
```

字段说明
- `timestamp_ns`: 节点时间戳
- `timestamp_sec`: 节点时间戳
- `timestamp_index`: 节点时间戳在`timestamp_list`中的索引
- `anomaly_code`: 节点所属的异常类型代码
- `anomaly_name`: 节点所属的异常类型名称
- `source`: 节点来源，只可以为"data"（来自数据异常区间）或"image"（来自图像异常区间）
- `is_merged`: 是否发生过节点合并，true代表该节点为合并后的节点；false代表该节点没有和其他事件节点发生合并

### 异常类型编码规则

为避免数据异常和图像异常的 `anomaly_code` 发生混用，`anomaly_code` 采用按来源分段的固定枚举。MVP 阶段建议约定：

- `1000-1999`：数据异常，对应 `source = "data"`。
- `2000-2999`：图像异常，对应 `source = "image"`。

事件生成模块需要同时校验 `anomaly_code` 和 `source`：当 `source` 与 `anomaly_code` 所属区间不一致时，应按非法输入报错。`anomaly_name` 作为可读名称，需要与 `anomaly_code` 一一对应，不参与跨来源复用。后续新增异常类型时，只在对应来源的编码区间内追加枚举。

## 异常处理规则

MVP 阶段默认采用 fail-fast 策略，但允许无异常结果正常返回空集合。

### 必须报错的情况

- 请求缺少 `basic`、`params`、`check_info` 或 `task_id`。
- `check_info` 中缺少 `timestamp_list`，或 `timestamp_list` 为空。
- `timestamp_list` 不是按时间递增排列，或存在无法解析的时间戳。
- `data_anomaly_ranges` / `img_anomaly_ranges` 字段缺失，或字段值不是列表。
- 异常区间缺少 `start_timestamp_ns`、`end_timestamp_ns`、`start_timestamp_index`、`end_timestamp_index`、`anomaly_code`、`anomaly_name` 等必要字段。
- 异常区间的起止时间非法，例如 `start_timestamp_ns > end_timestamp_ns`、索引越界、索引与时间戳无法对应。
- `source` 与 `anomaly_code` 所属编码区间不一致，或 `anomaly_code` 与 `anomaly_name` 不匹配。
- `mode` 不在已注册事件生成策略中。MVP 阶段仅要求支持 `anomaly` 模式。
- `merge_range` 不是非负整数。

### 可以降级处理的情况

- `data_anomaly_ranges` 和 `img_anomaly_ranges` 均为空时，返回空的 `event_points`，不视为异常。
- 异常区间的 `anomaly_name` 不属于当前生成规则关注的类型时，跳过该区间，并在日志中记录。
- 多个事件节点落在同一 `timestamp_index` 上时，先去重，再按合并规则处理。
- 两个事件节点的帧间隔小于或等于 `merge_range` 时，合并为一个节点，保留时间更早的节点，并将 `is_merged` 标记为 `true`。

### 日志要求

- 报错日志需要包含 `task_id`、`job_id`、错误字段名和错误原因。
- 跳过异常区间或合并事件节点时，需要记录来源 `source`、异常类型 `anomaly_name`、原始起止时间戳和最终保留的事件节点。

## MVP 验收标准

1. 能够接收数据质检模块输出对象，并从 `timestamp_list`、`data_anomaly_ranges`、`img_anomaly_ranges` 中生成事件节点。
2. 能够基于数据异常区间的起止点生成事件节点，MVP 至少覆盖末端执行器全 0 速异常和动作参数台阶跳变异常。
3. 能够基于图像静止帧异常区间的起止点生成事件节点。
4. 生成的 `event_points` 按 `timestamp_ns` 升序排列，且每个节点包含 `timestamp_ns`、`timestamp_sec`、`timestamp_index`、`anomaly_code`、`anomaly_name`、`source`、`is_merged` 字段。
5. 当多个事件节点帧间隔小于或等于 `merge_range` 时，能够合并节点，保留时间更早的节点，并正确设置 `is_merged`。
6. 当输入中不存在可生成事件节点的异常区间时，能够返回空的 `event_points`。
7. 当输入字段缺失、时间戳非法、异常区间非法或参数非法时，能够抛出明确错误，不生成不可信结果。
8. 输出结果能够被后续事件标注模块直接读取，用于生成待标注事件片段；事件生成模块本身不直接输出事件时间段。
9. 至少包含单元测试或等价验证用例，覆盖正常生成、空异常区间、节点合并、非法输入四类场景。

## 更新记录

- 2026-07-09：补充输出、异常处理规则和 MVP 验收标准，统一事件生成参数命名。
- 2026-07-09：明确 `event_points` 使用 int 类型 `order_id` 作为键，补充 `merge_range` 帧数语义和异常类型编码规则。
- 2026-07-07：统一文档格式，补充待设计章节。
