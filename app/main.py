"""Command-line entry point for the local core auto-labeling service."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from app.services.orchestrator import AutoLabelingService


def parse_args() -> argparse.Namespace:
    """Parse runtime paths and optional VLM settings from CLI arguments."""

    parser = argparse.ArgumentParser(description="Run local MCAP auto-labeling MVP")
    parser.add_argument("--mcap-path", required=True, type=Path)
    parser.add_argument("--robot-config-path", required=True, type=Path)
    parser.add_argument("--output-path", type=Path)
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--vlm-endpoint", default=None)
    parser.add_argument("--vlm-model", default="qwen/qwen3.5-9b")
    parser.add_argument("--system-prompt", default="")
    parser.add_argument("--input-prompt", default="")
    parser.add_argument("--task-id", default="local-run")
    parser.add_argument("--job-id", default=None)
    return parser.parse_args()


def main() -> None:
    """Run the serial pipeline and print the final annotation JSON."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = parse_args()
    output_path = args.output_path or args.mcap_path.with_suffix(".annotations.json")
    result = AutoLabelingService().run(
        mcap_path=args.mcap_path,
        robot_config_path=args.robot_config_path,
        output_path=output_path,
        task_id=args.task_id,
        job_id=args.job_id,
        max_frames=args.max_frames,
        vlm_endpoint=args.vlm_endpoint,
        vlm_params={
            "model": args.vlm_model,
            "system_prompt": args.system_prompt,
            "input_prompt": args.input_prompt,
        },
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
