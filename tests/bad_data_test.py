from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

import pytest

import maxminddb_rust as maxminddb

DATA_DIR = Path(__file__).parent / "data"
TEST_DATA_DIR = DATA_DIR / "test-data"
EMPTY_METADATA_PATHS = [
    DATA_DIR
    / "bad-data"
    / "libmaxminddb"
    / f"libmaxminddb-empty-{kind}-last-in-metadata.mmdb"
    for kind in ("array", "map")
]
# This fixture has corrupt, unreachable search-tree nodes. Detecting them needs
# the Rust crate's verify() API, which this Python binding does not expose.
UNREACHABLE_CORRUPTION = (
    DATA_DIR / "bad-data" / "libmaxminddb" / "libmaxminddb-corrupt-search-tree.mmdb"
)
BAD_DATA_PATHS = sorted(
    set((DATA_DIR / "bad-data").rglob("*.mmdb"))
    - {*EMPTY_METADATA_PATHS, UNREACHABLE_CORRUPTION}
)
IP = "1.2.3.4"


@pytest.fixture(
    params=[
        maxminddb.MODE_MMAP,
        maxminddb.MODE_MEMORY,
        maxminddb.MODE_FILE,
        maxminddb.MODE_FD,
    ],
    ids=["mmap", "memory", "file", "fd"],
)
def mode(request: pytest.FixtureRequest) -> int:
    return request.param


@contextmanager
def open_reader(path: Path, mode: int) -> Iterator[maxminddb.Reader]:
    if mode == maxminddb.MODE_FD:
        with path.open("rb") as database:
            reader = maxminddb.open_database(database, mode)
    else:
        reader = maxminddb.open_database(path, mode)
    with reader:
        yield reader


@pytest.mark.parametrize("path", BAD_DATA_PATHS, ids=lambda path: path.name)
def test_bad_data_corpus_raises_invalid_database_error(path: Path, mode: int) -> None:
    with pytest.raises(maxminddb.InvalidDatabaseError):
        read_bad_database(path, mode)


def read_bad_database(path: Path, mode: int) -> None:
    with open_reader(path, mode) as reader:
        for ip in ("1.1.1.1", "128.0.0.1", "163.254.149.39"):
            reader.get(ip)
        # Also exercise corruption outside the individual lookup paths.
        for _network, _record in reader:
            pass


@pytest.mark.parametrize("path", EMPTY_METADATA_PATHS, ids=lambda path: path.name)
def test_empty_containers_at_end_of_metadata_are_valid(path: Path, mode: int) -> None:
    with open_reader(path, mode) as reader:
        assert reader.metadata().description == {}
        assert reader.metadata().languages == []
        assert reader.get("1.1.1.1") == {"ip": "test"}


@pytest.mark.parametrize("limit", ["value", "payload"])
@pytest.mark.parametrize("method", ["get", "get_with_prefix_len", "get_path"])
def test_lookup_resource_limits(limit: str, method: str, mode: int) -> None:
    path = TEST_DATA_DIR / f"MaxMind-DB-test-decoder-{limit}-limit-over.mmdb"
    args = (IP, ()) if method == "get_path" else (IP,)
    with (
        open_reader(path, mode) as reader,
        pytest.raises(maxminddb.InvalidDatabaseError, match="bad data"),
    ):
        getattr(reader, method)(*args)


@pytest.mark.parametrize("limit", ["value", "payload"])
@pytest.mark.parametrize("method", ["get_many", "get_many_path"])
@pytest.mark.parametrize(
    "container", [list, tuple, iter], ids=["list", "tuple", "iterator"]
)
def test_batch_resource_limits(
    limit: str, method: str, container: Callable[[list[str]], Iterable[str]], mode: int
) -> None:
    path = TEST_DATA_DIR / f"MaxMind-DB-test-decoder-{limit}-limit-over.mmdb"
    ips = container([IP])
    args = (ips, ()) if method == "get_many_path" else (ips,)
    with (
        open_reader(path, mode) as reader,
        pytest.raises(maxminddb.InvalidDatabaseError, match="bad data"),
    ):
        getattr(reader, method)(*args)


@pytest.mark.parametrize("limit", ["value", "payload"])
def test_iteration_resource_limits(limit: str, mode: int) -> None:
    path = TEST_DATA_DIR / f"MaxMind-DB-test-decoder-{limit}-limit-over.mmdb"
    with (
        open_reader(path, mode) as reader,
        pytest.raises(maxminddb.InvalidDatabaseError, match="resource limit exceeded"),
    ):
        next(iter(reader))


@pytest.mark.parametrize(
    "limit", ["value-limit", "payload-limit", "value-limit-pointer-heavy"]
)
def test_records_at_resource_limits_remain_readable(limit: str, mode: int) -> None:
    path = TEST_DATA_DIR / f"MaxMind-DB-test-decoder-{limit}.mmdb"
    with open_reader(path, mode) as reader:
        expected = reader.get(IP)
        assert expected is not None
        if limit == "value-limit":
            assert expected == [0] * 65535
        elif limit == "payload-limit":
            assert len(expected) == len([65535] * 32 + [32])
            assert sum(map(len, expected)) == 2 << 20
        # Each call and each record in a batch gets its own decoding budget.
        assert reader.get_path(IP, ()) == expected
        assert reader.get_many([IP, IP]) == [expected, expected]
        assert reader.get_many_path([IP, IP], ()) == [expected, expected]
        assert next(iter(reader))[1] == expected


@pytest.mark.parametrize("method", ["get_path", "get_many_path"])
def test_path_navigation_shares_payload_budget(method: str, mode: int) -> None:
    path = TEST_DATA_DIR / "MaxMind-DB-test-decode-path-shared-budget.mmdb"
    ip_arg = [IP] if method == "get_many_path" else IP
    with (
        open_reader(path, mode) as reader,
        pytest.raises(maxminddb.InvalidDatabaseError, match="bad data"),
    ):
        getattr(reader, method)(ip_arg, ("target",))


def test_metadata_resource_limit(mode: int) -> None:
    path = TEST_DATA_DIR / "MaxMind-DB-test-metadata-payload-limit.mmdb"
    with (
        pytest.raises(maxminddb.InvalidDatabaseError, match="valid MaxMind DB file"),
        open_reader(path, mode),
    ):
        pass
