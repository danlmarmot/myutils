from datetime import datetime
from functools import lru_cache
import hashlib
import shutil
from pathlib import Path
import subprocess
import sys

import plistlib


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


def get_volume_name(path: Path) -> str:
    """Return the volume name for an absolute path.

    Paths under /Volumes/<name> return <name>. All other paths return 'root'.
    """
    parts = path.parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        return parts[2]
    return "root"


def copy_if_different(src: Path, dst: Path) -> bool:
    """Copy src to dst only if their contents differ.

    Creates parent directories of dst if they don't exist.
    Preserves Finder tags (not copied by shutil.copy2).
    Returns True if the file was copied, False if it was skipped.
    """
    if dst.exists() and get_partial_sha(src) == get_partial_sha(dst):
        return False

    if src.is_dir():
        return False

    dst.parent.mkdir(parents=True, exist_ok=True)

    print(f"Copying {src} to {dst} with Finder tags. ")
    shutil.copy2(src, dst)
    result = subprocess.run(
        ["xattr", "-px", "com.apple.metadata:_kMDItemUserTags", str(src)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        subprocess.run(
            [
                "xattr",
                "-wx",
                "com.apple.metadata:_kMDItemUserTags",
                result.stdout.strip(),
                str(dst),
            ],
            capture_output=True,
            text=True,
        )
    return True


def get_files_with_tag(
    tag: str,
    include_volumes: list[str] | None = None,
    exclude_volumes: list[str] | None = None,
    include_root_volume: bool = True,
) -> list[Path]:
    """Return file paths tagged with the given Finder tag.

    Args:
        tag: The Finder tag to search for.
        include_volumes: If provided, only search these volume names (e.g. ["Macintosh HD"]).
        exclude_volumes: If provided, skip these volume names.
        include_root_volume: Whether to search the root filesystem (/). Defaults to True.

    Returns:
        List of Path objects for matching files.
    """
    volumes_root = Path("/Volumes")
    all_volumes = [v for v in volumes_root.iterdir() if v.is_dir()]

    if include_volumes is not None:
        volumes = [v for v in all_volumes if v.name in include_volumes]
    else:
        volumes = all_volumes

    if exclude_volumes is not None:
        volumes = [v for v in volumes if v.name not in exclude_volumes]

    search_paths = list(volumes)
    if include_root_volume:
        search_paths.insert(0, Path("/"))

    results = []
    for volume in search_paths:
        cmd = ["mdfind", "-onlyin", str(volume), f"kMDItemUserTags == '{tag}'"]
        output = subprocess.run(cmd, capture_output=True, text=True)
        for line in output.stdout.splitlines():
            line = line.strip()
            if line:
                results.append(Path(line))

    return results


def get_file_create_datetime(source: Path) -> datetime:
    stat_info = source.stat()

    if hasattr(stat_info, "st_birthtime"):
        birth_time = stat_info.st_birthtime
    else:
        birth_time = stat_info.st_ctime

    dt = datetime.fromtimestamp(birth_time).astimezone()
    return dt


@lru_cache(maxsize=16)
def is_volume_mounted(path: Path) -> bool:
    """Return True if the volume containing path is currently mounted.

    For paths under /Volumes/<name>/..., checks that /Volumes/<name> exists.
    Paths not under /Volumes (e.g. local /Users/...) are always considered mounted.
    """
    parts = path.parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        return (Path("/Volumes") / parts[2]).exists()
    return True


def has_finder_tag(file_path: Path, tag_name: str) -> bool:
    """Check if a file has a specific Finder tag."""
    result = subprocess.run(
        ["xattr", "-px", "com.apple.metadata:_kMDItemUserTags", str(file_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        # xattr -px returns hex-encoded data with spaces, convert to binary plist
        try:
            import plistlib

            # Remove all whitespace and convert hex to bytes
            hex_data = "".join(result.stdout.split())
            plist_data = bytes.fromhex(hex_data)

            # Parse the plist - specify format for XML plists
            if plist_data.startswith(b"<?xml") or plist_data.startswith(b"<!DOCTYPE"):
                tags = plistlib.loads(plist_data, fmt=plistlib.FMT_XML)
            else:
                tags = plistlib.loads(plist_data)
                # deal with additional stuff on tags
                tags = [item for s in tags for item in s.split("\n")]

            # Tags are in format like "tagname\n0" or just "tagname"
            return any(tag_name in tag for tag in tags)
        except Exception as e:
            print(f"Error parsing Finder tags for {file_path}: {e}", file=sys.stderr)
            print(f"Raw output: {result.stdout}", file=sys.stderr)
            print(f"Possibly mangled tags, here they are: {tags}")
            sys.exit(1)
            return False
    return False


def set_finder_tag(file_path: Path, tag_name: str) -> bool:
    """sets a single finder tag"""
    tag_str = f'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><array><string>{tag_name}\n0</string></array></plist>'
    subprocess.run(
        ["xattr", "-w", "com.apple.metadata:_kMDItemUserTags", tag_str, str(file_path)]
    )
    return True


def add_finder_tag(file_path: Path, tag_name: str) -> bool:
    """Add a Finder tag to a file, preserving any existing tags."""

    # Read existing tags
    result = subprocess.run(
        ["xattr", "-px", "com.apple.metadata:_kMDItemUserTags", str(file_path)],
        capture_output=True,
        text=True,
    )

    existing_tags = []
    if result.returncode == 0:
        try:
            hex_data = "".join(result.stdout.split())
            plist_data = bytes.fromhex(hex_data)
            if plist_data.startswith(b"<?xml") or plist_data.startswith(b"<!DOCTYPE"):
                existing_tags = plistlib.loads(plist_data, fmt=plistlib.FMT_XML)
            else:
                existing_tags = plistlib.loads(plist_data)
        except Exception as e:
            print(f"Error reading existing tags from {file_path}: {e}", file=sys.stderr)
            return False

    # Already present (tags may be stored as "name\n0")
    if any(tag_name == tag.split("\n")[0] for tag in existing_tags):
        return True

    existing_tags.append(f"{tag_name}\n0")

    # Write back as binary plist
    new_plist = plistlib.dumps(existing_tags, fmt=plistlib.FMT_BINARY)
    hex_str = " ".join(f"{b:02x}" for b in new_plist)

    result = subprocess.run(
        [
            "xattr",
            "-wx",
            "com.apple.metadata:_kMDItemUserTags",
            hex_str,
            str(file_path),
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(
            f"Error writing Finder tag on {file_path}: {result.stderr}", file=sys.stderr
        )
        return False

    return True


def get_finder_tags_with_prefix(file_path: Path, prefix: str) -> list[str]:
    """Return the names of all Finder tags on file_path that start with prefix.

    Returns an empty list if the file has no tags or none match.
    Tag names are returned without the color suffix (e.g. "mytag", not "mytag\\n0").
    """
    result = subprocess.run(
        ["xattr", "-px", "com.apple.metadata:_kMDItemUserTags", str(file_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []

    try:
        hex_data = "".join(result.stdout.split())
        plist_data = bytes.fromhex(hex_data)
        if plist_data.startswith(b"<?xml") or plist_data.startswith(b"<!DOCTYPE"):
            tags = plistlib.loads(plist_data, fmt=plistlib.FMT_XML)
        else:
            tags = plistlib.loads(plist_data)
    except Exception as e:
        print(f"Error reading Finder tags from {file_path}: {e}", file=sys.stderr)
        return []

    return [t.split("\n")[0] for t in tags if t.split("\n")[0].startswith(prefix)]


def remove_finder_tags_with_prefix(file_path: Path, prefix: str) -> bool:
    """Remove all Finder tags whose name starts with prefix.

    Returns True if the tag list was written back (even if unchanged), False on error.
    If no tags exist on the file, returns True without writing.
    """
    result = subprocess.run(
        ["xattr", "-px", "com.apple.metadata:_kMDItemUserTags", str(file_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return True  # no tags present — nothing to remove

    try:
        hex_data = "".join(result.stdout.split())
        plist_data = bytes.fromhex(hex_data)
        if plist_data.startswith(b"<?xml") or plist_data.startswith(b"<!DOCTYPE"):
            existing_tags = plistlib.loads(plist_data, fmt=plistlib.FMT_XML)
        else:
            existing_tags = plistlib.loads(plist_data)
    except Exception as e:
        print(f"Error reading Finder tags from {file_path}: {e}", file=sys.stderr)
        return False

    filtered = [t for t in existing_tags if not t.split("\n")[0].startswith(prefix)]

    new_plist = plistlib.dumps(filtered, fmt=plistlib.FMT_BINARY)
    hex_str = " ".join(f"{b:02x}" for b in new_plist)

    write_result = subprocess.run(
        [
            "xattr",
            "-wx",
            "com.apple.metadata:_kMDItemUserTags",
            hex_str,
            str(file_path),
        ],
        capture_output=True,
        text=True,
    )
    if write_result.returncode != 0:
        print(
            f"Error writing Finder tags on {file_path}: {write_result.stderr}",
            file=sys.stderr,
        )
        return False

    return True
