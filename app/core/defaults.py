"""Canonical defaults shared by CLI orchestration and the HTTP demo."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_SYSTEM_PROMPT = """[Role]
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
}"""

DEFAULT_INPUT_PROMPT = """这是一段机器人操作视频的关键帧序列，请描述机器人完成的任务，使用中文描述其任动作意图和环境细节信息。
可选的任务动作有：靠近/拿起/移动/放下/远离（复位）。
- 靠近：夹爪在非夹持状态下，靠近物体
- 拿起：夹爪尝试夹取物体，夹爪状态从非夹持转为夹持状态
- 移动：夹爪保持夹持状态，夹持物体进行移动
- 放下：夹爪松开物体，夹爪状态由夹持转为非夹持
- 远离（复位）：夹爪在非夹持状态下，尝试移动到距离物体更远的位置。特殊：如果夹爪尝试回到启动前位置与姿态，则认为是复位动作
描述动作意图时需要尽可能保留以下信息：动作、交互物件、地点信息。示例：
- 尝试靠近球体
- 尝试从桌面拿起方块
- 尝试将瓶子移动到盒子上方
在描述环境细节信息时，需要注意背景，环境和前景交互目标（末端执行器与交互目标物）的属性信息
描述示例：
- 黑色的夹爪尝试从木制桌面上将黄色方块拿起
- 桌面上由3个红色球体，夹爪靠近中间的球体
- 夹爪尝试将黄色瓶子放入黑色的盒子中，但是失败了"""

PARSER_DEFAULTS: dict[str, Any] = {
    "insert": {"rotation": "ZYX", "max_tor_time_sec": 0.2},
    "output_format": {"include_vector_view": True, "include_component_schema": True},
}

DATA_CHECK_DEFAULTS: dict[str, Any] = {
    "basic": {
        "eps": 1e-9,
        "fps": 30,
        "smooth": {"method": "savgol", "window_frame_length": 10, "polyorder": 3},
    },
    "data_detection": {
        "sudden_change_config": {
            "enable": True,
            "window_time_sec": 0.5,
            "z_score": 3,
            "sudden_time_sec": 0.066666667,
            "step_time_sec": 0.5,
            "zcr_ratio": 0.4,
        },
        "extreme_value_config": {
            "enable": True, "degree": 0.01, "expansion_coef": 0.2, "min_tor": 1e-4,
        },
    },
    "image_detection": {
        "enable": True, "luminance": 10, "window_time_sec": 1.0,
        "lap_var": 150, "z_score": 2, "resize_length": 860, "resize_width": 640,
        "SSIM": 0.7, "pixel_mae": 5, "moving_area_ratio": 0.05,
    },
    "trigger_detection": {
        "mode": "end_effector",
        "params": {
            "algorithm": "Pelt", "model": "clinear", "pen": 15,
            "min_duration_sec": 0.666666667, "jump_frames": 1, "state_count": 3,
            "feature_window_sec": 0.166666667, "stay_probability": 0.995,
            "candidate_sigma_sec": 1.333333333, "candidate_bonus": 1,
        },
    },
    "merge_policy": {"min_low_quality_time_sec": 0.166666667, "max_gap_time_sec": 0.2},
}

EVENT_GENERATION_DEFAULTS: dict[str, Any] = {
    "point_policy": {"mode": "pass_through"},
    "pairing_policy": {"mode": "adjacent_by_topic"},
}

EVENT_LABELING_DEFAULTS: dict[str, Any] = {
    "sampling": {
        "mode": "fixed_sequence",
        "params": {"fixed_frame_len": 20, "context_frame_len": 2},
    },
    "vlm_params": {
        "model": "qwen/qwen3.5-9b",
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "input_prompt": DEFAULT_INPUT_PROMPT,
    },
    "output": {
        "layerId": "l2", "category": "detail",
        "attributes": {"scene": "tabletop", "sceneTags": []},
    },
}


def copy_defaults(value: dict[str, Any]) -> dict[str, Any]:
    """Return mutable request-local defaults."""

    return deepcopy(value)
