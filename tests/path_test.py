from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import maxminddb_rust

if TYPE_CHECKING:
    from collections.abc import Iterator


def test_get_path() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"
    with maxminddb_rust.open_database(db_path) as reader:
        ip = "81.2.69.142"

        # Verify full record first
        record = reader.get(ip)
        assert record["country"]["iso_code"] == "GB"
        assert record["city"]["names"]["en"] == "London"

        # Test get_path for various depths
        assert reader.get_path(ip, ("country", "iso_code")) == "GB"
        assert reader.get_path(ip, ("city", "names", "en")) == "London"
        assert (
            reader.get_path(ip, ("location", "latitude"))
            == record["location"]["latitude"]
        )

        # Test non-existent path
        assert reader.get_path(ip, ("non", "existent")) is None

        # Test non-existent IP
        assert reader.get_path("1.1.1.1", ("country", "iso_code")) is None

        # Test array indexing (if subdivisions exist)
        if "subdivisions" in record and len(record["subdivisions"]) > 0:
            assert (
                reader.get_path(ip, ("subdivisions", 0, "iso_code"))
                == record["subdivisions"][0]["iso_code"]
            )


def test_get_path_ipv6() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"
    with maxminddb_rust.open_database(db_path) as reader:
        ip = "2001:2b8::"

        assert reader.get_path(ip, ("country", "iso_code")) == "KR"
        assert reader.get_path(ip, ("continent", "names", "en")) == "Asia"


def test_get_path_invalid_types() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"
    with maxminddb_rust.open_database(db_path) as reader:
        ip = "81.2.69.142"

        # Invalid path element type (float)
        with pytest.raises(
            TypeError, match="Path elements must be strings or integers"
        ):
            reader.get_path(ip, ("country", 3.14))

        # Invalid path argument type (not a sequence)
        with pytest.raises(TypeError, match="Path must be a sequence"):
            reader.get_path(ip, "country")


def test_get_path_rejects_bool_path_element() -> None:
    db_path = (
        Path(__file__).parent / "data" / "test-data" / "MaxMind-DB-test-decoder.mmdb"
    )
    with (
        maxminddb_rust.open_database(db_path) as reader,
        pytest.raises(TypeError, match="Path elements must be strings or integers"),
    ):
        reader.get_path("1.1.1.1", ("array", True))


@pytest.mark.parametrize("path", [("\ud800",), ["\ud800"]])
def test_get_path_rejects_unencodable_str_element(path: object) -> None:
    db_path = (
        Path(__file__).parent / "data" / "test-data" / "MaxMind-DB-test-decoder.mmdb"
    )
    with maxminddb_rust.open_database(db_path) as reader:
        with pytest.raises(
            TypeError, match="Path elements must be strings or integers"
        ):
            reader.get_path("1.1.1.1", path)  # type: ignore[arg-type]
        with pytest.raises(
            TypeError, match="Path elements must be strings or integers"
        ):
            reader.get_many_path(["1.1.1.1"], path)  # type: ignore[arg-type]


@pytest.mark.parametrize("index", [0, -1])
def test_get_path_cache_rejects_index_like_elements(index: int) -> None:
    class IndexLike:
        def __init__(self, value: int) -> None:
            self.value = value
            self.calls = 0

        def __index__(self) -> int:
            self.calls += 1
            return self.value

    db_path = (
        Path(__file__).parent / "data" / "test-data" / "MaxMind-DB-test-decoder.mmdb"
    )
    with maxminddb_rust.open_database(db_path) as reader:
        reader.get_path("1.1.1.1", ("array", index))

        index_like = IndexLike(index)
        with pytest.raises(
            TypeError, match="Path elements must be strings or integers"
        ):
            reader.get_path("1.1.1.1", ("array", index_like))
        assert index_like.calls == 0


def test_get_path_negative_array_indexes() -> None:
    db_path = (
        Path(__file__).parent / "data" / "test-data" / "MaxMind-DB-test-decoder.mmdb"
    )
    with maxminddb_rust.open_database(db_path) as reader:
        expected = [1, 2, 3]
        assert reader.get_path("1.1.1.1", ("array", -1)) == expected[-1]
        assert reader.get_path("1.1.1.1", ("array", -3)) == 1
        assert reader.get_path("1.1.1.1", ("array", -4)) is None


def test_get_path_closed_db() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"
    reader = maxminddb_rust.open_database(db_path)
    reader.close()

    with pytest.raises(ValueError, match="closed"):
        reader.get_path("81.2.69.142", ("country", "iso_code"))


def test_get_path_mixed_invalid() -> None:
    db_path = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"
    with maxminddb_rust.open_database(db_path) as reader:
        ip = "81.2.69.142"

        # Mixed valid and invalid types
        with pytest.raises(
            TypeError, match="Path elements must be strings or integers"
        ):
            reader.get_path(ip, ("country", 3.14, "iso_code"))


def test_get_path_cache_eviction_can_run_python_code() -> None:
    db_path = (
        Path(__file__).parent / "data" / "test-data" / "MaxMind-DB-test-decoder.mmdb"
    )
    outcomes: list[object] = []

    with maxminddb_rust.open_database(db_path) as reader:

        class Key(str):
            __slots__ = ()

            def __del__(self) -> None:
                # Python ignores exceptions raised in __del__, so record them.
                try:
                    outcomes.append(reader.get_path("1.1.1.1", ("utf8_string",)))
                except BaseException as err:  # noqa: BLE001 - PanicException included
                    outcomes.append(err)

        # Fill the cache with other paths. An equal cached path would keep the
        # Key path out of the cache.
        for index in range(100):
            reader.get_path("1.1.1.1", (f"fill{index}",))
        # The cache holds the only reference to the tuple and its Key element.
        reader.get_path("1.1.1.1", (Key("array"),))
        assert outcomes == []
        # Insert more paths than the cache holds, so the Key path is evicted.
        for index in range(100):
            reader.get_path("1.1.1.1", (f"k{index}",))

    assert outcomes == ["unicode! ☯ - ♫"]


@pytest.mark.parametrize("base", [list, tuple])
def test_get_path_honors_overridden_iter_on_sequence_subclass(base: type) -> None:
    class IterOverride(base):  # type: ignore[misc, valid-type]
        def __iter__(self) -> Iterator[str]:
            return iter(("utf8_string",))

    db_path = (
        Path(__file__).parent / "data" / "test-data" / "MaxMind-DB-test-decoder.mmdb"
    )
    with maxminddb_rust.open_database(db_path) as reader:
        expected = reader.get_path("1.1.1.1", ("utf8_string",))
        assert expected is not None
        path = IterOverride(["boolean"])
        # Twice, so a cached result cannot hide the parse.
        assert reader.get_path("1.1.1.1", path) == expected
        assert reader.get_path("1.1.1.1", path) == expected
        assert reader.get_many_path(["1.1.1.1"], path) == [expected]
