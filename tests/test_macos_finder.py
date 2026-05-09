import plistlib
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from macos_finder import has_finder_tag


def _make_result(returncode: int, stdout: str) -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    return r


def _binary_plist_hex(tags: list[str]) -> str:
    data = plistlib.dumps(tags, fmt=plistlib.FMT_BINARY)
    return " ".join(f"{b:02x}" for b in data)


def _xml_plist_hex(tags: list[str]) -> str:
    data = plistlib.dumps(tags, fmt=plistlib.FMT_XML)
    return " ".join(f"{b:02x}" for b in data)


@pytest.fixture
def tmp_file(tmp_path):
    f = tmp_path / "photo.jpg"
    f.touch()
    return f


class TestHasFinderTag:
    def test_tag_found_binary_plist(self, tmp_file):
        hex_stdout = _binary_plist_hex(["imported\n6"])
        with patch("subprocess.run", return_value=_make_result(0, hex_stdout)):
            assert has_finder_tag(tmp_file, "imported") is True

    def test_tag_not_found_binary_plist(self, tmp_file):
        hex_stdout = _binary_plist_hex(["reviewed\n6"])
        with patch("subprocess.run", return_value=_make_result(0, hex_stdout)):
            assert has_finder_tag(tmp_file, "imported") is False

    def test_tag_found_xml_plist(self, tmp_file):
        hex_stdout = _xml_plist_hex(["imported"])
        with patch("subprocess.run", return_value=_make_result(0, hex_stdout)):
            assert has_finder_tag(tmp_file, "imported") is True

    def test_tag_not_found_xml_plist(self, tmp_file):
        hex_stdout = _xml_plist_hex(["reviewed"])
        with patch("subprocess.run", return_value=_make_result(0, hex_stdout)):
            assert has_finder_tag(tmp_file, "imported") is False

    def test_no_xattr_returns_false(self, tmp_file):
        with patch("subprocess.run", return_value=_make_result(1, "")):
            assert has_finder_tag(tmp_file, "imported") is False

    def test_multiple_tags_partial_match(self, tmp_file):
        hex_stdout = _binary_plist_hex(["reviewed\n6", "imported\n6"])
        with patch("subprocess.run", return_value=_make_result(0, hex_stdout)):
            assert has_finder_tag(tmp_file, "imported") is True

    def test_subprocess_called_with_correct_args(self, tmp_file):
        hex_stdout = _binary_plist_hex([])
        with patch("subprocess.run", return_value=_make_result(0, hex_stdout)) as mock_run:
            has_finder_tag(tmp_file, "imported")
            mock_run.assert_called_once_with(
                ["xattr", "-px", "com.apple.metadata:_kMDItemUserTags", str(tmp_file)],
                capture_output=True,
                text=True,
            )
