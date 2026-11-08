from __future__ import annotations

import gc
import sys
from ipaddress import ip_address
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import maxminddb_rust

if TYPE_CHECKING:
    from collections.abc import Iterator


def test_get_many_matches_individual_get_and_preserves_order() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

    with maxminddb_rust.open_database(db_path) as reader:
        ips = ["81.2.69.142", "2001:2b8::", "1.1.1.1", "81.2.69.142"]

        expected = [reader.get(ip) for ip in ips]

        assert reader.get_many(ips) == expected


def test_get_many_accepts_ipaddress_objects() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

    with maxminddb_rust.open_database(db_path) as reader:
        ips = [ip_address("81.2.69.142"), ip_address("2001:2b8::")]

        expected = [reader.get(ip) for ip in ips]

        assert reader.get_many(ips) == expected


def test_get_many_accepts_tuple_input() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

    with maxminddb_rust.open_database(db_path) as reader:
        ips = ("81.2.69.142", "2001:2b8::", "1.1.1.1")

        expected = [reader.get(ip) for ip in ips]

        assert reader.get_many(ips) == expected


def test_get_many_rejects_string_instead_of_iterable_of_ips() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

    with maxminddb_rust.open_database(db_path) as reader, pytest.raises(
        TypeError, match="iterable of strings or ipaddress objects"
    ):
        reader.get_many("81.2.69.142")  # type: ignore[arg-type]


def test_get_many_rejects_bytes_like_containers() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

    with maxminddb_rust.open_database(db_path) as reader:
        for ips in (b"81.2.69.142", bytearray(b"81.2.69.142")):
            with pytest.raises(
                TypeError, match="iterable of strings or ipaddress objects"
            ):
                reader.get_many(ips)  # type: ignore[arg-type]


def test_get_many_rejects_invalid_ip() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

    with maxminddb_rust.open_database(db_path) as reader, pytest.raises(
        ValueError,
        match="'not_ip' does not appear to be an IPv4 or IPv6 address",
    ):
        reader.get_many(["81.2.69.142", "not_ip"])


def test_get_many_closed_db() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

    reader = maxminddb_rust.open_database(db_path)
    reader.close()

    with pytest.raises(ValueError, match="closed"):
        reader.get_many(["81.2.69.142"])


def test_get_many_rejects_ipv6_in_ipv4_database() -> None:
    db_path = (
        Path(__file__).parent / "data" / "test-data" / "MaxMind-DB-test-ipv4-24.mmdb"
    )

    with maxminddb_rust.open_database(db_path) as reader, pytest.raises(
        ValueError,
        match="You attempted to look up an IPv6 address in an IPv4-only database",
    ):
        reader.get_many(["1.1.1.1", "2001::"])


def test_get_many_handles_mutating_input_list() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"
    ips: list[object] = []

    def get_ip(_self: object) -> int:
        ips.clear()
        return int(ip_address("81.2.69.142"))

    shrink_type = type("IPv4Address", (), {"_ip": property(get_ip)})
    ips[:] = [shrink_type(), "1.1.1.1", "81.2.69.142"]

    with maxminddb_rust.open_database(db_path) as reader:
        results = reader.get_many(ips)  # type: ignore[arg-type]
        assert results == [reader.get("81.2.69.142")]


def test_get_many_result_list_is_not_exposed_half_built() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

    def get_ip(_self: object) -> int:
        # Iterating a list with an unfilled NULL slot would crash.
        for obj in gc.get_objects():
            if type(obj) is list:
                for _ in obj:
                    pass
        return int(ip_address("81.2.69.142"))

    gc_walk_type = type("IPv4Address", (), {"_ip": property(get_ip)})
    ips = ["81.2.69.142", gc_walk_type(), "1.1.1.1"]

    with maxminddb_rust.open_database(db_path) as reader:
        expected = [reader.get(ip) for ip in ips]
        assert reader.get_many(ips) == expected  # type: ignore[arg-type]
        expected_paths = [reader.get_path(ip, ("country",)) for ip in ips]
        assert reader.get_many_path(ips, ("country",)) == expected_paths  # type: ignore[arg-type]


class _MisleadingLength:
    """An iterable whose __len__ is far larger than what it yields."""

    def __init__(self, items: list[str]) -> None:
        self.items = items

    def __iter__(self) -> Iterator[str]:
        return iter(self.items)

    def __len__(self) -> int:
        return sys.maxsize


def test_get_many_does_not_trust_len_of_generic_iterable() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

    with maxminddb_rust.open_database(db_path) as reader:
        ips = _MisleadingLength(["81.2.69.142"])
        assert reader.get_many(ips) == [reader.get("81.2.69.142")]  # type: ignore[arg-type]
        assert reader.get_many_path(ips, ("country", "iso_code")) == ["GB"]  # type: ignore[arg-type]
