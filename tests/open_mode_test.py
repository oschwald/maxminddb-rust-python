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


def test_mode_file_reports_unsupported_before_path_extraction() -> None:
    with pytest.raises(ValueError, match="MODE_FILE not yet supported"):
        maxminddb_rust.open_database(0, maxminddb_rust.MODE_FILE)
