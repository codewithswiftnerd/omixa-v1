import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    # Where uploaded/cleaned files live temporarily, see utils/file_handler.py
    TEMP_DIR = os.path.join(BASE_DIR, "temp")

    # 25 MB, flat for every user, no accounts/plans to vary it by.
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024

    ALLOWED_EXTENSIONS = {"csv", "xlsx", "xls"}

    # How long a job's temp files live before cleanup sweeps them.
    JOB_TTL_SECONDS = 60 * 30  # 30 minutes

    # Comma-separated origins allowed to call the API cross-origin,
    # e.g. "https://omixa.app,https://app.omixa.app". Defaults to "*".
    ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*")

    # If set, every /api/* request (except /api/health) must send a
    # matching X-API-Key header. Unset by default for local dev, set
    # this before deploying anywhere public:
    #   export OMIXA_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    API_KEY = os.environ.get("OMIXA_API_KEY")

    # Requests per IP per rolling 60s window on /api/* routes. Simple
    # in-memory counter, resets on restart and is per-worker-process
    # (so the real limit under multiple gunicorn workers is roughly
    # N times this number). Fine for V1; replace with a shared store
    # if you scale past one instance. Set to 0 to disable.
    RATE_LIMIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "30"))
