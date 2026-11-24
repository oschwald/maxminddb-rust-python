"""Errors raised while decoding keep the Python error that caused them."""

from __future__ import annotations

import io
import struct
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

import pytest

import maxminddb_rust as maxminddb

IP = "1.2.3.4"
BAD_DATA = "bad data"
METADATA_LIMIT_DB = (
    Path(__file__).parent
    / "data"
    / "test-data"
    / "MaxMind-DB-test-metadata-payload-limit.mmdb"
)

# MaxMind DB type numbers.
UTF8_STRING = 2
DOUBLE = 3
BYTES = 4
UINT16 = 5
UINT32 = 6
MAP = 7
INT32 = 8
UINT64 = 9
UINT128 = 10
ARRAY = 11
BOOLEAN = 14
FLOAT = 15
MAX_SHORT_SIZE = 29


def control(type_number: int, size: int) -> bytes:
    """Encode a control byte for a value of up to 284 bytes or entries."""
    extra = b""
    if size >= MAX_SHORT_SIZE:
        extra = bytes([size - MAX_SHORT_SIZE])
        size = MAX_SHORT_SIZE
    if type_number <= MAP:
        return bytes([type_number << 5 | size]) + extra
    return bytes([size, type_number - MAP]) + extra


def uint(type_number: int, value: int) -> bytes:
    data = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return control(type_number, len(data)) + data


def string(value: str | bytes) -> bytes:
    data = value.encode() if isinstance(value, str) else value
    return control(UTF8_STRING, len(data)) + data


def mapping(items: dict[str, bytes]) -> bytes:
    return control(MAP, len(items)) + b"".join(
        string(key) + value for key, value in items.items()
    )


def array(*values: bytes) -> bytes:
    return control(ARRAY, len(values)) + b"".join(values)


TRUE = control(BOOLEAN, 1)


def database(record: bytes) -> io.BytesIO:
    """Build an IPv4 database whose every address maps to `record`."""
    node_count = 1
    # Both records of the one node point to the start of the data section.
    data_pointer = (node_count + 16).to_bytes(3, "big")
    metadata = mapping(
        {
            "binary_format_major_version": uint(UINT16, 2),
            "binary_format_minor_version": uint(UINT16, 0),
            "build_epoch": uint(UINT64, 0),
            "database_type": string("Test"),
            "description": mapping({}),
            "ip_version": uint(UINT16, 4),
            "languages": array(),
            "node_count": uint(UINT32, node_count),
            "record_size": uint(UINT16, 24),
        }
    )
    return io.BytesIO(
        data_pointer * 2 + bytes(16) + record + b"\xab\xcd\xefMaxMind.com" + metadata
    )


def open_reader(record: bytes) -> maxminddb.Reader:
    return maxminddb.open_database(database(record), maxminddb.MODE_FD)


LOOKUPS: dict[str, Callable[[maxminddb.Reader], object]] = {
    "get": lambda reader: reader.get(IP),
    "get_with_prefix_len": lambda reader: reader.get_with_prefix_len(IP),
    "get_path": lambda reader: reader.get_path(IP, ("value",)),
    "get_many": lambda reader: reader.get_many([IP]),
    "get_many_path": lambda reader: reader.get_many_path([IP], ("value",)),
}


@pytest.mark.parametrize("lookup", LOOKUPS.values(), ids=LOOKUPS.keys())
def test_lookup_error_keeps_python_cause(
    lookup: Callable[[maxminddb.Reader], object],
) -> None:
    with (
        open_reader(mapping({"value": string(b"\xff")})) as reader,
        pytest.raises(maxminddb.InvalidDatabaseError, match=BAD_DATA) as excinfo,
    ):
        lookup(reader)

    # The decoder's own message is the cause, and the Python error that
    # stopped decoding is its cause.
    detail = excinfo.value.__cause__
    assert isinstance(detail, maxminddb.InvalidDatabaseError)
    assert "decoding error" in str(detail)
    assert isinstance(detail.__cause__, UnicodeDecodeError)


def test_iteration_error_keeps_python_cause() -> None:
    with (
        open_reader(mapping({"value": string(b"\xff")})) as reader,
        pytest.raises(maxminddb.InvalidDatabaseError, match="UnicodeDecodeError") as e,
    ):
        next(iter(reader))
    assert isinstance(e.value.__cause__, UnicodeDecodeError)


def test_bad_data_error_has_decoder_message_as_cause() -> None:
    # A Python error from an earlier decode must not become the cause of a
    # later failure that Python did not cause.
    with (
        open_reader(mapping({"value": string(b"\xff")})) as reader,
        pytest.raises(maxminddb.InvalidDatabaseError),
    ):
        reader.get(IP)
    # A string that runs past the end of the data section.
    truncated = control(UTF8_STRING, 20) + b"short"
    with (
        open_reader(truncated) as reader,
        pytest.raises(maxminddb.InvalidDatabaseError, match=BAD_DATA) as excinfo,
    ):
        reader.get(IP)
    detail = excinfo.value.__cause__
    assert isinstance(detail, maxminddb.InvalidDatabaseError)
    assert "offset" in str(detail)
    assert detail.__cause__ is None


def test_open_error_has_decoder_message_as_cause() -> None:
    with (
        pytest.raises(
            maxminddb.InvalidDatabaseError, match="valid MaxMind DB file"
        ) as excinfo,
        maxminddb.open_database(METADATA_LIMIT_DB),
    ):
        pass
    detail = excinfo.value.__cause__
    assert isinstance(detail, maxminddb.InvalidDatabaseError)
    assert str(detail)


def test_memory_error_while_decoding_is_not_bad_data() -> None:
    testcapi = pytest.importorskip("_testcapi")
    if not hasattr(testcapi, "set_nomemory"):
        pytest.skip("_testcapi.set_nomemory is not available")

    # Every kind of value, with numbers too large for CPython's small-int
    # cache and strings too long for the string cache. Each allocation
    # failure while building them must raise MemoryError, not
    # InvalidDatabaseError or a panic.
    record = mapping(
        {
            f"key{i}": array(
                TRUE,
                string("x" * 40 + str(i)),
                uint(UINT16, 60000 + i),
                uint(UINT32, 70000 + i),
                uint(UINT64, 2**40 + i),
                uint(UINT128, 2**100 + i),
                control(INT32, 4) + (-70000 - i).to_bytes(4, "big", signed=True),
                control(DOUBLE, 8) + struct.pack(">d", 1.5 + i),
                control(FLOAT, 4) + struct.pack(">f", 2.5 + i),
                mapping({"bytes": control(BYTES, 3) + b"abc", "empty": array()}),
            )
            for i in range(10)
        }
    )
    with open_reader(record) as reader:
        expected = reader.get(IP)
        memory_errors = 0
        for start in range(1000):
            testcapi.set_nomemory(start, start + 1)
            try:
                reader.get(IP)
            except MemoryError:
                memory_errors += 1
            finally:
                testcapi.remove_mem_hooks()
        assert memory_errors > 0
        assert reader.get(IP) == expected
