"""Environment-backed settings for the lightweight HTTP demo."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = Path(os.getenv("AUTO_LABEL_ENV_FILE", str(PROJECT_ROOT / ".env")))
load_dotenv(ENV_FILE, override=False)


def _project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings intentionally kept small for the single-job MVP."""

    host: str = os.getenv("AUTO_LABEL_HOST", "0.0.0.0")
    port: int = int(os.getenv("AUTO_LABEL_PORT", "8000"))
    workspace_root: Path = Path(os.getenv("AUTO_LABEL_WORKSPACE_ROOT", "/tmp/auto-labeling-demo"))
    max_upload_bytes: int = int(os.getenv("AUTO_LABEL_MAX_UPLOAD_BYTES", str(5 * 1024**3)))
    min_free_bytes: int = int(os.getenv("AUTO_LABEL_MIN_FREE_BYTES", str(6 * 1024**3)))
    worker_id: int = int(os.getenv("AUTO_LABEL_WORKER_ID", "1"))
    vlm_endpoint: str = os.getenv("AUTO_LABEL_VLM_ENDPOINT", "")
    vlm_timeout_sec: float = float(os.getenv("AUTO_LABEL_VLM_TIMEOUT_SEC", "120"))
    frontend_dist: Path = Path(os.getenv("AUTO_LABEL_FRONTEND_DIST", "frontend/dist"))
    log_path: Path = _project_path(os.getenv("AUTO_LABEL_LOG_PATH", "logs/auto-labeling-demo.log"))
    log_level: str = os.getenv("AUTO_LABEL_LOG_LEVEL", "INFO")
    log_max_bytes: int = int(os.getenv("AUTO_LABEL_LOG_MAX_BYTES", str(20 * 1024**2)))
    log_backup_count: int = int(os.getenv("AUTO_LABEL_LOG_BACKUP_COUNT", "5"))


settings = Settings()
