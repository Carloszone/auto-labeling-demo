"""Environment-backed settings for the lightweight HTTP demo."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from app.core.defaults import SERVICE_DEFAULTS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = Path(os.getenv("AUTO_LABEL_ENV_FILE", str(PROJECT_ROOT / ".env")))
load_dotenv(ENV_FILE, override=False)


def _project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings intentionally kept small for the single-job MVP."""

    host: str = os.getenv("AUTO_LABEL_HOST", str(SERVICE_DEFAULTS["host"]))
    port: int = int(os.getenv("AUTO_LABEL_PORT", str(SERVICE_DEFAULTS["port"])))
    workspace_root: Path = Path(os.getenv("AUTO_LABEL_WORKSPACE_ROOT", str(SERVICE_DEFAULTS["workspace_root"])))
    max_upload_bytes: int = int(os.getenv("AUTO_LABEL_MAX_UPLOAD_BYTES", str(SERVICE_DEFAULTS["max_upload_bytes"])))
    min_free_bytes: int = int(os.getenv("AUTO_LABEL_MIN_FREE_BYTES", str(SERVICE_DEFAULTS["min_free_bytes"])))
    worker_id: int = int(os.getenv("AUTO_LABEL_WORKER_ID", str(SERVICE_DEFAULTS["worker_id"])))
    vlm_endpoint: str = os.getenv("AUTO_LABEL_VLM_ENDPOINT", "")
    vlm_timeout_sec: float = float(os.getenv("AUTO_LABEL_VLM_TIMEOUT_SEC", str(SERVICE_DEFAULTS["vlm_timeout_sec"])))
    frontend_dist: Path = Path(os.getenv("AUTO_LABEL_FRONTEND_DIST", str(SERVICE_DEFAULTS["frontend_dist"])))
    log_path: Path = _project_path(os.getenv("AUTO_LABEL_LOG_PATH", str(SERVICE_DEFAULTS["log_path"])))
    log_level: str = os.getenv("AUTO_LABEL_LOG_LEVEL", str(SERVICE_DEFAULTS["log_level"]))
    log_max_bytes: int = int(os.getenv("AUTO_LABEL_LOG_MAX_BYTES", str(SERVICE_DEFAULTS["log_max_bytes"])))
    log_backup_count: int = int(os.getenv("AUTO_LABEL_LOG_BACKUP_COUNT", str(SERVICE_DEFAULTS["log_backup_count"])))


settings = Settings()
