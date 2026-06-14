from __future__ import annotations

import os

import pytest

import maxminddb_rust


def test_mode_fd_reports_unsupported_for_file_object() -> None:
    db_path = os.path.join(
        os.path.dirname(__file__), "data", "test-data", "GeoIP2-City-Test.mmdb"
    )

    with open(db_path, "rb") as database:
        with pytest.raises(ValueError, match="MODE_FD not yet supported"):
            maxminddb_rust.open_database(database, maxminddb_rust.MODE_FD)


def test_mode_fd_reports_unsupported_for_integer_descriptor() -> None:
    with pytest.raises(ValueError, match="MODE_FD not yet supported"):
        maxminddb_rust.open_database(0, maxminddb_rust.MODE_FD)


def test_mode_file_requires_pathlike_database() -> None:
    with pytest.raises(
        ValueError, match="database must be a string, PathLike, or file descriptor"
    ):
        maxminddb_rust.open_database(0, maxminddb_rust.MODE_FILE)


def test_mode_file_opens_pathlike_database() -> None:
    db_path = os.path.join(
        os.path.dirname(__file__), "data", "test-data", "GeoIP2-City-Test.mmdb"
    )

    reader = maxminddb_rust.open_database(db_path, maxminddb_rust.MODE_FILE)
    try:
        assert reader.get("81.2.69.142") is not None
    finally:
        reader.close()
