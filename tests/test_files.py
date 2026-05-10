import plistlib
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from myutils.files import (
    copy_if_different,
    get_files_with_tag,
    get_finder_tags_with_prefix,
    has_finder_tag,
    set_finder_tag,
    add_finder_tag,
    remove_finder_tags_with_prefix,
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
# copy_if_different
# ---------------------------------------------------------------------------


class TestCopyIfDifferent:
    def test_copies_when_dst_does_not_exist(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_bytes(b"hello")
        dst = tmp_path / "dst.txt"
        with patch("shutil.copy2") as mock_copy, \
             patch("subprocess.run", return_value=_make_result(returncode=1)):
            result = copy_if_different(src, dst)
        assert result is True
        mock_copy.assert_called_once_with(src, dst)

    def test_skips_when_contents_identical(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_bytes(b"same content")
        dst.write_bytes(b"same content")
        with patch("shutil.copy2") as mock_copy:
            result = copy_if_different(src, dst)
        assert result is False
        mock_copy.assert_not_called()

    def test_copies_when_contents_differ(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_bytes(b"new content")
        dst.write_bytes(b"old content")
        with patch("shutil.copy2") as mock_copy, \
             patch("subprocess.run", return_value=_make_result(returncode=1)):
            result = copy_if_different(src, dst)
        assert result is True
        mock_copy.assert_called_once_with(src, dst)

    def test_creates_missing_parent_dirs(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_bytes(b"hello")
        dst = tmp_path / "a" / "b" / "dst.txt"
        with patch("shutil.copy2"), \
             patch("subprocess.run", return_value=_make_result(returncode=1)):
            copy_if_different(src, dst)
        assert dst.parent.exists()

    def test_copies_finder_tags_when_present(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_bytes(b"data")
        tag_hex = "62 70 6c 69 73 74"  # arbitrary hex stand-in
        read_result = _make_result(stdout=tag_hex, returncode=0)
        write_result = _make_result(returncode=0)
        with patch("shutil.copy2"), \
             patch("subprocess.run", side_effect=[read_result, write_result]) as mock_run:
            copy_if_different(src, dst)
        read_call, write_call = mock_run.call_args_list
        assert read_call[0][0] == [
            "xattr", "-px", "com.apple.metadata:_kMDItemUserTags", str(src)
        ]
        assert write_call[0][0] == [
            "xattr", "-wx", "com.apple.metadata:_kMDItemUserTags", tag_hex, str(dst)
        ]

    def test_skips_finder_tag_copy_when_src_has_none(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_bytes(b"data")
        no_tag_result = _make_result(returncode=1)
        with patch("shutil.copy2"), \
             patch("subprocess.run", return_value=no_tag_result) as mock_run:
            copy_if_different(src, dst)
        # Only the read xattr call; no write call
        assert mock_run.call_count == 1

    def test_finder_tag_hex_is_stripped_before_write(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_bytes(b"data")
        tag_hex_with_newline = "62 70 6c 69 73 74\n"
        read_result = _make_result(stdout=tag_hex_with_newline, returncode=0)
        write_result = _make_result(returncode=0)
        with patch("shutil.copy2"), \
             patch("subprocess.run", side_effect=[read_result, write_result]) as mock_run:
            copy_if_different(src, dst)
        write_hex_arg = mock_run.call_args_list[1][0][0][3]
        assert not write_hex_arg.endswith("\n")


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
# get_finder_tags_with_prefix
# ---------------------------------------------------------------------------


class TestGetFinderTagsWithPrefix:
    def test_returns_matching_tag_names(self, tmp_file):
        read_result = _make_xattr_result(0, _binary_plist_hex(["import_a\n0", "import_b\n0", "reviewed\n0"]))
        with patch("subprocess.run", return_value=read_result):
            result = get_finder_tags_with_prefix(tmp_file, "import_")
        assert result == ["import_a", "import_b"]

    def test_returns_empty_when_no_tags_on_file(self, tmp_file):
        with patch("subprocess.run", return_value=_make_xattr_result(1)):
            assert get_finder_tags_with_prefix(tmp_file, "import_") == []

    def test_returns_empty_when_no_tags_match_prefix(self, tmp_file):
        read_result = _make_xattr_result(0, _binary_plist_hex(["reviewed\n0", "done\n0"]))
        with patch("subprocess.run", return_value=read_result):
            assert get_finder_tags_with_prefix(tmp_file, "import_") == []

    def test_returns_empty_on_corrupt_plist(self, tmp_file):
        with patch("subprocess.run", return_value=_make_xattr_result(0, "zz zz zz")):
            assert get_finder_tags_with_prefix(tmp_file, "import_") == []

    def test_strips_color_suffix_from_names(self, tmp_file):
        read_result = _make_xattr_result(0, _binary_plist_hex(["import_a\n6"]))
        with patch("subprocess.run", return_value=read_result):
            result = get_finder_tags_with_prefix(tmp_file, "import_")
        assert result == ["import_a"]

    def test_prefix_match_is_exact_not_substring(self, tmp_file):
        read_result = _make_xattr_result(0, _binary_plist_hex(["notimport_x\n0", "import_y\n0"]))
        with patch("subprocess.run", return_value=read_result):
            result = get_finder_tags_with_prefix(tmp_file, "import_")
        assert "notimport_x" not in result
        assert "import_y" in result

    def test_returns_all_matching_tags(self, tmp_file):
        tags = ["import_a\n0", "import_b\n0", "import_c\n0"]
        read_result = _make_xattr_result(0, _binary_plist_hex(tags))
        with patch("subprocess.run", return_value=read_result):
            result = get_finder_tags_with_prefix(tmp_file, "import_")
        assert result == ["import_a", "import_b", "import_c"]

    def test_empty_prefix_returns_all_tag_names(self, tmp_file):
        read_result = _make_xattr_result(0, _binary_plist_hex(["alpha\n0", "beta\n0"]))
        with patch("subprocess.run", return_value=read_result):
            result = get_finder_tags_with_prefix(tmp_file, "")
        assert result == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# remove_finder_tags_with_prefix
# ---------------------------------------------------------------------------


class TestRemoveFinderTagsWithPrefix:
    def test_removes_matching_tags(self, tmp_file):
        read_result = _make_xattr_result(0, _binary_plist_hex(["import_a\n0", "import_b\n0", "reviewed\n0"]))
        write_result = _make_xattr_result(0)
        with patch("subprocess.run", side_effect=[read_result, write_result]) as mock_run:
            assert remove_finder_tags_with_prefix(tmp_file, "import_") is True
        write_hex = mock_run.call_args[0][0][3]
        written_tags = plistlib.loads(bytes.fromhex("".join(write_hex.split())))
        tag_names = [t.split("\n")[0] for t in written_tags]
        assert "import_a" not in tag_names
        assert "import_b" not in tag_names
        assert "reviewed" in tag_names

    def test_returns_true_when_no_tags_on_file(self, tmp_file):
        with patch("subprocess.run", return_value=_make_xattr_result(1)) as mock_run:
            assert remove_finder_tags_with_prefix(tmp_file, "import_") is True
        assert mock_run.call_count == 1  # only the read; no write

    def test_no_matching_tags_still_writes_back(self, tmp_file):
        read_result = _make_xattr_result(0, _binary_plist_hex(["reviewed\n0", "done\n0"]))
        write_result = _make_xattr_result(0)
        with patch("subprocess.run", side_effect=[read_result, write_result]) as mock_run:
            assert remove_finder_tags_with_prefix(tmp_file, "import_") is True
        write_hex = mock_run.call_args[0][0][3]
        written_tags = plistlib.loads(bytes.fromhex("".join(write_hex.split())))
        tag_names = [t.split("\n")[0] for t in written_tags]
        assert tag_names == ["reviewed", "done"]

    def test_removes_all_tags_when_all_match(self, tmp_file):
        read_result = _make_xattr_result(0, _binary_plist_hex(["import_a\n0", "import_b\n0"]))
        write_result = _make_xattr_result(0)
        with patch("subprocess.run", side_effect=[read_result, write_result]) as mock_run:
            assert remove_finder_tags_with_prefix(tmp_file, "import_") is True
        write_hex = mock_run.call_args[0][0][3]
        written_tags = plistlib.loads(bytes.fromhex("".join(write_hex.split())))
        assert written_tags == []

    def test_returns_false_on_write_failure(self, tmp_file):
        read_result = _make_xattr_result(0, _binary_plist_hex(["import_a\n0"]))
        write_result = _make_xattr_result(1, stderr="permission denied")
        with patch("subprocess.run", side_effect=[read_result, write_result]):
            assert remove_finder_tags_with_prefix(tmp_file, "import_") is False

    def test_returns_false_on_corrupt_plist(self, tmp_file):
        read_result = _make_xattr_result(0, "zz zz zz")
        with patch("subprocess.run", return_value=read_result):
            assert remove_finder_tags_with_prefix(tmp_file, "import_") is False

    def test_prefix_match_is_exact_not_substring(self, tmp_file):
        # "pre_" prefix should not remove "notpre_tag"
        read_result = _make_xattr_result(0, _binary_plist_hex(["notpre_tag\n0", "pre_tag\n0"]))
        write_result = _make_xattr_result(0)
        with patch("subprocess.run", side_effect=[read_result, write_result]) as mock_run:
            remove_finder_tags_with_prefix(tmp_file, "pre_")
        write_hex = mock_run.call_args[0][0][3]
        written_tags = plistlib.loads(bytes.fromhex("".join(write_hex.split())))
        tag_names = [t.split("\n")[0] for t in written_tags]
        assert "notpre_tag" in tag_names
        assert "pre_tag" not in tag_names

    def test_uses_correct_xattr_key(self, tmp_file):
        read_result = _make_xattr_result(0, _binary_plist_hex(["import_a\n0"]))
        write_result = _make_xattr_result(0)
        with patch("subprocess.run", side_effect=[read_result, write_result]) as mock_run:
            remove_finder_tags_with_prefix(tmp_file, "import_")
        read_cmd = mock_run.call_args_list[0][0][0]
        write_cmd = mock_run.call_args_list[1][0][0]
        assert read_cmd[2] == "com.apple.metadata:_kMDItemUserTags"
        assert write_cmd[2] == "com.apple.metadata:_kMDItemUserTags"


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
