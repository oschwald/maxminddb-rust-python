from __future__ import annotations

import io
import os

import pytest

import maxminddb_rust


def test_mode_fd_opens_file_object() -> None:
    db_path = os.path.join(
        os.path.dirname(__file__), "data", "test-data", "GeoIP2-City-Test.mmdb"
    )

    with open(db_path, "rb") as database:
        reader = maxminddb_rust.open_database(database, maxminddb_rust.MODE_FD)

    try:
        assert reader.get("81.2.69.142") is not None
    finally:
        reader.close()


def test_mode_fd_opens_bytes_io() -> None:
    db_path = os.path.join(
        os.path.dirname(__file__), "data", "test-data", "GeoIP2-City-Test.mmdb"
    )

    with open(db_path, "rb") as database:
        database_buffer = io.BytesIO(database.read())

    reader = maxminddb_rust.open_database(database_buffer, maxminddb_rust.MODE_FD)
    try:
        assert reader.get("81.2.69.142") is not None
    finally:
        reader.close()


def test_mode_fd_requires_readable_object() -> None:
    with pytest.raises(AttributeError, match="'int' object has no attribute 'read'"):
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
