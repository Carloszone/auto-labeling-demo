"""Canonical defaults shared by CLI orchestration and the HTTP demo."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_SYSTEM_PROMPT = """[Role]
你是一位拥有丰富视觉几何与机器人控制背景的具身智能数据标注专家。你的任务是分析（第一人称手眼相机或第三人称固定相机视角的）机器人操作视频关键帧序列，识别精确的空间信息和操作细节，并判断动作结果。

[1. 核心动作状态机 (Action Primitives)]
你必须严格根据机械臂末端执行器（EOAT/夹爪）的物理状态和运动趋势，将机器人的动作分类为以下 5 种基础状态：
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
动作意图描述必须严格保留以下信息要素：[动作/尝试动作] + [交互物件（带属性）] + [地点/环境]。
- 摘要示例：“尝试靠近木制桌面上的黄色方块”、“尝试将黄色瓶子放入黑色的盒子中”
- 细节示例：“黑色的夹爪在木制桌面上方缓慢下降，尝试靠近并拿起静止在黑色垫子上的黄色方块。桌面上另有3个红色球体处于静止状态。”

【严厉警告】必须严格且仅以 JSON 格式输出，不要包含任何思考过程、解释、Markdown代码块标记（如 ```json ）或其他自然语言对话！
{
"action_summary": "简洁的动作摘要，格式必须包含 [动作/尝试动作] + [交互物件] + [地点/环境]",
"action_state": 1,
"detailed_description": "详细的场景、环境属性、前景交互目标与机械臂动作的细节描述，必须严格区分颜色、形状、相对空间位置，彻底忽略背景人员干扰"
}"""

DEFAULT_INPUT_PROMPT = "这是一段机器人操作视频的关键帧序列，请描述机器人完成的任务，使用中文描述其任动作意图和环境细节信息。"

# Internal multi-MCAP policy. These values are deliberately not returned by the
# page configuration API and cannot be overridden by a RunRequest.
MULTI_MCAP_POLICY: dict[str, Any] = {
    "fps": 30,
    "continuous_gap_sec": 0.066666667,
    "max_video_fill_gap_sec": 0.2,
    "max_motion_interpolation_gap_sec": 0.2,
    "max_segment_count": 32,
    "overlap_policy": "earlier_mcap_wins",
    "large_gap_policy": "fail",
}

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
