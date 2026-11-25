# Copyright (c) MaxMind, Inc.
# Licensed under the Apache License, Version 2.0
# Copied from: https://github.com/maxmind/MaxMind-DB-Reader-python
# Original file: tests/reader_test.py
#
# This file has been adapted for maxminddb-rust compatibility testing.
# See tests/maxmind/UPSTREAM_LICENSE for full license text.

from __future__ import annotations

import contextlib
import decimal
import enum
import gc
import gzip
import io
import ipaddress
import multiprocessing
import os
import pathlib
import struct
import sys
import tempfile
import threading
import tracemalloc
import unittest
from typing import TYPE_CHECKING, Any, cast
from unittest import mock

import maxminddb_rust as maxminddb
from maxminddb_rust import (
    MODE_AUTO,
    MODE_FD,
    MODE_FILE,
    MODE_MEMORY,
    MODE_MMAP,
    MODE_MMAP_EXT,
    InvalidDatabaseError,
    open_database,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from typing import IO

    from maxminddb_rust import Reader


# Directory holding the shared MaxMind DB test fixtures.
_TEST_DATA_DIR = "tests/data/test-data"
_DECODER_DB = f"{_TEST_DATA_DIR}/MaxMind-DB-test-decoder.mmdb"
# Valid arguments for the C extension Metadata.
_METADATA_FIELDS: dict[str, Any] = {
    "binary_format_major_version": 2,
    "binary_format_minor_version": 0,
    "build_epoch": 1,
    "database_type": "db",
    "description": {},
    "ip_version": 4,
    "languages": [],
    "node_count": 1,
    "record_size": 24,
}


@contextlib.contextmanager
def _bounded(seconds: int = 60, address_space: int = 2 << 30) -> Iterator[None]:
    """Fail, rather than hang or exhaust memory, if a limit regresses.

    POSIX only. macOS refuses to lower RLIMIT_AS, and a process that already
    uses more address space than the cap, such as one under AddressSanitizer,
    would die on its next allocation; only the alarm applies in those cases.
    """
    if sys.platform == "win32":
        yield
        return
    import resource  # noqa: PLC0415
    import signal  # noqa: PLC0415

    def on_alarm(*_: object) -> None:
        msg = f"hostile decode did not stop within {seconds}s"
        raise TimeoutError(msg)

    def address_space_in_use() -> int:
        # Linux only; elsewhere the size is unknown and the cap applies.
        try:
            with pathlib.Path("/proc/self/statm").open() as statm:
                return int(statm.read().split()[0]) * resource.getpagesize()
        except (OSError, ValueError):
            return 0

    cap_memory = sys.platform != "darwin" and address_space_in_use() < address_space
    if cap_memory:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        limit = address_space
        if soft != resource.RLIM_INFINITY:
            limit = min(limit, soft)
        if hard != resource.RLIM_INFINITY:
            limit = min(limit, hard)
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    old_handler = signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
        if cap_memory:
            resource.setrlimit(resource.RLIMIT_AS, (soft, hard))


# The Rust reader's message for a corrupt search tree, such as one with a cycle.
_CORRUPT_TREE = "search tree appears to have a cycle or invalid structure"
_METADATA_START_MARKER = b"\xab\xcd\xefMaxMind.com"
# libmaxminddb requires these unsigned integer types for metadata values.
_METADATA_UINT_TYPES = {"build_epoch": 9, "node_count": 6}
_UINT16_TYPE = 5


def _database_with_metadata(
    extra: Sequence[tuple[object, object]] = (),
    /,
    **changes: object,
) -> bytes:
    """Return the decoder test database with changed metadata.

    A value of None removes the key. extra adds entries, which can have keys
    that are not strings.
    """
    data = pathlib.Path(_DECODER_DB).read_bytes()
    start = data.rfind(_METADATA_START_MARKER) + len(_METADATA_START_MARKER)
    # maxminddb_rust has no public decoder, so rebuild the metadata map from
    # the reader's Metadata.
    with maxminddb.open_database(_DECODER_DB) as reader:
        metadata = {key: getattr(reader.metadata(), key) for key in _METADATA_FIELDS}
    merged = {**metadata, **changes}
    entries = [*((k, v) for k, v in merged.items() if v is not None), *extra]
    items = b"".join(_encode_value(k) + _encode_value(v, str(k)) for k, v in entries)
    return data[:start] + _encode_control(7, len(entries)) + items


def _database(
    records: tuple[int, ...],
    *,
    ip_version: int,
    data: bytes | None = None,
) -> bytes:
    """Return a database with a 24-bit search tree and one data record.

    The tree has two records per node, and a record of node_count + 16 points
    at the data record. data is the encoded data section, by default the
    string "net".
    """
    if data is None:
        data = _encode_value("net")
    metadata = {
        "binary_format_major_version": 2,
        "binary_format_minor_version": 0,
        "build_epoch": 1,
        "database_type": "Test",
        "description": {"en": "Test"},
        "ip_version": ip_version,
        "languages": ["en"],
        "node_count": len(records) // 2,
        "record_size": 24,
    }
    tree = b"".join(record.to_bytes(3, "big") for record in records)
    return tree + bytes(16) + data + _METADATA_START_MARKER + _encode_value(metadata)


def _encode_value(value: object, key: str = "") -> bytes:
    if isinstance(value, str):
        encoded = value.encode()
        return _encode_control(2, len(encoded)) + encoded
    if isinstance(value, bool):
        return _encode_control(14, int(value))
    if isinstance(value, float):
        return _encode_control(3, 8) + struct.pack(">d", value)
    if isinstance(value, int):
        encoded = value.to_bytes((value.bit_length() + 7) // 8, "big")
        type_num = _METADATA_UINT_TYPES.get(key, _UINT16_TYPE)
        return _encode_control(type_num, len(encoded)) + encoded
    if isinstance(value, list):
        items = b"".join(_encode_value(v) for v in value)
        return _encode_control(11, len(value)) + items
    if isinstance(value, dict):
        items = b"".join(
            _encode_value(k) + _encode_value(v, k) for k, v in value.items()
        )
        return _encode_control(7, len(value)) + items
    msg = f"cannot encode {value!r}"
    raise TypeError(msg)


_MAX_BASIC_TYPE = 7
_ONE_BYTE_SIZE_MARKER = 29


def _encode_control(type_num: int, size: int) -> bytes:
    # Sizes from 29 to 284 use one extra size byte. These tests need no more.
    extended = b""
    if type_num > _MAX_BASIC_TYPE:
        extended = bytes([type_num - _MAX_BASIC_TYPE])
        type_num = 0
    if size < _ONE_BYTE_SIZE_MARKER:
        return bytes([type_num << 5 | size]) + extended
    return (
        bytes([type_num << 5 | _ONE_BYTE_SIZE_MARKER])
        + extended
        + bytes([size - _ONE_BYTE_SIZE_MARKER])
    )


def get_reader_from_file_descriptor(filepath: str, mode: int) -> Reader:
    """Patches open_database() for class TestFDReader()."""
    # There are a few cases where mode is statically defined in
    # BaseTestReader(). In those cases, this opens the string path.
    with _database_source(filepath, mode) as database:
        return maxminddb.open_database(database, mode)


@contextlib.contextmanager
def _database_source(path: str, mode: int) -> Iterator[str | IO[bytes]]:
    """Yield the database argument for path: a binary file for MODE_FD."""
    if mode == MODE_FD:
        with pathlib.Path(path).open("rb") as database:
            yield database
    else:
        yield path


class BaseTestReader(unittest.TestCase):
    mode: int
    reader_class: type[maxminddb.Reader]
    use_ip_objects = False
    # The Rust reader reports exceeded resource limits as bad data.
    payload_error = "bad data"
    value_count_error = "bad data"
    fan_out_error = "bad data"
    metadata_error = "Error opening database file"

    # fork doesn't work on Windows and spawn would involve pickling the reader,
    # which isn't possible.
    if os.name != "nt":
        mp = multiprocessing.get_context("fork")

    def ipf(self, ip: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | str:
        if self.use_ip_objects:
            return ipaddress.ip_address(ip)
        return ip

    def _lookup_resource_record(self, filename: str, ip: str = "0.0.0.1") -> object:
        # Each DoS fixture resolves any address to its single crafted record.
        with open_database(f"{_TEST_DATA_DIR}/{filename}", self.mode) as reader:
            return reader.get(self.ipf(ip))

    def test_payload_amplification_is_rejected(self) -> None:
        # An array of 8,192 pointers to one 65,535-byte value. The value count
        # stays low, but copying each target would materialize about 512 MiB.
        with (
            _bounded(),
            self.assertRaisesRegex(InvalidDatabaseError, self.payload_error),
        ):
            self._lookup_resource_record(
                "MaxMind-DB-test-payload-amplification-dos.mmdb"
            )

    def test_payload_amplification_string_is_rejected(self) -> None:
        # The UTF-8 string variant, so the decode path for strings is exercised.
        with (
            _bounded(),
            self.assertRaisesRegex(InvalidDatabaseError, self.payload_error),
        ):
            self._lookup_resource_record(
                "MaxMind-DB-test-payload-amplification-dos-string.mmdb"
            )

    def test_payload_amplification_worst_case_is_rejected(self) -> None:
        # 65,535 pointers to one 65,535-byte value. The record is exactly
        # 65,536 values under the flat rule, so only the payload budget can
        # reject it.
        with (
            _bounded(),
            self.assertRaisesRegex(InvalidDatabaseError, self.payload_error),
        ):
            self._lookup_resource_record(
                "MaxMind-DB-test-payload-amplification-dos-worst-case.mmdb"
            )

    def test_value_count_boundary(self) -> None:
        # The at-limit fixture decodes to exactly 65,536 values and must decode.
        # The pointer-heavy fixture reaches 65,535 values through pointers,
        # which cost nothing beyond the values they resolve to. One value more
        # than the limit is rejected.
        self.assertIsInstance(
            self._lookup_resource_record("MaxMind-DB-test-decoder-value-limit.mmdb"),
            list,
        )
        self.assertIsInstance(
            self._lookup_resource_record(
                "MaxMind-DB-test-decoder-value-limit-pointer-heavy.mmdb"
            ),
            list,
        )
        with self.assertRaisesRegex(InvalidDatabaseError, self.value_count_error):
            self._lookup_resource_record(
                "MaxMind-DB-test-decoder-value-limit-over.mmdb"
            )

    def test_pointer_fan_out_fixture_is_rejected(self) -> None:
        # A full database whose record nests arrays of pointers to the level
        # below, the classic 2**depth fan-out.
        with (
            _bounded(),
            self.assertRaisesRegex(InvalidDatabaseError, self.fan_out_error),
        ):
            self._lookup_resource_record("MaxMind-DB-test-pointer-decoder-dos.mmdb")

    def test_pointer_fan_out_ipv6_fixture_is_rejected(self) -> None:
        # The same fan-out in a conventional IPv6 database that maps the whole
        # address space to the record, so the IPv6 tree path is covered too.
        with (
            _bounded(),
            self.assertRaisesRegex(InvalidDatabaseError, self.fan_out_error),
        ):
            self._lookup_resource_record(
                "MaxMind-DB-test-pointer-decoder-dos-ipv6.mmdb", "2001:db8::1"
            )

    def test_payload_at_limit_is_accepted(self) -> None:
        # References totaling exactly 2 MiB of payload decode successfully, so
        # the limit does not reject a record at the boundary.
        self.assertIsInstance(
            self._lookup_resource_record("MaxMind-DB-test-decoder-payload-limit.mmdb"),
            list,
        )

    def test_payload_one_over_limit_is_rejected(self) -> None:
        # One byte more than 2 MiB is rejected, catching an off-by-one.
        with self.assertRaisesRegex(InvalidDatabaseError, self.payload_error):
            self._lookup_resource_record(
                "MaxMind-DB-test-decoder-payload-limit-over.mmdb"
            )

    def test_metadata_payload_limit_is_enforced_on_open(self) -> None:
        # Metadata must stay within the payload limit when the database is opened.
        with (
            _bounded(),
            self.assertRaisesRegex(InvalidDatabaseError, self.metadata_error),
            open_database(
                f"{_TEST_DATA_DIR}/MaxMind-DB-test-metadata-payload-limit.mmdb",
                self.mode,
            ),
        ):
            pass

    def test_normal_record_still_decodes(self) -> None:
        # A record with ordinary string and bytes values, which the payload
        # budget also charges, decodes unchanged.
        record = cast(
            "dict",
            self._lookup_resource_record("MaxMind-DB-test-decoder.mmdb", "::1.1.1.0"),
        )
        self.assertEqual(record["utf8_string"], "unicode! ☯ - ♫")
        self.assertEqual(record["bytes"], b"\x00\x00\x00*")

    def test_reader(self) -> None:
        ipv4_version = 4
        for record_size in [24, 28, 32]:
            for ip_version in [4, 6]:
                file_name = (
                    "tests/data/test-data/MaxMind-DB-test-ipv"
                    + str(ip_version)
                    + "-"
                    + str(record_size)
                    + ".mmdb"
                )
                reader = open_database(file_name, self.mode)

                self._check_metadata(reader, ip_version, record_size)

                if ip_version == ipv4_version:
                    self._check_ip_v4(reader, file_name)
                else:
                    self._check_ip_v6(reader, file_name)
                reader.close()

    def test_get_with_prefix_len(self) -> None:
        decoder_record = {
            "array": [1, 2, 3],
            "boolean": True,
            "bytes": b"\x00\x00\x00*",
            "double": 42.123456,
            "float": 1.100000023841858,
            "int32": -268435456,
            "map": {
                "mapX": {
                    "arrayX": [7, 8, 9],
                    "utf8_stringX": "hello",
                },
            },
            "uint128": 1329227995784915872903807060280344576,
            "uint16": 0x64,
            "uint32": 0x10000000,
            "uint64": 0x1000000000000000,
            "utf8_string": "unicode! ☯ - ♫",
        }

        tests = [
            {
                "ip": "1.1.1.1",
                "file_name": "MaxMind-DB-test-ipv6-32.mmdb",
                "expected_prefix_len": 8,
                "expected_record": None,
            },
            {
                "ip": "::1:ffff:ffff",
                "file_name": "MaxMind-DB-test-ipv6-24.mmdb",
                "expected_prefix_len": 128,
                "expected_record": {"ip": "::1:ffff:ffff"},
            },
            {
                "ip": "::2:0:1",
                "file_name": "MaxMind-DB-test-ipv6-24.mmdb",
                "expected_prefix_len": 122,
                "expected_record": {"ip": "::2:0:0"},
            },
            {
                "ip": "1.1.1.1",
                "file_name": "MaxMind-DB-test-ipv4-24.mmdb",
                "expected_prefix_len": 32,
                "expected_record": {"ip": "1.1.1.1"},
            },
            {
                "ip": "1.1.1.3",
                "file_name": "MaxMind-DB-test-ipv4-24.mmdb",
                "expected_prefix_len": 31,
                "expected_record": {"ip": "1.1.1.2"},
            },
            {
                "ip": "1.1.1.3",
                "file_name": "MaxMind-DB-test-decoder.mmdb",
                "expected_prefix_len": 24,
                "expected_record": decoder_record,
            },
            {
                "ip": "::ffff:1.1.1.128",
                "file_name": "MaxMind-DB-test-decoder.mmdb",
                "expected_prefix_len": 120,
                "expected_record": decoder_record,
            },
            {
                "ip": "::1.1.1.128",
                "file_name": "MaxMind-DB-test-decoder.mmdb",
                "expected_prefix_len": 120,
                "expected_record": decoder_record,
            },
            {
                "ip": "200.0.2.1",
                "file_name": "MaxMind-DB-no-ipv4-search-tree.mmdb",
                "expected_prefix_len": 0,
                "expected_record": "::/64",
            },
            {
                "ip": "::200.0.2.1",
                "file_name": "MaxMind-DB-no-ipv4-search-tree.mmdb",
                "expected_prefix_len": 64,
                "expected_record": "::/64",
            },
            {
                "ip": "0:0:0:0:ffff:ffff:ffff:ffff",
                "file_name": "MaxMind-DB-no-ipv4-search-tree.mmdb",
                "expected_prefix_len": 64,
                "expected_record": "::/64",
            },
            {
                "ip": "ef00::",
                "file_name": "MaxMind-DB-no-ipv4-search-tree.mmdb",
                "expected_prefix_len": 1,
                "expected_record": None,
            },
        ]

        for test in tests:
            with open_database(
                "tests/data/test-data/" + cast("str", test["file_name"]),
                self.mode,
            ) as reader:
                (record, prefix_len) = reader.get_with_prefix_len(
                    cast("str", test["ip"]),
                )

                self.assertEqual(
                    prefix_len,
                    test["expected_prefix_len"],
                    f"expected prefix_len of {test['expected_prefix_len']}"
                    f" for {test['ip']}"
                    f" in {test['file_name']} but got {prefix_len}",
                )
                self.assertEqual(
                    record,
                    test["expected_record"],
                    "expected_record for "
                    + cast("str", test["ip"])
                    + " in "
                    + cast("str", test["file_name"]),
                )

    def test_iterator(self) -> None:
        tests = (
            {
                "database": "ipv4",
                "expected": [
                    "1.1.1.1/32",
                    "1.1.1.2/31",
                    "1.1.1.4/30",
                    "1.1.1.8/29",
                    "1.1.1.16/28",
                    "1.1.1.32/32",
                ],
            },
            {
                "database": "ipv6",
                "expected": [
                    "::1:ffff:ffff/128",
                    "::2:0:0/122",
                    "::2:0:40/124",
                    "::2:0:50/125",
                    "::2:0:58/127",
                ],
            },
            {
                "database": "mixed",
                "expected": [
                    "1.1.1.1/32",
                    "1.1.1.2/31",
                    "1.1.1.4/30",
                    "1.1.1.8/29",
                    "1.1.1.16/28",
                    "1.1.1.32/32",
                    "::1:ffff:ffff/128",
                    "::2:0:0/122",
                    "::2:0:40/124",
                    "::2:0:50/125",
                    "::2:0:58/127",
                ],
            },
        )

        for record_size in [24, 28, 32]:
            for test in tests:
                f = (
                    f"tests/data/test-data/MaxMind-DB-test-{test['database']}"
                    f"-{record_size}.mmdb"
                )
                reader = open_database(f, self.mode)
                networks = [str(n) for (n, _) in reader]
                self.assertEqual(networks, test["expected"], f)

    def test_decoder(self) -> None:
        reader = open_database(
            "tests/data/test-data/MaxMind-DB-test-decoder.mmdb",
            self.mode,
        )
        record = cast("dict", reader.get(self.ipf("::1.1.1.0")))

        self.assertEqual(record["array"], [1, 2, 3])
        self.assertEqual(record["boolean"], True)
        self.assertEqual(record["bytes"], bytearray(b"\x00\x00\x00*"))
        self.assertEqual(record["double"], 42.123456)
        self.assertAlmostEqual(record["float"], 1.1)
        self.assertEqual(record["int32"], -268435456)
        self.assertEqual(
            {
                "mapX": {"arrayX": [7, 8, 9], "utf8_stringX": "hello"},
            },
            record["map"],
        )

        self.assertEqual(record["uint16"], 100)
        self.assertEqual(record["uint32"], 268435456)
        self.assertEqual(record["uint64"], 1152921504606846976)
        self.assertEqual(record["utf8_string"], "unicode! ☯ - ♫")

        self.assertEqual(1329227995784915872903807060280344576, record["uint128"])
        reader.close()

    def test_decoder_maximum_values(self) -> None:
        with open_database(_DECODER_DB, self.mode) as reader:
            record = cast("dict", reader.get(self.ipf("::255.255.255.255")))
        # A C long has 32 bits on Windows, where a signed conversion would make
        # the uint32 negative.
        self.assertEqual(record["uint32"], 2**32 - 1)
        self.assertEqual(record["uint64"], 2**64 - 1)
        self.assertEqual(record["uint128"], 2**128 - 1)

    def test_metadata_pointers(self) -> None:
        with open_database(
            "tests/data/test-data/MaxMind-DB-test-metadata-pointers.mmdb",
            self.mode,
        ) as reader:
            self.assertEqual(
                "Lots of pointers in metadata",
                reader.metadata().database_type,
            )

    def test_no_ipv4_search_tree(self) -> None:
        reader = open_database(
            "tests/data/test-data/MaxMind-DB-no-ipv4-search-tree.mmdb",
            self.mode,
        )

        self.assertEqual(reader.get(self.ipf("1.1.1.1")), "::/64")
        self.assertEqual(reader.get(self.ipf("192.1.1.1")), "::/64")
        reader.close()

    def test_ipv6_address_in_ipv4_database(self) -> None:
        reader = open_database(
            "tests/data/test-data/MaxMind-DB-test-ipv4-24.mmdb",
            self.mode,
        )
        with self.assertRaisesRegex(
            ValueError,
            "Error looking up 2001::. "
            "You attempted to look up an IPv6 address "
            "in an IPv4-only database",
        ):
            reader.get(self.ipf("2001::"))
        reader.close()

    def test_opening_path(self) -> None:
        with open_database(
            pathlib.Path("tests/data/test-data/MaxMind-DB-test-decoder.mmdb"),
            self.mode,
        ) as reader:
            self.assertEqual(reader.metadata().database_type, "MaxMind DB Decoder Test")

    @unittest.skip("Not applicable - Rust implementation always has extension")
    def test_no_extension_exception(self) -> None:
        real_extension = maxminddb._extension  # noqa: SLF001
        maxminddb._extension = None  # type: ignore[assignment]  # noqa: SLF001
        with self.assertRaisesRegex(
            ValueError,
            "MODE_MMAP_EXT requires the maxminddb.extension module to be available",
        ):
            open_database(
                "tests/data/test-data/MaxMind-DB-test-decoder.mmdb",
                MODE_MMAP_EXT,
            )
        maxminddb._extension = real_extension  # noqa: SLF001

    def test_broken_database(self) -> None:
        reader = open_database(
            "tests/data/test-data/GeoIP2-City-Test-Broken-Double-Format.mmdb",
            self.mode,
        )
        with self.assertRaisesRegex(
            InvalidDatabaseError,
            r"The MaxMind DB file's data "
            r"section contains bad data \(unknown data "
            r"type or corrupt data\)",
        ):
            reader.get(self.ipf("2001:220::"))
        reader.close()

    def test_search_tree_past_end_of_file(self) -> None:
        # The metadata claims more nodes than the file holds. The Rust reader
        # validates the tree size when opening the database.
        with self.assertRaises(InvalidDatabaseError):
            open_database(
                f"{_TEST_DATA_DIR}/GeoIP2-City-Test-Invalid-Node-Count.mmdb",
                self.mode,
            )

    def test_unknown_metadata_key_is_ignored(self) -> None:
        # A new minor version of the format can add metadata keys.
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "unknown-key.mmdb"
            path.write_bytes(_database_with_metadata(unknown_key="value"))
            with open_database(str(path), self.mode) as reader:
                metadata = reader.metadata()
                self.assertEqual(metadata.database_type, "MaxMind DB Decoder Test")
                self.assertFalse(hasattr(metadata, "unknown_key"))

    def test_metadata_strings_keep_embedded_nuls(self) -> None:
        changes: dict[str, object] = {
            "database_type": "one\0two",
            "description": {"en\0x": "first", "en\0y": "second\0value"},
            "languages": ["en\0x"],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "nul.mmdb"
            path.write_bytes(_database_with_metadata(**changes))
            with open_database(str(path), self.mode) as reader:
                metadata = reader.metadata()
        self.assertEqual(metadata.database_type, changes["database_type"])
        self.assertEqual(metadata.description, changes["description"])
        self.assertEqual(metadata.languages, changes["languages"])

    def test_invalid_metadata_is_rejected(self) -> None:
        cases: dict[str, dict[str, object]] = {
            "missing languages": {"languages": None},
            "missing description": {"description": None},
            "string node_count": {"node_count": "1"},
            "double node_count": {"node_count": 1.5},
            "boolean record_size": {"record_size": True},
            "boolean build_epoch": {"build_epoch": True},
            "string languages": {"languages": "en"},
            "integer in languages": {"languages": [1]},
            "integer in description": {"description": {"en": 1}},
            "integer key in description": {"description": {1: "en"}},
            "integer database_type": {"database_type": 5},
            "ip_version 5": {"ip_version": 5},
            "binary_format_major_version 3": {"binary_format_major_version": 3},
            # The Rust crate accepts a build_epoch of 0, which upstream rejects.
            "binary_format_minor_version too large": {
                "binary_format_minor_version": 2**16,
            },
            "build_epoch too large": {"build_epoch": 2**64},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "invalid-metadata.mmdb"
            for name, changes in cases.items():
                with self.subTest(name):
                    path.write_bytes(_database_with_metadata(**changes))
                    with (
                        self.assertRaises(InvalidDatabaseError),
                        open_database(str(path), self.mode),
                    ):
                        pass

    def test_metadata_that_does_not_decode_is_rejected(self) -> None:
        cases = {
            "list key": _database_with_metadata([([1], "value")]),
            "integer key": _database_with_metadata([(7, "value")]),
            "string that is not UTF-8": _database_with_metadata(
                database_type="NOT-UTF-8",
            ).replace(b"NOT-UTF-8", b"\xff" * 9),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "bad-metadata.mmdb"
            for name, data in cases.items():
                with self.subTest(name):
                    path.write_bytes(data)
                    # The Rust reader rejects all of these when it opens the
                    # database, with its generic open error and no cause.
                    with (
                        self.assertRaises(InvalidDatabaseError) as cm,
                        open_database(str(path), self.mode) as reader,
                    ):
                        reader.metadata()
                    self.assertIn("Error opening database file", str(cm.exception))

    def test_exhausted_iterator_stops_after_close(self) -> None:
        with open_database(
            f"{_TEST_DATA_DIR}/MaxMind-DB-test-ipv4-24.mmdb",
            self.mode,
        ) as reader:
            iterator = iter(reader)
            list(iterator)
        self.assertEqual(next(iterator, "done"), "done")

    def test_search_node_at_full_depth_is_rejected(self) -> None:
        # Nodes 0 to 32 form a left spine, so node 32 is a search node at
        # depth 32, which an IPv4 tree cannot have.
        data = 33 + 16
        records = [record for i in range(32) for record in (i + 1, data)]
        records += [data, data]
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "too-deep.mmdb"
            path.write_bytes(_database(tuple(records), ip_version=4))
            with (
                open_database(str(path), self.mode) as reader,
                self.assertRaisesRegex(InvalidDatabaseError, _CORRUPT_TREE),
            ):
                list(reader)

    @unittest.expectedFailure
    def test_record_that_points_to_the_root_is_rejected(self) -> None:
        # Known difference: the Rust crate detects the cycle only once a walk
        # passes the address length, so it yields networks that the tree does
        # not have, such as 192.0.0.0/3, before it raises.
        self._check_record_that_points_to_the_root_is_rejected()

    def _check_record_that_points_to_the_root_is_rejected(self) -> None:
        # Node 1's right record points back to the root. The left records point
        # at data, so a walk through the root again would yield networks that
        # the tree does not have, such as 192.0.0.0/3.
        records = (18, 1, 18, 0)
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "root-record.mmdb"
            path.write_bytes(_database(records, ip_version=4))
            with open_database(str(path), self.mode) as reader:
                seen: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
                with self.assertRaisesRegex(
                    InvalidDatabaseError,
                    _CORRUPT_TREE,
                ):
                    for network, _ in reader:
                        seen.append(network)
                valid = {
                    ipaddress.ip_network("0.0.0.0/1"),
                    ipaddress.ip_network("128.0.0.0/2"),
                }
                self.assertLessEqual(set(seen), valid)
                # Both readers yield node 0's left record before node 1.
                self.assertIn(ipaddress.ip_network("0.0.0.0/1"), seen)

    def test_iterate_ipv6_networks_shorter_than_96_bits(self) -> None:
        # One node whose two records point at the same data record, so the
        # tree holds ::/1 and 8000::/1.
        records = (17, 17)
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "short-ipv6.mmdb"
            path.write_bytes(_database(records, ip_version=6))
            with open_database(str(path), self.mode) as reader:
                self.assertEqual(
                    list(reader),
                    [
                        (ipaddress.ip_network("::/1"), "net"),
                        (ipaddress.ip_network("8000::/1"), "net"),
                    ],
                )

    def test_iterate_ipv6_network_just_above_ipv4(self) -> None:
        # A left spine of 96 nodes whose last right record is data holds only
        # ::1:0:0/96. The first 96 bits are not all zero, so it is IPv6.
        empty = 96
        data = empty + 16
        records = [record for i in range(95) for record in (i + 1, empty)]
        records += [empty, data]
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "ipv6-above-ipv4.mmdb"
            path.write_bytes(_database(tuple(records), ip_version=6))
            with open_database(str(path), self.mode) as reader:
                self.assertEqual(
                    [network for network, _ in reader],
                    [ipaddress.ip_network("::1:0:0/96")],
                )

    def test_record_that_points_into_the_separator_is_rejected(self) -> None:
        # The left record, node_count + 1, points into the 16-byte separator
        # between the search tree and the data section. libmaxminddb before
        # 1.14 reports it as bad data.
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "separator.mmdb"
            path.write_bytes(_database((2, 17), ip_version=4))
            with (
                open_database(str(path), self.mode) as reader,
                self.assertRaisesRegex(
                    InvalidDatabaseError,
                    "search tree is corrupt|contains bad data",
                ),
            ):
                reader.get(self.ipf("1.1.1.1"))

    def test_cyclic_search_tree_is_rejected(self) -> None:
        data = bytearray(
            pathlib.Path(f"{_TEST_DATA_DIR}/MaxMind-DB-test-ipv4-24.mmdb").read_bytes(),
        )
        # Point the left record of node 1 back at node 1.
        data[6:9] = b"\x00\x00\x01"
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "cyclic.mmdb"
            path.write_bytes(data)
            with open_database(str(path), self.mode) as reader:
                iterator = iter(reader)
                with self.assertRaisesRegex(
                    InvalidDatabaseError,
                    _CORRUPT_TREE,
                ):
                    list(iterator)
                # The iterator stops after an error, as a generator does.
                self.assertEqual(next(iterator, "done"), "done")

        # A record that points back to the root of an IPv4 tree.
        broken = f"{_TEST_DATA_DIR}/MaxMind-DB-test-broken-search-tree-24.mmdb"
        with (
            open_database(broken, self.mode) as reader,
            self.assertRaisesRegex(InvalidDatabaseError, _CORRUPT_TREE),
        ):
            list(reader)

        # A record in the IPv4 subtree of an IPv6 tree that points back to the
        # IPv4 start node. Each record of that node, ::/97 and ::8000:0/97,
        # points to it in turn. The right record makes the address nonzero.
        source = f"{_TEST_DATA_DIR}/MaxMind-DB-test-mixed-24.mmdb"
        original = pathlib.Path(source).read_bytes()
        # maxminddb_rust does not expose the IPv4 start node, so follow 96 left
        # records from the root, as the reader does. Each node has two 24-bit
        # records.
        ipv4_start = 0
        for _ in range(96):
            offset = ipv4_start * 6
            ipv4_start = int.from_bytes(original[offset : offset + 3], "big")
        for index in (0, 1):
            offset = ipv4_start * 6 + index * 3
            mixed = bytearray(original)
            mixed[offset : offset + 3] = ipv4_start.to_bytes(3, "big")
            with (
                self.subTest(index=index),
                tempfile.TemporaryDirectory() as directory,
            ):
                path = pathlib.Path(directory) / "ipv4-cycle.mmdb"
                path.write_bytes(mixed)
                with (
                    open_database(str(path), self.mode) as reader,
                    self.assertRaisesRegex(InvalidDatabaseError, _CORRUPT_TREE),
                ):
                    list(reader)

    def test_value_past_data_section_is_rejected(self) -> None:
        # One node whose records both point to the start of the data section.
        # The value is the whole data section, so its payload overruns into
        # the metadata marker.
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "overrun.mmdb"
            for value in (
                "c4ffff",  # uint32 declaring 4 bytes
                "0303ffff",  # uint128 declaring 3 bytes
                "456162",  # string declaring 5 bytes
                "856162",  # bytes declaring 5 bytes
                "0301ff",  # int32 declaring 3 bytes
                "680000",  # double
                "040800",  # float
                "2800",  # pointer declaring 2 bytes
            ):
                data = bytes.fromhex(value)
                path.write_bytes(_database((17, 17), ip_version=4, data=data))
                with (
                    self.subTest(value=value),
                    open_database(str(path), self.mode) as reader,
                    self.assertRaisesRegex(InvalidDatabaseError, "contains bad data"),
                ):
                    reader.get(self.ipf("1.1.1.1"))

    def test_record_that_points_into_the_metadata_is_rejected(self) -> None:
        data = bytearray(_database((17, 17), ip_version=4))
        # binary_format_minor_version is a uint16 with no payload, so only the
        # record check can reject it.
        key = _encode_value("binary_format_minor_version")
        target = data.index(key) + len(key)
        self.assertEqual(data[target], 0xA0)
        # A record resolves to itself plus the tree size minus node_count.
        data[:6] = (target - 5).to_bytes(3, "big") * 2
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "metadata-record.mmdb"
            path.write_bytes(data)
            with (
                open_database(str(path), self.mode) as reader,
                # The Rust reader's data section bound includes the metadata, so
                # it rejects the record when decoding the value.
                self.assertRaisesRegex(InvalidDatabaseError, "contains bad data"),
            ):
                reader.get(self.ipf("1.1.1.1"))

    def test_search_tree_that_overlaps_the_metadata_is_rejected(self) -> None:
        # The file holds a 6-byte tree, but node_count 3 needs 18 bytes. The
        # tree and separator then end past the metadata marker, but not past
        # the end of the file.
        node_count = _encode_value("node_count")
        data = _database((17, 17), ip_version=4)
        self.assertEqual(data.count(node_count + b"\xc1\x01"), 1)
        data = data.replace(node_count + b"\xc1\x01", node_count + b"\xc1\x03")
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "overlap.mmdb"
            path.write_bytes(data)
            with (
                # The Rust reader reports every rejected tree size as a failed
                # open.
                self.assertRaisesRegex(
                    InvalidDatabaseError,
                    "Error opening database file",
                ),
                open_database(str(path), self.mode),
            ):
                pass

    def test_ip_validation(self) -> None:
        reader = open_database(
            "tests/data/test-data/MaxMind-DB-test-decoder.mmdb",
            self.mode,
        )
        with self.assertRaisesRegex(
            ValueError,
            "'not_ip' does not appear to be an IPv4 or IPv6 address",
        ):
            reader.get("not_ip")
        reader.close()

    def test_missing_database(self) -> None:
        with self.assertRaisesRegex(
            FileNotFoundError,
            "No such file or directory|The system cannot find the file specified",
        ):
            open_database("file-does-not-exist.mmdb", self.mode)

    def test_nondatabase(self) -> None:
        with self.assertRaisesRegex(
            InvalidDatabaseError,
            r"Error opening database file \(README.md\). "
            r"Is this a valid MaxMind DB file\?",
        ):
            open_database("README.md", self.mode)

    # This is from https://github.com/maxmind/MaxMind-DB-Reader-python/issues/58
    def test_database_with_invalid_utf8_key(self) -> None:
        reader = open_database(
            "tests/data/bad-data/maxminddb-python/bad-unicode-in-map-key.mmdb",
            self.mode,
        )
        with self.assertRaises(InvalidDatabaseError):
            reader.get_with_prefix_len("163.254.149.39")

    def test_non_string_map_key_is_rejected(self) -> None:
        data = pathlib.Path(
            f"{_TEST_DATA_DIR}/MaxMind-DB-test-ipv4-24.mmdb"
        ).read_bytes()
        # Each record map refers to the shared "ip" key through a pointer.
        self.assertEqual(data.count(b"\x42ip"), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "non-string-map-key.mmdb"
            # Change the key type from string (0x42) to uint16. Known
            # difference: the Rust reader decodes a bytes (0x82) key as a
            # string, so that case is not checked here.
            for ctrl in (b"\xa2",):
                path.write_bytes(data.replace(b"\x42ip", ctrl + b"ip"))
                with (
                    self.subTest(ctrl=ctrl.hex()),
                    open_database(str(path), self.mode) as reader,
                    # The Rust reader reports the key as bad data.
                    self.assertRaisesRegex(InvalidDatabaseError, "contains bad data"),
                ):
                    reader.get(self.ipf("1.1.1.1"))

    def test_extended_type_byte_0(self) -> None:
        # The pure Python reader rejects extended type byte 0. The bundled
        # libmaxminddb does not check this byte, so the C extension decodes it
        # as an empty map. If the bundled libmaxminddb starts to reject it,
        # update HISTORY.rst too. A system libmaxminddb can already reject it.
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "extended-type-byte-0.mmdb"
            path.write_bytes(_database((17, 17), ip_version=4, data=b"\x00\x00"))
            with open_database(str(path), self.mode) as reader:
                self.assertEqual(reader.get(self.ipf("1.1.1.1")), {})

    def test_too_many_constructor_args(self) -> None:
        with self.assertRaises(TypeError):
            self.reader_class("README.md", self.mode, 1)  # type: ignore[arg-type,call-arg]

    def test_bad_constructor_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, r"Unsupported open mode \(100\)"):
            self.reader_class("README.md", mode=100)  # type:  ignore[arg-type]

    def test_no_constructor_args(self) -> None:
        with self.assertRaisesRegex(
            TypeError,
            r" 1 required positional argument|"
            r"\(pos 1\) not found|"
            r"takes at least 2 arguments|"
            r"function missing required argument \'database\' \(pos 1\)",
        ):
            self.reader_class()  # type:  ignore[call-arg]

    def test_too_many_get_args(self) -> None:
        reader = open_database(
            "tests/data/test-data/MaxMind-DB-test-decoder.mmdb",
            self.mode,
        )
        with self.assertRaises(TypeError):
            reader.get(self.ipf("1.1.1.1"), "blah")  # type:  ignore[call-arg]
        reader.close()

    def test_no_get_args(self) -> None:
        reader = open_database(
            "tests/data/test-data/MaxMind-DB-test-decoder.mmdb",
            self.mode,
        )
        with self.assertRaises(TypeError):
            reader.get()  # type:  ignore[call-arg]
        reader.close()

    def test_incorrect_get_arg_type(self) -> None:
        reader = open_database("tests/data/test-data/GeoIP2-City-Test.mmdb", self.mode)
        with self.assertRaisesRegex(
            TypeError,
            "argument 1 must be a string or ipaddress object",
        ):
            reader.get(1)  # type:  ignore[arg-type]
        reader.close()

    def test_metadata_args(self) -> None:
        reader = open_database(
            "tests/data/test-data/MaxMind-DB-test-decoder.mmdb",
            self.mode,
        )
        with self.assertRaises(TypeError):
            reader.metadata("blah")  # type:  ignore[call-arg]
        reader.close()

    def test_metadata_unknown_attribute(self) -> None:
        reader = open_database(
            "tests/data/test-data/MaxMind-DB-test-decoder.mmdb",
            self.mode,
        )
        metadata = reader.metadata()
        with self.assertRaisesRegex(
            AttributeError,
            r"'((maxminddb(_rust)?\.)?(extension\.)?)?Metadata' "
            r"object has no attribute 'blah'",
        ):
            metadata.blah  # type:  ignore[attr-defined]  # noqa: B018
        reader.close()

    def test_close(self) -> None:
        reader = open_database(
            "tests/data/test-data/MaxMind-DB-test-decoder.mmdb",
            self.mode,
        )
        reader.close()

    def test_double_close(self) -> None:
        reader = open_database(
            "tests/data/test-data/MaxMind-DB-test-decoder.mmdb",
            self.mode,
        )
        reader.close()
        # Check that calling close again doesn't raise an exception
        reader.close()

    def test_closed_get(self) -> None:
        if self.mode in [MODE_MEMORY, MODE_FD]:
            return
        reader = open_database(
            "tests/data/test-data/MaxMind-DB-test-decoder.mmdb",
            self.mode,
        )
        reader.close()
        with self.assertRaisesRegex(
            ValueError,
            "Attempt to read from a closed MaxMind DB.|closed",
        ):
            reader.get(self.ipf("1.1.1.1"))

    def test_with_statement(self) -> None:
        filename = "tests/data/test-data/MaxMind-DB-test-ipv4-24.mmdb"
        with open_database(filename, self.mode) as reader:
            self._check_ip_v4(reader, filename)
        self.assertEqual(reader.closed, True)

    def test_with_statement_close(self) -> None:
        filename = "tests/data/test-data/MaxMind-DB-test-ipv4-24.mmdb"
        reader = open_database(filename, self.mode)
        reader.close()

        with (
            self.assertRaisesRegex(
                ValueError,
                "Attempt to reopen a closed MaxMind DB",
            ),
            reader,
        ):
            pass

    def test_closed(self) -> None:
        reader = open_database(
            "tests/data/test-data/MaxMind-DB-test-decoder.mmdb",
            self.mode,
        )
        self.assertEqual(reader.closed, False)
        reader.close()
        self.assertEqual(reader.closed, True)

    @unittest.skip("Not applicable - Reader.__new__ requires a database")
    def test_iterate_uninitialized_reader(self) -> None:
        reader = self.reader_class.__new__(self.reader_class)
        # The C reader raises in iter(), the pure Python reader in next().
        with self.assertRaisesRegex(ValueError, "closed MaxMind DB"):
            next(iter(reader))

    @unittest.skip("Not applicable - Reader.__new__ requires a database")
    def test_close_uninitialized_reader(self) -> None:
        reader = self.reader_class.__new__(self.reader_class)
        self.assertTrue(reader.closed)
        reader.close()
        self.assertTrue(reader.closed)

    @unittest.skip("Not applicable - Reader.__init__ does not reopen a reader")
    def test_reinitialize_reopens_the_reader(self) -> None:
        reader = open_database(
            f"{_TEST_DATA_DIR}/MaxMind-DB-test-ipv4-24.mmdb",
            self.mode,
        )
        self.addCleanup(reader.close)
        iterator = iter(reader)
        next(iterator)
        self._reinitialize(reader, _DECODER_DB, self.mode)
        self.assertEqual(reader.metadata().database_type, "MaxMind DB Decoder Test")
        # The iterator walked the old database, so it must stop.
        with self.assertRaisesRegex(ValueError, "reopened MaxMind DB"):
            next(iterator)

        reader.close()
        self._reinitialize(reader, _DECODER_DB, self.mode)
        self.assertFalse(reader.closed)
        self.assertIsNotNone(reader.get("::1.1.1.0"))

    @unittest.skip("Not applicable - Reader.__init__ does not reopen a reader")
    def test_reinitialize_at_the_last_record(self) -> None:
        # The last record of this database is the right child of the root, so
        # no node of the walk remains after it.
        reader = open_database(
            f"{_TEST_DATA_DIR}/MaxMind-DB-test-decoder-value-limit.mmdb",
            self.mode,
        )
        self.addCleanup(reader.close)
        count = sum(1 for _ in reader)
        iterator = iter(reader)
        for _ in range(count):
            next(iterator)
        self._reinitialize(reader, _DECODER_DB, self.mode)
        with self.assertRaisesRegex(ValueError, "reopened MaxMind DB"):
            next(iterator)

    @unittest.skip("Known difference - an iterator finishes after close()")
    def test_iterate_after_close(self) -> None:
        reader = open_database(_DECODER_DB, self.mode)
        iterator = iter(reader)
        next(iterator)
        reader.close()
        with self.assertRaisesRegex(ValueError, "closed MaxMind DB"):
            next(iterator)

    @unittest.skip("Not applicable - Reader.__init__ does not reopen a reader")
    def test_failed_reinitialize(self) -> None:
        reader = open_database(_DECODER_DB, self.mode)
        self.addCleanup(reader.close)

        # A failed reinit leaves the old database open.
        with self.assertRaisesRegex(ValueError, "Unsupported open mode"):
            self._reinitialize(reader, _DECODER_DB, 100)
        if self.mode != MODE_FD:
            with self.assertRaises(FileNotFoundError):
                self._reinitialize(reader, "missing.mmdb", self.mode)
        with self.assertRaises(InvalidDatabaseError):
            self._reinitialize(reader, "README.md", self.mode)
        self.assertFalse(reader.closed)
        self.assertEqual(reader.metadata().database_type, "MaxMind DB Decoder Test")
        self.assertIsNotNone(reader.get("::1.1.1.0"))

    def _reinitialize(self, reader: Any, path: str, mode: int) -> None:  # noqa: ANN401
        with _database_source(path, mode) as database:
            reader.__init__(database, mode)

    def test_closed_metadata(self) -> None:
        reader = open_database(
            "tests/data/test-data/MaxMind-DB-test-decoder.mmdb",
            self.mode,
        )
        reader.close()

        # The primary purpose of this is to ensure the extension doesn't
        # segfault
        try:
            metadata = reader.metadata()
        except OSError as ex:
            self.assertEqual(
                "Attempt to read from a closed MaxMind DB.",
                str(ex),
                "extension throws exception",
            )
        else:
            self.assertIsNotNone(metadata, "pure Python implementation returns value")

    def test_reading_from_buffer(self) -> None:
        filename = "tests/data/test-data/MaxMind-DB-test-ipv4-24.mmdb"
        with pathlib.Path(filename).open("rb") as f:
            buf = io.BytesIO(f.read())
        # we have to use unpatched open_database here because the patched version
        # calls open() on our buffer
        reader = maxminddb.open_database(buf, MODE_FD)
        self._check_ip_v4(reader, filename)
        reader.close()

    if os.name != "nt":

        def test_multiprocessing(self) -> None:
            self._check_concurrency(self.mp.Process)

        def test_threading(self) -> None:
            self._check_concurrency(threading.Thread)

        def _check_concurrency(self, worker_class) -> None:  # noqa: ANN001
            reader = open_database(
                "tests/data/test-data/GeoIP2-Domain-Test.mmdb",
                self.mode,
            )

            def lookup(pipe) -> None:  # noqa: ANN001
                try:
                    for i in range(32):
                        reader.get(self.ipf(f"65.115.240.{i}"))
                    pipe.send(1)
                except:  # noqa: E722
                    pipe.send(0)
                finally:
                    if worker_class is self.mp.Process:  # type: ignore[attr-defined]
                        reader.close()
                    pipe.close()

            pipes = [self.mp.Pipe() for _ in range(32)]
            procs = [worker_class(target=lookup, args=(c,)) for (_, c) in pipes]
            for proc in procs:
                proc.start()
            for proc in procs:
                proc.join()

            reader.close()

            count = sum([p.recv() for (p, _) in pipes])

            self.assertEqual(count, 32, "expected number of successful lookups")

    def _check_metadata(
        self,
        reader: Reader,
        ip_version: int,
        record_size: int,
    ) -> None:
        metadata = reader.metadata()

        self.assertEqual(2, metadata.binary_format_major_version, "major version")
        self.assertEqual(metadata.binary_format_minor_version, 0)
        self.assertGreater(metadata.build_epoch, 1373571901)
        self.assertEqual(metadata.database_type, "Test")

        self.assertEqual(
            {"en": "Test Database", "zh": "Test Database Chinese"},
            metadata.description,
        )
        self.assertEqual(metadata.ip_version, ip_version)
        self.assertEqual(metadata.languages, ["en", "zh"])
        self.assertGreater(metadata.node_count, 36)

        self.assertEqual(metadata.record_size, record_size)
        self.assertEqual(metadata.node_byte_size, record_size // 4)
        self.assertEqual(
            metadata.search_tree_size,
            metadata.node_count * record_size // 4,
        )

    def _check_ip_v4(self, reader: Reader, file_name: str) -> None:
        for i in range(6):
            address = "1.1.1." + str(pow(2, i))
            self.assertEqual(
                {"ip": address},
                reader.get(self.ipf(address)),
                "found expected data record for " + address + " in " + file_name,
            )

        pairs = {
            "1.1.1.3": "1.1.1.2",
            "1.1.1.5": "1.1.1.4",
            "1.1.1.7": "1.1.1.4",
            "1.1.1.9": "1.1.1.8",
            "1.1.1.15": "1.1.1.8",
            "1.1.1.17": "1.1.1.16",
            "1.1.1.31": "1.1.1.16",
        }
        for key_address, value_address in pairs.items():
            data = {"ip": value_address}

            self.assertEqual(
                data,
                reader.get(self.ipf(key_address)),
                "found expected data record for " + key_address + " in " + file_name,
            )

        for ip in ["1.1.1.33", "255.254.253.123"]:
            self.assertIsNone(reader.get(self.ipf(ip)))

    def _check_ip_v6(self, reader: Reader, file_name: str) -> None:
        subnets = ["::1:ffff:ffff", "::2:0:0", "::2:0:40", "::2:0:50", "::2:0:58"]

        for address in subnets:
            self.assertEqual(
                {"ip": address},
                reader.get(self.ipf(address)),
                "found expected data record for " + address + " in " + file_name,
            )

        pairs = {
            "::2:0:1": "::2:0:0",
            "::2:0:33": "::2:0:0",
            "::2:0:39": "::2:0:0",
            "::2:0:41": "::2:0:40",
            "::2:0:49": "::2:0:40",
            "::2:0:52": "::2:0:50",
            "::2:0:57": "::2:0:50",
            "::2:0:59": "::2:0:58",
        }

        for key_address, value_address in pairs.items():
            self.assertEqual(
                {"ip": value_address},
                reader.get(self.ipf(key_address)),
                "found expected data record for " + key_address + " in " + file_name,
            )

        for ip in ["1.1.1.33", "255.254.253.123", "89fa::"]:
            self.assertIsNone(reader.get(self.ipf(ip)))


class TestExtensionReader(BaseTestReader):
    mode = MODE_MMAP_EXT
    reader_class = maxminddb.Reader

    def test_invalid_utf8_key_does_not_leak(self) -> None:
        def fail_to_decode(count: int) -> None:
            for _ in range(count):
                with contextlib.suppress(InvalidDatabaseError):
                    reader.get("163.254.149.39")

        with maxminddb.Reader(
            "tests/data/bad-data/maxminddb-python/bad-unicode-in-map-key.mmdb",
            MODE_MMAP_EXT,
        ) as reader:
            fail_to_decode(100)
            gc.collect()
            tracemalloc.start()
            try:
                before, _ = tracemalloc.get_traced_memory()
                fail_to_decode(2000)
                gc.collect()
                after, _ = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
        # A leaked dict on each failure keeps about 128 KB.
        self.assertLess(after - before, 16_000)

    def test_file_object_is_refused(self) -> None:
        # MODE_MMAP_EXT needs a path, as the official C extension does.
        with self.assertRaisesRegex(TypeError, "Use MODE_FD for a file object"):
            open_database(io.BytesIO(b""), MODE_MMAP_EXT)


class TestExtensionReaderWithIPObjects(BaseTestReader):
    mode = MODE_MMAP_EXT
    use_ip_objects = True
    reader_class = maxminddb.Reader


class TestModule(unittest.TestCase):
    def test_mode_is_exported(self) -> None:
        # maxminddb_rust has no const submodule. Mode is in the top-level
        # module, and the MODE_* constants are its members, as upstream.
        self.assertTrue(issubclass(maxminddb.Mode, enum.IntEnum))
        for mode in maxminddb.Mode:
            self.assertIs(getattr(maxminddb, f"MODE_{mode.name}"), mode)

    @unittest.skip("Not applicable - maxminddb_rust has no types submodule")
    def test_record_subscript_works_at_runtime(self) -> None:
        pass


class TestAutoReader(BaseTestReader):
    mode = MODE_AUTO
    reader_class = maxminddb.Reader


class TestMMAPReader(BaseTestReader):
    mode = MODE_MMAP
    reader_class = maxminddb.Reader


# We want one pure Python test to use IP objects, it doesn't
# really matter which one.
class TestMMAPReaderWithIPObjects(BaseTestReader):
    mode = MODE_MMAP
    use_ip_objects = True
    reader_class = maxminddb.Reader


class TestFileReader(BaseTestReader):
    mode = MODE_FILE
    reader_class = maxminddb.Reader


class TestMemoryReader(BaseTestReader):
    mode = MODE_MEMORY
    reader_class = maxminddb.Reader


class TestFDReader(BaseTestReader):
    def setUp(self) -> None:
        self.open_database_patcher = mock.patch(__name__ + ".open_database")
        self.addCleanup(self.open_database_patcher.stop)
        self.open_database = self.open_database_patcher.start()
        self.open_database.side_effect = get_reader_from_file_descriptor

    mode = MODE_FD
    reader_class = maxminddb.Reader


class TestReaderInitialization(unittest.TestCase):
    def test_auto_mode_reads_file_object_from_its_position(self) -> None:
        data = pathlib.Path(
            f"{_TEST_DATA_DIR}/MaxMind-DB-test-ipv4-24.mmdb",
        ).read_bytes()
        with tempfile.TemporaryFile() as file_object:
            file_object.write(b"header" + data)
            file_object.seek(len(b"header"))
            with maxminddb.open_database(file_object, MODE_AUTO) as reader:
                self.assertEqual(reader.get("1.1.1.1"), {"ip": "1.1.1.1"})

    def test_auto_mode_reads_wrapped_and_piped_streams(self) -> None:
        path = f"{_TEST_DATA_DIR}/MaxMind-DB-test-ipv4-24.mmdb"
        data = pathlib.Path(path).read_bytes()

        with tempfile.TemporaryDirectory() as directory:
            gz_path = pathlib.Path(directory) / "db.mmdb.gz"
            with gzip.open(gz_path, "wb") as compressed:
                compressed.write(data)
            with gz_path.open("rb") as backing:
                stream = io.BufferedReader(gzip.GzipFile(fileobj=backing))
                with maxminddb.open_database(stream, MODE_AUTO) as reader:
                    self.assertEqual(reader.get("1.1.1.1"), {"ip": "1.1.1.1"})
                # The reader does not close the caller's stream.
                self.assertFalse(stream.closed)
                stream.close()

        # The fixture is far smaller than the pipe buffer, so the write to the
        # pipe does not block.
        read_fd, write_fd = os.pipe()
        os.write(write_fd, data)
        os.close(write_fd)
        pipe = os.fdopen(read_fd, "rb")
        try:
            with maxminddb.open_database(pipe, MODE_AUTO) as reader:
                self.assertEqual(reader.get("1.1.1.1"), {"ip": "1.1.1.1"})
            self.assertFalse(pipe.closed)
        finally:
            pipe.close()

    def test_auto_mode_accepts_any_database_type(self) -> None:
        path = f"{_TEST_DATA_DIR}/MaxMind-DB-test-ipv4-24.mmdb"
        data = pathlib.Path(path).read_bytes()

        class PathWithTextRead:
            """A path object with a text read(), as py.path.local has."""

            def __fspath__(self) -> str:
                return path

            def read(self) -> str:
                return "not the database"

        with pathlib.Path(path).open("rb") as file_object:
            sources: list[tuple[str, object]] = [
                ("path", path),
                ("bytes path", os.fsencode(path)),
                ("file object", file_object),
                ("BytesIO", io.BytesIO(data)),
                # A path wins over read().
                ("path with a text read()", PathWithTextRead()),
            ]
            for name, database in sources:
                with (
                    self.subTest(name),
                    maxminddb.open_database(database, MODE_AUTO) as reader,  # type: ignore[arg-type]
                ):
                    self.assertEqual(reader.get("1.1.1.1"), {"ip": "1.1.1.1"})

        # Upstream's pure Python reader also takes a file descriptor here.
        # maxminddb_rust refuses it, as open_database does upstream when the
        # C extension is installed.
        descriptor = os.open(path, os.O_RDONLY)
        self.addCleanup(os.close, descriptor)
        with self.assertRaisesRegex(TypeError, r"\(int\)"):
            maxminddb.open_database(descriptor, MODE_AUTO)  # type: ignore[arg-type]

    def test_database_type_must_match_mode(self) -> None:
        path = f"{_TEST_DATA_DIR}/MaxMind-DB-test-decoder.mmdb"
        reader_class = maxminddb.Reader
        with self.assertRaisesRegex(TypeError, r"\(str\)\. MODE_FD takes"):
            reader_class(path, MODE_FD)
        with self.assertRaisesRegex(TypeError, r"\(int\)\. MODE_FD takes"):
            reader_class(3, MODE_FD)  # type: ignore[arg-type]
        with pathlib.Path(path).open("rb") as database:
            for mode in (MODE_FILE, MODE_MEMORY, MODE_MMAP, MODE_MMAP_EXT):
                with (
                    self.subTest(mode=mode),
                    self.assertRaisesRegex(TypeError, "Use MODE_FD"),
                ):
                    reader_class(database, mode)  # type: ignore[arg-type]

        for bad in (None, object()):
            with self.assertRaisesRegex(TypeError, "Unsupported database type"):
                reader_class(bad, MODE_AUTO)  # type: ignore[arg-type]
        # The module is in the name, so that a zipfile.Path is not just "Path".
        with self.assertRaisesRegex(TypeError, r"\(decimal\.Decimal\)"):
            reader_class(decimal.Decimal(1), MODE_AUTO)  # type: ignore[arg-type]

        class TextWrapper:
            """A text file wrapper, such as tempfile.NamedTemporaryFile("r")."""

            def __init__(self, text_file: IO[str]) -> None:
                self.text_file = text_file

            def read(self) -> str:
                return self.text_file.read()

        for mode in (MODE_AUTO, MODE_FD):
            with (
                self.subTest(mode=mode),
                # read() fails to decode the database.
                pathlib.Path(path).open(encoding="utf-8") as text_file,
            ):
                for text_source in (text_file, TextWrapper(text_file)):
                    text_file.seek(0)
                    with self.assertRaisesRegex(TypeError, "binary mode"):
                        reader_class(text_source, mode)  # type: ignore[arg-type]

        # A bool is an int, but not a file descriptor.
        with self.assertRaisesRegex(TypeError, r"\(bool\)"):
            reader_class(False, MODE_AUTO)  # type: ignore[arg-type]  # noqa: FBT003
        with self.assertRaisesRegex(TypeError, r"\(bool\)\. Pass a path"):
            reader_class(False, MODE_FILE)  # type: ignore[arg-type]  # noqa: FBT003

    def test_path_modes_refuse_a_descriptor_with_index(self) -> None:
        # Upstream's path modes take a file descriptor, such as an object
        # with __index__. maxminddb_rust takes only paths and file objects.
        class Descriptor:
            """A file descriptor object, as numpy.int64 is."""

            def __init__(self, fd: int) -> None:
                self.fd = fd

            def __index__(self) -> int:
                return self.fd

        path = f"{_TEST_DATA_DIR}/MaxMind-DB-test-ipv4-24.mmdb"
        fd = os.open(path, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        for mode in (MODE_AUTO, MODE_MMAP, MODE_MMAP_EXT):
            with (
                self.subTest(mode=mode),
                self.assertRaisesRegex(TypeError, r"\.Descriptor\)"),
            ):
                maxminddb.open_database(Descriptor(fd), mode)  # type: ignore[arg-type]
        # A bool gets the bool error.
        with self.assertRaisesRegex(TypeError, r"\(bool\)\. Pass a path"):
            maxminddb.open_database(False)  # type: ignore[arg-type]  # noqa: FBT003

    def test_index_that_raises_is_not_a_descriptor(self) -> None:
        class Array:
            """An array of many values, as a numpy.ndarray is."""

            def __index__(self) -> int:
                msg = "only integer scalar arrays can be converted"
                raise TypeError(msg)

        with self.assertRaisesRegex(TypeError, r"Array\)\. Pass a path"):
            maxminddb.Reader(Array(), MODE_AUTO)  # type: ignore[arg-type]

    def test_fd_mode_reads_any_binary_reader(self) -> None:
        data = pathlib.Path(
            f"{_TEST_DATA_DIR}/MaxMind-DB-test-ipv4-24.mmdb",
        ).read_bytes()

        class FileWithPath(io.BytesIO):
            def __fspath__(self) -> str:
                return "does-not-exist.mmdb"

        class BytearrayReader:
            def read(self) -> bytes:
                return bytearray(data)  # type: ignore[return-value]

        for database in (FileWithPath(data), BytearrayReader()):
            with (
                self.subTest(type(database).__name__),
                maxminddb.Reader(database, MODE_FD) as reader,  # type: ignore[arg-type]
            ):
                self.assertEqual(reader.get("1.1.1.1"), {"ip": "1.1.1.1"})

        class NoneReader:
            def read(self) -> None:
                return None

        with self.assertRaisesRegex(TypeError, r"returned NoneType, not bytes\.$"):
            maxminddb.Reader(NoneReader(), MODE_FD)  # type: ignore[arg-type]

        class StrReader:
            def read(self) -> str:
                return "text"

        with self.assertRaisesRegex(TypeError, r"returned str, not bytes\. Open"):
            maxminddb.Reader(StrReader(), MODE_FD)  # type: ignore[arg-type]

    def test_invalid_tree_metadata_is_rejected_on_open(self) -> None:
        data = pathlib.Path(
            f"{_TEST_DATA_DIR}/MaxMind-DB-test-ipv4-24.mmdb"
        ).read_bytes()
        cases = (
            (b"record_size\xa1\x18", b"record_size\xa1\x1e"),
            (b"node_count\xc1\x8b", b"node_count\x04\x01\xff\xff\xff\xff"),
        )
        for original, replacement in cases:
            with self.subTest(field=original):
                self.assertEqual(data.count(original), 1)
                database = io.BytesIO(data.replace(original, replacement))
                with self.assertRaises(InvalidDatabaseError):
                    maxminddb.Reader(database, MODE_FD)

    def test_metadata_decode_error_names_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "bad-metadata.mmdb"
            path.write_bytes(_database_with_metadata([([1], "value")]))
            with self.assertRaises(InvalidDatabaseError) as cm:
                maxminddb.Reader(str(path), MODE_MEMORY)
        # maxminddb_rust reports every metadata error as a failed open.
        self.assertEqual(
            str(cm.exception),
            f"Error opening database file ({path}). Is this a valid MaxMind DB file?",
        )

    def test_empty_file_is_rejected(self) -> None:
        # Unlike Python's mmap module, the Rust reader maps an empty file, so
        # the memory-mapped modes are checked too.
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "empty.mmdb"
            path.write_bytes(b"")
            for mode in (MODE_AUTO, MODE_MMAP_EXT, MODE_MMAP, MODE_FILE, MODE_MEMORY):
                with (
                    self.subTest(mode=mode),
                    self.assertRaisesRegex(
                        InvalidDatabaseError, "Is this a valid MaxMind DB"
                    ),
                ):
                    maxminddb.Reader(path, mode)


class TestOldReader(unittest.TestCase):
    def test_old_reader(self) -> None:
        reader = maxminddb.Reader("tests/data/test-data/MaxMind-DB-test-decoder.mmdb")
        record = cast("dict", reader.get("::1.1.1.0"))

        self.assertEqual(record["array"], [1, 2, 3])
        reader.close()


class TestModuleUnderTest(unittest.TestCase):
    def test_maxminddb_is_maxminddb_rust(self) -> None:
        # Upstream imports maxminddb.extension. That import rebinds the
        # maxminddb name to the official package when it is installed, so
        # these tests would silently exercise the wrong reader.
        rust = sys.modules["maxminddb_rust"]
        self.assertIs(maxminddb, rust)
        for name in ("InvalidDatabaseError", "open_database"):
            with self.subTest(name=name):
                self.assertIs(globals()[name], getattr(rust, name))


del BaseTestReader
