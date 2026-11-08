from __future__ import annotations

import subprocess
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"


def test_get_accepts_ip_object_when_first_lookup_is_patched() -> None:
    # The address classes are cached on the first object lookup in the process,
    # so patch ipaddress in a new interpreter.
    code = """
import ipaddress
import sys
from unittest import mock

import maxminddb_rust

reader = maxminddb_rust.open_database(sys.argv[1])
real = ipaddress.ip_address("81.2.69.142")
expected = reader.get("81.2.69.142")
assert expected is not None
with mock.patch("ipaddress.IPv4Address"):
    assert reader.get(real) == expected
assert reader.get(real) == expected
"""
    subprocess.run(  # noqa: S603 - Fixed argument vector; no shell.
        [sys.executable, "-c", code, str(DB_PATH)], check=True
    )
