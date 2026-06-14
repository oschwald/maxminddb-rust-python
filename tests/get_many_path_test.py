from __future__ import annotations

import os
from ipaddress import ip_address

import pytest

import maxminddb_rust


def city_db_path() -> str:
    return os.path.join(
        os.path.dirname(__file__), "data", "test-data", "GeoIP2-City-Test.mmdb"
    )


def test_get_many_path_matches_individual_get_path_and_preserves_order() -> None:
    with maxminddb_rust.open_database(city_db_path()) as reader:
        ips = ["81.2.69.142", "2001:2b8::", "1.1.1.1", "81.2.69.142"]
        path = ("country", "iso_code")

        expected = [reader.get_path(ip, path) for ip in ips]

        assert reader.get_many_path(ips, path) == expected


def test_get_many_path_accepts_ipaddress_objects() -> None:
    with maxminddb_rust.open_database(city_db_path()) as reader:
        ips = [ip_address("81.2.69.142"), ip_address("2001:2b8::")]
        path = ("country", "iso_code")

        expected = [reader.get_path(ip, path) for ip in ips]

        assert reader.get_many_path(ips, path) == expected


def test_get_many_path_rejects_string_instead_of_iterable_of_ips() -> None:
    with maxminddb_rust.open_database(city_db_path()) as reader:
        with pytest.raises(TypeError, match="iterable of strings or ipaddress objects"):
            reader.get_many_path(  # type: ignore[arg-type]
                "81.2.69.142", ("country", "iso_code")
            )


def test_get_many_path_rejects_bytes_like_containers() -> None:
    with maxminddb_rust.open_database(city_db_path()) as reader:
        for ips in (b"81.2.69.142", bytearray(b"81.2.69.142")):
            with pytest.raises(
                TypeError, match="iterable of strings or ipaddress objects"
            ):
                reader.get_many_path(ips, ("country", "iso_code"))  # type: ignore[arg-type]


def test_get_many_path_rejects_invalid_ip() -> None:
    with maxminddb_rust.open_database(city_db_path()) as reader:
        with pytest.raises(
            ValueError,
            match="'not_ip' does not appear to be an IPv4 or IPv6 address",
        ):
            reader.get_many_path(["81.2.69.142", "not_ip"], ("country", "iso_code"))


def test_get_many_path_rejects_invalid_path() -> None:
    with maxminddb_rust.open_database(city_db_path()) as reader:
        with pytest.raises(
            TypeError, match="Path elements must be strings or integers"
        ):
            reader.get_many_path(["81.2.69.142"], ("country", True))


def test_get_many_path_closed_db() -> None:
    reader = maxminddb_rust.open_database(city_db_path())
    reader.close()

    with pytest.raises(ValueError, match="closed"):
        reader.get_many_path(["81.2.69.142"], ("country", "iso_code"))
