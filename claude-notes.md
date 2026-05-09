# 2026-0509

## layout

for this to work, and for uv to find the shared utilities, they must 
live in a myutils directory in this repo, yeah it's stuttering but whatever

## Write unit tests

    uv run pytest tests/ -v

7/7 tests pass. Tests are in tests/test_macos_finder.py and cover:

- Tag found in binary plist (with \n color suffix)
- Tag not found in binary plist
- Tag found/not found in XML plist
- xattr returns non-zero → False
- Multiple tags, partial match
- Correct subprocess.run arguments