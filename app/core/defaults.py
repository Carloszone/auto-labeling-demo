"""Canonical defaults shared by CLI orchestration and the HTTP demo."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_SYSTEM_PROMPT = """You are a robotics manipulation annotation expert.
Analyze the ordered frames from one camera viewpoint from the robot perspective.
All frames are presented in chronological order and each frame is explicitly marked as
context_before, event, or context_after. Only event frames define the event being labeled.
Context frames are boundary evidence only: use them to understand state immediately before
or after the event, but never classify an action that appears only in context frames as the
current event. Do not merge an adjacent action from context into the event description.
If event frames alone do not establish a completed manipulation or outcome, use action_state=0
and describe only the observable state or partial motion.
Return only a JSON object with exactly these fields:
{"action_summary": "concise action", "action_state": 1, "detailed_description": "detailed scene and interaction"}
action_state must be 1 for success, -1 for failure, or 0 when the segment is incomplete or uncertain.
Describe only observable robot, end-effector, object, spatial, and outcome details."""

DEFAULT_INPUT_PROMPT = (
    """
    这是一段机器人操作视频的关键帧序列，请描述机器人完成的任务，使用中文描述其任动作意图和环境细节信息。
    可选的任务动作有：靠近/拿起/移动/放下/远离（复位）。
    - 靠近：夹爪在非夹持状态下，靠近物体
    - 拿起：夹爪尝试夹取物体，夹爪状态从非夹持转为夹持状态
    - 移动：夹爪保持夹持状态，夹持物体进行移动
    - 放下：夹爪松开物体，夹爪状态由夹持转为非夹持
    - 远离（复位）：夹爪在非夹持状态下，尝试移动到距离物体更远的位置。特殊：如果夹爪尝试回到启动前位置与姿态，则认为是复位动作
    描述动作意图时需要尽可能保留以下信息：动作、交互物件、地点信息。示例：
    - 尝试靠近球体
    - 尝试拿起方块
    - 尝试将瓶子移动到盒子上方
    在描述环境细节信息时，需要注意背景，环境和前景交互目标（末端执行器与交互目标物）的属性信息
    描述示例：
    - 黑色的夹爪尝试从木制桌面上将黄色方块拿起
    - 桌面上由3个红色球体，夹爪靠近中间的球体
    - 夹爪尝试将黄色瓶子放入黑色的盒子中，但是失败了
    """
)

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
