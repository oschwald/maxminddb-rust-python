from __future__ import annotations

import errno
import io
import mmap
import os
import shutil
import sys
from pathlib import Path

import pytest

import maxminddb_rust


def test_mode_fd_opens_file_object() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

    with db_path.open("rb") as database:
        reader = maxminddb_rust.open_database(database, maxminddb_rust.MODE_FD)

    try:
        assert reader.get("81.2.69.142") is not None
    finally:
        reader.close()


def test_mode_fd_opens_bytes_io() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

    with db_path.open("rb") as database:
        database_buffer = io.BytesIO(database.read())

    reader = maxminddb_rust.open_database(database_buffer, maxminddb_rust.MODE_FD)
    try:
        assert reader.get("81.2.69.142") is not None
    finally:
        reader.close()


@pytest.mark.parametrize("wrap", ["mmap", "memoryview"])
def test_mode_fd_accepts_buffer_contents(wrap: str) -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

    with db_path.open("rb") as database:
        if wrap == "mmap":
            contents: mmap.mmap | memoryview = mmap.mmap(
                database.fileno(), 0, access=mmap.ACCESS_READ
            )
        else:
            contents = memoryview(database.read())

        class BufferFile:
            def read(self) -> mmap.mmap | memoryview:
                return contents

        reader = maxminddb_rust.open_database(BufferFile(), maxminddb_rust.MODE_FD)  # type: ignore[arg-type]
        try:
            assert reader.get("81.2.69.142") is not None
        finally:
            reader.close()
            if isinstance(contents, mmap.mmap):
                contents.close()


def test_mode_fd_requires_readable_object() -> None:
    with pytest.raises(TypeError, match=r"Unsupported database type \(int\)"):
        maxminddb_rust.open_database(0, maxminddb_rust.MODE_FD)


def test_mode_file_requires_pathlike_database() -> None:
    with pytest.raises(TypeError, match=r"Unsupported database type \(int\)"):
        maxminddb_rust.open_database(0, maxminddb_rust.MODE_FILE)


@pytest.mark.parametrize("mode", [maxminddb_rust.MODE_AUTO, maxminddb_rust.MODE_FILE])
def test_bytes_path_opens_database(mode: int) -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

    with maxminddb_rust.open_database(os.fsencode(db_path), mode) as reader:
        assert reader.get("81.2.69.142") is not None


def test_auto_mode_reads_binary_file_object() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

    with db_path.open("rb") as db_file, maxminddb_rust.open_database(db_file) as reader:
        assert reader.get("81.2.69.142") is not None


def test_mode_equal_to_a_mode_is_accepted() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

    with maxminddb_rust.open_database(db_path, 2.0) as reader:  # type: ignore[arg-type]
        assert reader.get("81.2.69.142") is not None


def test_mode_errors_propagate() -> None:
    class BadMode:
        def __index__(self) -> int:
            raise KeyboardInterrupt

    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

    with pytest.raises(KeyboardInterrupt):
        maxminddb_rust.open_database(db_path, BadMode())  # type: ignore[arg-type]


def test_read_errors_propagate() -> None:
    class BadContents:
        def __len__(self) -> int:
            return 1

        def __getitem__(self, index: int) -> int:
            raise KeyboardInterrupt

    class BadFile:
        def read(self) -> BadContents:
            return BadContents()

    with pytest.raises(KeyboardInterrupt):
        maxminddb_rust.open_database(BadFile(), maxminddb_rust.MODE_FD)  # type: ignore[arg-type]


def test_non_integer_mode_raises_value_error() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

    with pytest.raises(ValueError, match=r"Unsupported open mode \(None\)"):
        maxminddb_rust.open_database(db_path, None)  # type: ignore[arg-type]


def test_mode_file_opens_pathlike_database() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

    reader = maxminddb_rust.open_database(db_path, maxminddb_rust.MODE_FILE)
    try:
        assert reader.get("81.2.69.142") is not None
    finally:
        reader.close()


@pytest.mark.skipif(
    os.name == "nt" or sys.platform == "darwin",
    reason="requires a filesystem accepting non-UTF-8 filenames",
)
@pytest.mark.parametrize("mode", [maxminddb_rust.MODE_MMAP, maxminddb_rust.MODE_FILE])
def test_path_with_surrogate_escape(mode: int, tmp_path: Path) -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"
    raw_path = os.fsencode(tmp_path) + b"/city-\xff.mmdb"
    shutil.copyfile(db_path, raw_path)

    reader = maxminddb_rust.open_database(os.fsdecode(raw_path), mode)
    try:
        assert reader.get("81.2.69.142") is not None
    finally:
        reader.close()


def test_pathlike_exception_is_preserved() -> None:
    class BrokenPath:
        def __fspath__(self) -> str:
            msg = "broken path"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="broken path"):
        maxminddb_rust.open_database(BrokenPath())


PATH_MODES = [
    maxminddb_rust.MODE_AUTO,
    maxminddb_rust.MODE_MMAP_EXT,
    maxminddb_rust.MODE_MMAP,
    maxminddb_rust.MODE_FILE,
    maxminddb_rust.MODE_MEMORY,
]


def _open_error(path: str | Path) -> OSError:
    """Return the error Python's own open() raises for path."""
    try:
        with open(path, "rb"):  # noqa: PTH123
            pass
    except OSError as err:
        return err
    msg = f"expected open({path!r}) to fail"
    raise AssertionError(msg)


def _assert_matches_open_error(err: OSError, path: str | Path) -> None:
    expected = _open_error(path)
    assert type(err) is type(expected)
    assert err.errno == expected.errno
    assert err.filename == os.fspath(path)
    # str() shows the filename with repr(), which doubles Windows backslashes.
    assert repr(os.fspath(path)) in str(err)


@pytest.mark.parametrize("mode", PATH_MODES)
@pytest.mark.parametrize("path_type", [str, Path])
def test_missing_file_sets_errno_and_filename(
    mode: int, path_type: type[str | Path], tmp_path: Path
) -> None:
    database = path_type(tmp_path / "missing.mmdb")

    with pytest.raises(FileNotFoundError) as exc_info:
        maxminddb_rust.open_database(database, mode)

    assert exc_info.value.errno == errno.ENOENT
    _assert_matches_open_error(exc_info.value, database)


@pytest.mark.parametrize("mode", PATH_MODES)
def test_directory_raises_same_error_as_open(mode: int, tmp_path: Path) -> None:
    # IsADirectoryError on POSIX, PermissionError on Windows, as with open().
    expected_type = type(_open_error(tmp_path))

    with pytest.raises(expected_type) as exc_info:
        maxminddb_rust.open_database(str(tmp_path), mode)

    _assert_matches_open_error(exc_info.value, str(tmp_path))


@pytest.mark.skipif(
    os.name == "nt" or sys.platform == "darwin",
    reason="requires a filesystem accepting non-UTF-8 filenames",
)
@pytest.mark.parametrize("mode", [maxminddb_rust.MODE_MMAP, maxminddb_rust.MODE_FILE])
def test_missing_file_with_surrogate_escape_keeps_filename(
    mode: int, tmp_path: Path
) -> None:
    missing = os.fsdecode(os.fsencode(tmp_path) + b"/missing-\xff.mmdb")

    with pytest.raises(FileNotFoundError) as exc_info:
        maxminddb_rust.open_database(missing, mode)

    assert exc_info.value.filename == missing
