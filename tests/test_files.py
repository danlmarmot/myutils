import plistlib
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from myutils.files import (
    get_files_with_tag,
    has_finder_tag,
    set_finder_tag,
    add_finder_tag,
    is_volume_mounted,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(stdout: str = "", returncode: int = 0) -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = ""
    return r


def _fake_volumes(*names: str):
    """Return a list of mock Path objects simulating /Volumes/<name>."""
    vols = []
    for name in names:
        p = MagicMock(spec=Path)
        p.name = name
        p.__str__ = lambda self, n=name: f"/Volumes/{n}"
        p.is_dir.return_value = True
        vols.append(p)
    return vols


# ---------------------------------------------------------------------------
# get_files_with_tag
# ---------------------------------------------------------------------------


class TestGetFilesWithTag:
    def _patch(self, volumes, mdfind_outputs):
        """Context manager factory that patches iterdir and subprocess.run."""
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            side_effects = [_make_result(out) for out in mdfind_outputs]
            with patch(
                "myutils.files.Path.iterdir", return_value=iter(volumes)
            ) as mock_iter, patch(
                "subprocess.run", side_effect=side_effects
            ) as mock_run:
                yield mock_iter, mock_run

        return _ctx()

    # --- basic results ---

    def test_returns_paths_from_root_volume(self):
        with self._patch([], ["/Users/alice/photo.jpg\n/Users/bob/scan.pdf\n"]):
            results = get_files_with_tag("red", include_root_volume=True)
        assert results == [Path("/Users/alice/photo.jpg"), Path("/Users/bob/scan.pdf")]

    def test_returns_paths_from_named_volume(self):
        vols = _fake_volumes("BackupDisk")
        with self._patch(vols, ["", "/Volumes/BackupDisk/archive.zip\n"]):
            results = get_files_with_tag("red", include_root_volume=True)
        assert Path("/Volumes/BackupDisk/archive.zip") in results

    def test_empty_mdfind_output_returns_empty_list(self):
        with self._patch([], [""]):
            results = get_files_with_tag("red", include_root_volume=True)
        assert results == []

    def test_no_volumes_no_root_returns_empty_list(self):
        with self._patch([], []):
            results = get_files_with_tag("red", include_root_volume=False)
        assert results == []

    # --- include_root_volume ---

    def test_root_excluded_does_not_search_slash(self):
        vols = _fake_volunteers = _fake_volumes("Disk1")
        with self._patch(vols, ["/Volumes/Disk1/file.txt\n"]) as (_, mock_run):
            get_files_with_tag("red", include_root_volume=False)
        searched = [call_args[0][0][2] for call_args in mock_run.call_args_list]
        assert "/" not in searched

    def test_root_included_searches_slash_first(self):
        vols = _fake_volumes("Disk1")
        with self._patch(vols, ["", ""]) as (_, mock_run):
            get_files_with_tag("red", include_root_volume=True)
        first_search_path = mock_run.call_args_list[0][0][0][2]
        assert first_search_path == "/"

    def test_root_excluded_by_default_is_included(self):
        # Default is include_root_volume=True
        with self._patch([], ["/file.txt\n"]) as (_, mock_run):
            get_files_with_tag("blue")
        searched = [c[0][0][2] for c in mock_run.call_args_list]
        assert "/" in searched

    # --- include_volumes ---

    def test_include_volumes_restricts_search(self):
        vols = _fake_volumes("Disk1", "Disk2", "Disk3")
        with self._patch(vols, ["", ""]) as (_, mock_run):
            get_files_with_tag("red", include_volumes=["Disk1"], include_root_volume=False)
        searched = [c[0][0][2] for c in mock_run.call_args_list]
        assert all("/Volumes/Disk2" not in s and "/Volumes/Disk3" not in s for s in searched)
        assert any("/Volumes/Disk1" in s for s in searched)

    def test_include_volumes_none_searches_all(self):
        vols = _fake_volumes("Disk1", "Disk2")
        with self._patch(vols, ["", "", ""]) as (_, mock_run):
            get_files_with_tag("red", include_volumes=None, include_root_volume=True)
        assert mock_run.call_count == 3  # root + Disk1 + Disk2

    def test_include_volumes_empty_list_searches_only_root(self):
        vols = _fake_volumes("Disk1", "Disk2")
        with self._patch(vols, [""]) as (_, mock_run):
            get_files_with_tag("red", include_volumes=[], include_root_volume=True)
        assert mock_run.call_count == 1
        assert mock_run.call_args[0][0][2] == "/"

    # --- exclude_volumes ---

    def test_exclude_volumes_skips_named_volume(self):
        vols = _fake_volumes("Disk1", "Disk2")
        with self._patch(vols, ["", ""]) as (_, mock_run):
            get_files_with_tag("red", exclude_volumes=["Disk2"], include_root_volume=True)
        searched = [c[0][0][2] for c in mock_run.call_args_list]
        assert all("/Volumes/Disk2" not in s for s in searched)

    def test_exclude_volumes_does_not_affect_root(self):
        vols = _fake_volumes("Disk1")
        with self._patch(vols, ["", ""]) as (_, mock_run):
            get_files_with_tag("red", exclude_volumes=["Disk1"], include_root_volume=True)
        searched = [c[0][0][2] for c in mock_run.call_args_list]
        assert "/" in searched
        assert all("/Volumes/Disk1" not in s for s in searched)

    def test_exclude_volumes_empty_list_excludes_nothing(self):
        vols = _fake_volumes("Disk1")
        with self._patch(vols, ["", ""]) as (_, mock_run):
            get_files_with_tag("red", exclude_volumes=[], include_root_volume=True)
        assert mock_run.call_count == 2  # root + Disk1

    # --- mdfind command shape ---

    def test_mdfind_called_with_correct_tag(self):
        with self._patch([], [""]) as (_, mock_run):
            get_files_with_tag("green", include_root_volume=True)
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "mdfind"
        assert cmd[1] == "-onlyin"
        assert "green" in cmd[3]

    # --- deduplication / multi-volume aggregation ---

    def test_results_from_multiple_volumes_are_combined(self):
        vols = _fake_volumes("Disk1", "Disk2")
        with self._patch(vols, ["/root_file.txt\n", "/Volumes/Disk1/a.jpg\n", "/Volumes/Disk2/b.jpg\n"]):
            results = get_files_with_tag("red", include_root_volume=True)
        assert Path("/root_file.txt") in results
        assert Path("/Volumes/Disk1/a.jpg") in results
        assert Path("/Volumes/Disk2/b.jpg") in results

    def test_blank_lines_in_mdfind_output_are_ignored(self):
        with self._patch([], ["/file1.txt\n\n/file2.txt\n\n"]):
            results = get_files_with_tag("red", include_root_volume=True)
        assert results == [Path("/file1.txt"), Path("/file2.txt")]


# ---------------------------------------------------------------------------
# Helpers for Finder tag read/write tests
# ---------------------------------------------------------------------------


def _make_xattr_result(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
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
            return_value=_make_xattr_result(0, _binary_plist_hex(["imported\n6"])),
        ):
            assert has_finder_tag(tmp_file, "imported") is True

    def test_tag_not_found_binary_plist(self, tmp_file):
        with patch(
            "subprocess.run",
            return_value=_make_xattr_result(0, _binary_plist_hex(["reviewed\n6"])),
        ):
            assert has_finder_tag(tmp_file, "imported") is False

    def test_tag_found_xml_plist(self, tmp_file):
        with patch(
            "subprocess.run", return_value=_make_xattr_result(0, _xml_plist_hex(["imported"]))
        ):
            assert has_finder_tag(tmp_file, "imported") is True

    def test_tag_not_found_xml_plist(self, tmp_file):
        with patch(
            "subprocess.run", return_value=_make_xattr_result(0, _xml_plist_hex(["reviewed"]))
        ):
            assert has_finder_tag(tmp_file, "imported") is False

    def test_xattr_nonzero_returns_false(self, tmp_file):
        with patch("subprocess.run", return_value=_make_xattr_result(1)):
            assert has_finder_tag(tmp_file, "imported") is False

    def test_multiple_tags_target_present(self, tmp_file):
        with patch(
            "subprocess.run",
            return_value=_make_xattr_result(
                0, _binary_plist_hex(["reviewed\n6", "imported\n6"])
            ),
        ):
            assert has_finder_tag(tmp_file, "imported") is True

    def test_empty_tags_list(self, tmp_file):
        with patch(
            "subprocess.run", return_value=_make_xattr_result(0, _binary_plist_hex([]))
        ):
            assert has_finder_tag(tmp_file, "imported") is False

    def test_tag_without_color_suffix(self, tmp_file):
        with patch(
            "subprocess.run",
            return_value=_make_xattr_result(0, _binary_plist_hex(["imported"])),
        ):
            assert has_finder_tag(tmp_file, "imported") is True

    def test_substring_match_returns_true(self, tmp_file):
        # "import" is a substring of "imported" — current implementation uses `in`
        with patch(
            "subprocess.run",
            return_value=_make_xattr_result(0, _binary_plist_hex(["imported\n6"])),
        ):
            assert has_finder_tag(tmp_file, "import") is True

    def test_case_sensitive(self, tmp_file):
        with patch(
            "subprocess.run",
            return_value=_make_xattr_result(0, _binary_plist_hex(["Imported\n6"])),
        ):
            assert has_finder_tag(tmp_file, "imported") is False

    def test_hex_with_extra_whitespace(self, tmp_file):
        raw = _binary_plist_hex(["imported\n6"])
        spaced = raw.replace(" ", "\n  ")
        with patch("subprocess.run", return_value=_make_xattr_result(0, spaced)):
            assert has_finder_tag(tmp_file, "imported") is True

    def test_subprocess_called_with_correct_args(self, tmp_file):
        with patch(
            "subprocess.run", return_value=_make_xattr_result(0, _binary_plist_hex([]))
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
        with patch("subprocess.run", return_value=_make_xattr_result(0)) as mock_run:
            assert set_finder_tag(tmp_file, "imported") is True

    def test_writes_xml_plist_via_xattr(self, tmp_file):
        with patch("subprocess.run", return_value=_make_xattr_result(0)) as mock_run:
            set_finder_tag(tmp_file, "green")
            args = mock_run.call_args[0][0]
            assert args[0] == "xattr"
            assert args[1] == "-w"
            assert args[2] == "com.apple.metadata:_kMDItemUserTags"
            assert "green" in args[3]
            assert str(tmp_file) == args[4]

    def test_tag_name_in_plist_string(self, tmp_file):
        with patch("subprocess.run", return_value=_make_xattr_result(0)) as mock_run:
            set_finder_tag(tmp_file, "myTag")
            plist_arg = mock_run.call_args[0][0][3]
            assert "myTag" in plist_arg


# ---------------------------------------------------------------------------
# add_finder_tag
# ---------------------------------------------------------------------------


class TestAddFinderTag:
    def test_adds_tag_to_file_with_no_existing_tags(self, tmp_file):
        read_result = _make_xattr_result(1)
        write_result = _make_xattr_result(0)
        with patch(
            "subprocess.run", side_effect=[read_result, write_result]
        ) as mock_run:
            assert add_finder_tag(tmp_file, "imported") is True
            assert mock_run.call_count == 2

    def test_adds_tag_preserving_existing_tags(self, tmp_file):
        read_result = _make_xattr_result(0, _binary_plist_hex(["reviewed\n0"]))
        write_result = _make_xattr_result(0)
        with patch(
            "subprocess.run", side_effect=[read_result, write_result]
        ) as mock_run:
            assert add_finder_tag(tmp_file, "imported") is True
            write_hex = mock_run.call_args[0][0][3]
            plist_bytes = bytes.fromhex("".join(write_hex.split()))
            written_tags = plistlib.loads(plist_bytes)
            tag_names = [t.split("\n")[0] for t in written_tags]
            assert "reviewed" in tag_names
            assert "imported" in tag_names

    def test_skips_duplicate_tag(self, tmp_file):
        read_result = _make_xattr_result(0, _binary_plist_hex(["imported\n0"]))
        with patch("subprocess.run", return_value=read_result) as mock_run:
            assert add_finder_tag(tmp_file, "imported") is True
            assert mock_run.call_count == 1

    def test_returns_false_when_write_fails(self, tmp_file):
        read_result = _make_xattr_result(1)
        write_result = _make_xattr_result(1, stderr="permission denied")
        with patch("subprocess.run", side_effect=[read_result, write_result]):
            assert add_finder_tag(tmp_file, "imported") is False

    def test_returns_false_on_corrupt_existing_plist(self, tmp_file, capsys):
        read_result = _make_xattr_result(0, "zz zz zz")
        with patch("subprocess.run", return_value=read_result):
            assert add_finder_tag(tmp_file, "imported") is False


# ---------------------------------------------------------------------------
# is_volume_mounted
# ---------------------------------------------------------------------------


class TestIsVolumeMounted:
    def setup_method(self):
        is_volume_mounted.cache_clear()

    def test_local_path_always_mounted(self, tmp_file):
        assert is_volume_mounted(tmp_file) is True

    def test_volumes_path_mounted_when_exists(self, tmp_path):
        vol_root = tmp_path / "Volumes" / "MyDisk"
        vol_root.mkdir(parents=True)
        file_path = vol_root / "photo.jpg"
        with patch("myutils.files.Path.exists", return_value=True):
            fake_path = Path("/Volumes/MyDisk/photo.jpg")
            assert is_volume_mounted(fake_path) is True

    def test_volumes_path_unmounted_when_missing(self):
        is_volume_mounted.cache_clear()
        fake_path = Path("/Volumes/UnpluggedDisk/photo.jpg")
        with patch("myutils.files.Path.exists", return_value=False):
            assert is_volume_mounted(fake_path) is False

    def test_path_not_under_volumes(self, tmp_file):
        assert is_volume_mounted(Path("/Users/someone/photo.jpg")) is True

    def test_short_path_not_under_volumes(self):
        assert is_volume_mounted(Path("/")) is True
