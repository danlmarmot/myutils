from pathlib import Path
import hashlib
from datetime import datetime

def get_full_sha(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def get_partial_sha(file_path, bytes_to_read=1024 * 256):
    """Get SHA256 hash of first N bytes of a file
    default N=256KB"""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        data = f.read(bytes_to_read)
        sha256.update(data)
    return sha256.hexdigest()

def get_file_create_datetime(source: Path) -> datetime:
    stat_info = source.stat()

    if hasattr(stat_info, "st_birthtime"):
        birth_time = stat_info.st_birthtime
    else:
        birth_time = stat_info.st_ctime

    dt = datetime.fromtimestamp(birth_time).astimezone()
    return dt