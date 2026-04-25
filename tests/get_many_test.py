from __future__ import annotations

import os

import pytest

import maxminddb_rust


def test_get_many_matches_individual_get_and_preserves_order() -> None:
    db_path = os.path.join(
        os.path.dirname(__file__), "data", "test-data", "GeoIP2-City-Test.mmdb"
    )

    with maxminddb_rust.open_database(db_path) as reader:
        ips = ["81.2.69.142", "2001:2b8::", "1.1.1.1", "81.2.69.142"]

        expected = [reader.get(ip) for ip in ips]

        assert reader.get_many(ips) == expected


def test_get_many_rejects_invalid_ip() -> None:
    db_path = os.path.join(
        os.path.dirname(__file__), "data", "test-data", "GeoIP2-City-Test.mmdb"
    )

    with maxminddb_rust.open_database(db_path) as reader:
        with pytest.raises(
            ValueError,
            match="'not_ip' does not appear to be an IPv4 or IPv6 address",
        ):
            reader.get_many(["81.2.69.142", "not_ip"])


def test_get_many_closed_db() -> None:
    db_path = os.path.join(
        os.path.dirname(__file__), "data", "test-data", "GeoIP2-City-Test.mmdb"
    )

    reader = maxminddb_rust.open_database(db_path)
    reader.close()

    with pytest.raises(ValueError, match="closed"):
        reader.get_many(["81.2.69.142"])


def test_get_many_rejects_ipv6_in_ipv4_database() -> None:
    db_path = os.path.join(
        os.path.dirname(__file__), "data", "test-data", "MaxMind-DB-test-ipv4-24.mmdb"
    )

    with maxminddb_rust.open_database(db_path) as reader:
        with pytest.raises(
            ValueError,
            match="You attempted to look up an IPv6 address in an IPv4-only database",
        ):
            reader.get_many(["1.1.1.1", "2001::"])
