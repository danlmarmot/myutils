import subprocess
from pathlib import Path
import sys

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
