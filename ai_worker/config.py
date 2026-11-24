import os


class Config:
    ORDP_WORKER_TOKEN = os.environ.get("ORDP_WORKER_TOKEN", "changeme-worker-token")
    AI_BACKEND_URL = os.environ.get("AI_BACKEND_URL", "http://django:8000").rstrip("/")
    AI_WORKER_ID = os.environ.get("AI_WORKER_ID", "ai-worker-1")

    AI_POLL_INTERVAL_SECONDS = int(os.environ.get("AI_POLL_INTERVAL_SECONDS", "5"))
    AI_MAX_JOB_SECONDS = int(os.environ.get("AI_MAX_JOB_SECONDS", "300"))

    # Archive limits
    AI_MAX_ARCHIVE_FILES = int(os.environ.get("AI_MAX_ARCHIVE_FILES", "1000"))
    AI_MAX_ARCHIVE_UNCOMPRESSED_GB = float(os.environ.get("AI_MAX_ARCHIVE_UNCOMPRESSED_GB", "2"))
    AI_MAX_COMPRESSION_RATIO = float(os.environ.get("AI_MAX_COMPRESSION_RATIO", "100"))
    AI_MAX_NESTING = int(os.environ.get("AI_MAX_NESTING", "2"))

    # Malware scanning configuration
    CLAMAV_ENABLED = os.environ.get("CLAMAV_ENABLED", "false").lower() in ("true", "1", "yes")
    CLAMAV_HOST = os.environ.get("CLAMAV_HOST", "")
    CLAMAV_PORT = int(os.environ.get("CLAMAV_PORT", "3310"))


config = Config()
