import plistlib
import subprocess
from functools import lru_cache
from pathlib import Path
import sys

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
        ['xattr', '-px', 'com.apple.metadata:_kMDItemUserTags', str(file_path)],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        # xattr -px returns hex-encoded data with spaces, convert to binary plist
        try:
            import plistlib
            # Remove all whitespace and convert hex to bytes
            hex_data = ''.join(result.stdout.split())
            plist_data = bytes.fromhex(hex_data)

            # Parse the plist - specify format for XML plists
            if plist_data.startswith(b'<?xml') or plist_data.startswith(b'<!DOCTYPE'):
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
    tag_str = f"<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\"><plist version=\"1.0\"><array><string>{tag_name}\n0</string></array></plist>"
    subprocess.run(['xattr', '-w', 'com.apple.metadata:_kMDItemUserTags', tag_str, str(file_path)])
    return True

def add_finder_tag(file_path: Path, tag_name: str) -> bool:
    """Add a Finder tag to a file, preserving any existing tags."""

    # Read existing tags
    result = subprocess.run(
        ['xattr', '-px', 'com.apple.metadata:_kMDItemUserTags', str(file_path)],
        capture_output=True,
        text=True
    )

    existing_tags = []
    if result.returncode == 0:
        try:
            hex_data = ''.join(result.stdout.split())
            plist_data = bytes.fromhex(hex_data)
            if plist_data.startswith(b'<?xml') or plist_data.startswith(b'<!DOCTYPE'):
                existing_tags = plistlib.loads(plist_data, fmt=plistlib.FMT_XML)
            else:
                existing_tags = plistlib.loads(plist_data)
        except Exception as e:
            print(f"Error reading existing tags from {file_path}: {e}", file=sys.stderr)
            return False

    # Already present (tags may be stored as "name\n0")
    if any(tag_name == tag.split('\n')[0] for tag in existing_tags):
        return True

    existing_tags.append(f"{tag_name}\n0")

    # Write back as binary plist
    new_plist = plistlib.dumps(existing_tags, fmt=plistlib.FMT_BINARY)
    hex_str = ' '.join(f'{b:02x}' for b in new_plist)

    result = subprocess.run(
        ['xattr', '-wx', 'com.apple.metadata:_kMDItemUserTags', hex_str, str(file_path)],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print(f"Error writing Finder tag on {file_path}: {result.stderr}", file=sys.stderr)
        return False

    return True