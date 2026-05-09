import plistlib
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from myutils.macos_finder import (
    has_finder_tag,
    set_finder_tag,
    add_finder_tag,
    is_volume_mounted,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
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


# ---------------------------------------------------------------------------
# has_finder_tag
# ---------------------------------------------------------------------------


class TestHasFinderTag:
    def test_tag_found_binary_plist(self, tmp_file):
        with patch(
            "subprocess.run",
            return_value=_make_result(0, _binary_plist_hex(["imported\n6"])),
        ):
            assert has_finder_tag(tmp_file, "imported") is True

    def test_tag_not_found_binary_plist(self, tmp_file):
        with patch(
            "subprocess.run",
            return_value=_make_result(0, _binary_plist_hex(["reviewed\n6"])),
        ):
            assert has_finder_tag(tmp_file, "imported") is False

    def test_tag_found_xml_plist(self, tmp_file):
        with patch(
            "subprocess.run", return_value=_make_result(0, _xml_plist_hex(["imported"]))
        ):
            assert has_finder_tag(tmp_file, "imported") is True

    def test_tag_not_found_xml_plist(self, tmp_file):
        with patch(
            "subprocess.run", return_value=_make_result(0, _xml_plist_hex(["reviewed"]))
        ):
            assert has_finder_tag(tmp_file, "imported") is False

    def test_xattr_nonzero_returns_false(self, tmp_file):
        with patch("subprocess.run", return_value=_make_result(1)):
            assert has_finder_tag(tmp_file, "imported") is False

    def test_multiple_tags_target_present(self, tmp_file):
        with patch(
            "subprocess.run",
            return_value=_make_result(
                0, _binary_plist_hex(["reviewed\n6", "imported\n6"])
            ),
        ):
            assert has_finder_tag(tmp_file, "imported") is True

    def test_empty_tags_list(self, tmp_file):
        with patch(
            "subprocess.run", return_value=_make_result(0, _binary_plist_hex([]))
        ):
            assert has_finder_tag(tmp_file, "imported") is False

    def test_tag_without_color_suffix(self, tmp_file):
        # Tags stored without the "\nN" color suffix
        with patch(
            "subprocess.run",
            return_value=_make_result(0, _binary_plist_hex(["imported"])),
        ):
            assert has_finder_tag(tmp_file, "imported") is True

    def test_substring_match_returns_true(self, tmp_file):
        # "import" is a substring of "imported" — current implementation uses `in`
        with patch(
            "subprocess.run",
            return_value=_make_result(0, _binary_plist_hex(["imported\n6"])),
        ):
            assert has_finder_tag(tmp_file, "import") is True

    def test_case_sensitive(self, tmp_file):
        with patch(
            "subprocess.run",
            return_value=_make_result(0, _binary_plist_hex(["Imported\n6"])),
        ):
            assert has_finder_tag(tmp_file, "imported") is False

    def test_hex_with_extra_whitespace(self, tmp_file):
        # xattr may emit output with newlines between hex groups; whitespace must be stripped
        raw = _binary_plist_hex(["imported\n6"])
        spaced = raw.replace(" ", "\n  ")
        with patch("subprocess.run", return_value=_make_result(0, spaced)):
            assert has_finder_tag(tmp_file, "imported") is True

    def test_subprocess_called_with_correct_args(self, tmp_file):
        with patch(
            "subprocess.run", return_value=_make_result(0, _binary_plist_hex([]))
        ) as mock_run:
            has_finder_tag(tmp_file, "imported")
            mock_run.assert_called_once_with(
                ["xattr", "-px", "com.apple.metadata:_kMDItemUserTags", str(tmp_file)],
                capture_output=True,
                text=True,
            )


# ---------------------------------------------------------------------------
# set_finder_tag
# ---------------------------------------------------------------------------


class TestSetFinderTag:
    def test_returns_true(self, tmp_file):
        with patch("subprocess.run", return_value=_make_result(0)) as mock_run:
            assert set_finder_tag(tmp_file, "imported") is True

    def test_writes_xml_plist_via_xattr(self, tmp_file):
        with patch("subprocess.run", return_value=_make_result(0)) as mock_run:
            set_finder_tag(tmp_file, "green")
            args = mock_run.call_args[0][0]
            assert args[0] == "xattr"
            assert args[1] == "-w"
            assert args[2] == "com.apple.metadata:_kMDItemUserTags"
            assert "green" in args[3]
            assert str(tmp_file) == args[4]

    def test_tag_name_in_plist_string(self, tmp_file):
        with patch("subprocess.run", return_value=_make_result(0)) as mock_run:
            set_finder_tag(tmp_file, "myTag")
            plist_arg = mock_run.call_args[0][0][3]
            assert "myTag" in plist_arg


# ---------------------------------------------------------------------------
# add_finder_tag
# ---------------------------------------------------------------------------


class TestAddFinderTag:
    def test_adds_tag_to_file_with_no_existing_tags(self, tmp_file):
        read_result = _make_result(1)  # no existing xattr
        write_result = _make_result(0)
        with patch(
            "subprocess.run", side_effect=[read_result, write_result]
        ) as mock_run:
            assert add_finder_tag(tmp_file, "imported") is True
            assert mock_run.call_count == 2

    def test_adds_tag_preserving_existing_tags(self, tmp_file):
        read_result = _make_result(0, _binary_plist_hex(["reviewed\n0"]))
        write_result = _make_result(0)
        with patch(
            "subprocess.run", side_effect=[read_result, write_result]
        ) as mock_run:
            assert add_finder_tag(tmp_file, "imported") is True
            # The write call's hex arg should encode a plist containing both tags
            write_hex = mock_run.call_args[0][0][3]
            plist_bytes = bytes.fromhex("".join(write_hex.split()))
            written_tags = plistlib.loads(plist_bytes)
            tag_names = [t.split("\n")[0] for t in written_tags]
            assert "reviewed" in tag_names
            assert "imported" in tag_names

    def test_skips_duplicate_tag(self, tmp_file):
        read_result = _make_result(0, _binary_plist_hex(["imported\n0"]))
        with patch("subprocess.run", return_value=read_result) as mock_run:
            assert add_finder_tag(tmp_file, "imported") is True
            # Only the read call should have been made
            assert mock_run.call_count == 1

    def test_returns_false_when_write_fails(self, tmp_file):
        read_result = _make_result(1)
        write_result = _make_result(1, stderr="permission denied")
        with patch("subprocess.run", side_effect=[read_result, write_result]):
            assert add_finder_tag(tmp_file, "imported") is False

    def test_returns_false_on_corrupt_existing_plist(self, tmp_file, capsys):
        read_result = _make_result(0, "zz zz zz")  # invalid hex
        with patch("subprocess.run", return_value=read_result):
            assert add_finder_tag(tmp_file, "imported") is False


# ---------------------------------------------------------------------------
# is_volume_mounted
# ---------------------------------------------------------------------------


class TestIsVolumeMounted:
    def setup_method(self):
        # Clear lru_cache between tests
        is_volume_mounted.cache_clear()

    def test_local_path_always_mounted(self, tmp_file):
        assert is_volume_mounted(tmp_file) is True

    def test_volumes_path_mounted_when_exists(self, tmp_path):
        # Simulate /Volumes/MyDisk/file.jpg where /Volumes/MyDisk exists
        vol_root = tmp_path / "Volumes" / "MyDisk"
        vol_root.mkdir(parents=True)
        file_path = vol_root / "photo.jpg"
        with patch("myutils.macos_finder.Path.exists", return_value=True):
            # Construct a path that looks like /Volumes/MyDisk/...
            fake_path = Path("/Volumes/MyDisk/photo.jpg")
            assert is_volume_mounted(fake_path) is True

    def test_volumes_path_unmounted_when_missing(self):
        is_volume_mounted.cache_clear()
        fake_path = Path("/Volumes/UnpluggedDisk/photo.jpg")
        with patch("myutils.macos_finder.Path.exists", return_value=False):
            assert is_volume_mounted(fake_path) is False

    def test_path_not_under_volumes(self, tmp_file):
        assert is_volume_mounted(Path("/Users/someone/photo.jpg")) is True

    def test_short_path_not_under_volumes(self):
        assert is_volume_mounted(Path("/")) is True
