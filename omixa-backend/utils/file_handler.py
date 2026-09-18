"""
Handles everything to do with temporary, per-job storage.

Omixa V1 has no database, so a "job" is just a folder:

    temp/
    └── <job_id>/
        ├── source.csv        (or .xlsx)
        └── cleaned.csv        (written after processing)

job_id is a uuid4 string. Once the user downloads the cleaned file
(or JOB_TTL_SECONDS passes), the whole folder is deleted.
"""

import os
import re
import time
import uuid
from typing import Optional
from werkzeug.utils import secure_filename

from config import Config

# job_id always comes straight from a URL segment, so before it's
# ever joined into a filesystem path it must be validated to look
# like the uuid4 this app actually generates, otherwise a crafted
# id (e.g. containing "..") could be used to read/delete/write
# outside of TEMP_DIR. See job_dir_path().
_JOB_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def is_valid_job_id(job_id) -> bool:
    return isinstance(job_id, str) and bool(_JOB_ID_RE.match(job_id))


def allowed_file(filename: str) -> bool:
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in Config.ALLOWED_EXTENSIONS
    )


def create_job_dir() -> str:
    job_id = str(uuid.uuid4())
    job_dir = os.path.join(Config.TEMP_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    return job_id


def job_dir_path(job_id: str) -> Optional[str]:
    """Returns None for anything that isn't a well-formed job_id
    rather than blindly joining it into a path, since job_id is
    user-supplied (a URL segment)."""
    if not is_valid_job_id(job_id):
        return None
    return os.path.join(Config.TEMP_DIR, job_id)


def save_upload(file_storage, job_id: str) -> str:
    """Saves the incoming file as source.<ext> inside the job dir.

    The original filename (sanitized) is kept in a small `original_name`
    marker file alongside it, the file itself is renamed to
    source.<ext> so the rest of the pipeline never has to deal with
    arbitrary user-supplied names, but the frontend still wants to
    show "customers.csv" rather than "source.csv"."""
    original = secure_filename(file_storage.filename)
    ext = original.rsplit(".", 1)[1].lower()
    job_dir = job_dir_path(job_id)
    dest = os.path.join(job_dir, f"source.{ext}")
    file_storage.save(dest)
    with open(os.path.join(job_dir, "original_name"), "w") as f:
        f.write(original)
    return dest


def original_filename(job_id: str) -> Optional[str]:
    d = job_dir_path(job_id)
    if not d:
        return None
    path = os.path.join(d, "original_name")
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return f.read().strip() or None
    except OSError:
        return None


def find_source_file(job_id: str) -> Optional[str]:
    d = job_dir_path(job_id)
    if not d or not os.path.isdir(d):
        return None
    for name in os.listdir(d):
        if name.startswith("source."):
            return os.path.join(d, name)
    return None


def cleaned_file_path(job_id: str, ext: str) -> str:
    d = job_dir_path(job_id)
    if not d:
        raise ValueError(f"Invalid job_id: {job_id!r}")
    return os.path.join(d, f"cleaned.{ext}")


def delete_job(job_id: str) -> None:
    """Discards all temp data for a job. Called after download,
    and by the periodic sweep for abandoned jobs. Safe to call on
    an invalid/already-gone job_id, this is best-effort cleanup,
    not something that should ever crash a request."""
    d = job_dir_path(job_id)
    if not d or not os.path.isdir(d):
        return
    for name in os.listdir(d):
        os.remove(os.path.join(d, name))
    os.rmdir(d)


def sweep_expired_jobs() -> None:
    """Deletes any job folder older than JOB_TTL_SECONDS. Cheap
    enough to call at the top of every API entry point (V1 has no
    scheduler), see routes/*.py. Never lets a bad individual job
    folder (e.g. deleted out from under it by a concurrent request)
    take down the whole sweep."""
    now = time.time()
    if not os.path.isdir(Config.TEMP_DIR):
        return
    for job_id in os.listdir(Config.TEMP_DIR):
        if not is_valid_job_id(job_id):
            continue  # not one of ours, never touch it
        d = job_dir_path(job_id)
        try:
            if d and os.path.isdir(d) and (now - os.path.getmtime(d)) > Config.JOB_TTL_SECONDS:
                delete_job(job_id)
        except OSError:
            continue  # e.g. another request already deleted it, not fatal
