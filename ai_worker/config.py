import os
import sys


class Config:
    def __init__(self):
        self.ORDP_WORKER_TOKEN = os.environ.get("ORDP_WORKER_TOKEN", "").strip()
        self.AI_BACKEND_URL = os.environ.get("AI_BACKEND_URL", "http://django:8000").rstrip("/")
        self.AI_WORKER_ID = os.environ.get("AI_WORKER_ID", "ai-worker-1")

        self.AI_POLL_INTERVAL_SECONDS = int(os.environ.get("AI_POLL_INTERVAL_SECONDS", "5"))
        self.AI_MAX_JOB_SECONDS = int(os.environ.get("AI_MAX_JOB_SECONDS", "300"))

        # Archive limits
        self.AI_MAX_ARCHIVE_FILES = int(os.environ.get("AI_MAX_ARCHIVE_FILES", "1000"))
        self.AI_MAX_ARCHIVE_UNCOMPRESSED_GB = float(os.environ.get("AI_MAX_ARCHIVE_UNCOMPRESSED_GB", "2"))
        self.AI_MAX_COMPRESSION_RATIO = float(os.environ.get("AI_MAX_COMPRESSION_RATIO", "100"))
        self.AI_MAX_NESTING = int(os.environ.get("AI_MAX_NESTING", "2"))

        # Malware scanning configuration
        self.CLAMAV_ENABLED = os.environ.get("CLAMAV_ENABLED", "false").lower() in ("true", "1", "yes")
        self.CLAMAV_HOST = os.environ.get("CLAMAV_HOST", "")
        self.CLAMAV_PORT = int(os.environ.get("CLAMAV_PORT", "3310"))

        if not self.ORDP_WORKER_TOKEN or self.ORDP_WORKER_TOKEN == "changeme-worker-token":
            sys.exit(
                "ERROR: ORDP_WORKER_TOKEN is not configured or is set to the default placeholder. "
                "Set a secure, non-empty ORDP_WORKER_TOKEN in your environment before running the worker."
            )


config = Config()
