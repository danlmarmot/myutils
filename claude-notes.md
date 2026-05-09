# 2026-0509
## Write unit tests

    uv run pytest tests/ -v

7/7 tests pass. Tests are in tests/test_macos_finder.py and cover:

- Tag found in binary plist (with \n color suffix)
- Tag not found in binary plist
- Tag found/not found in XML plist
- xattr returns non-zero → False
- Multiple tags, partial match
- Correct subprocess.run arguments