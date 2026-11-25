from __future__ import annotations

import ipaddress
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import maxminddb_rust

DB_PATH = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"


@pytest.mark.parametrize("error", [RuntimeError, ValueError, TypeError])
@pytest.mark.parametrize("name", ["IPv4Address", "IPv6Address"])
def test_get_propagates_error_raised_by_ip_attribute(
    name: str, error: type[Exception]
) -> None:
    def failing_ip(_self: object) -> int:
        msg = "_ip failed"
        raise error(msg)

    ip_type = type(name, (), {"_ip": property(failing_ip)})

    reader = maxminddb_rust.open_database(DB_PATH)
    with reader, pytest.raises(error, match="_ip failed"):
        reader.get(ip_type())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("name", "ip"), [("IPv4Address", "81.2.69.142"), ("IPv6Address", "2001:2b8::")]
)
@pytest.mark.parametrize("ip_attr", [None, "not an int", -1, 1 << 128])
def test_get_falls_back_when_ip_attribute_is_unusable(
    name: str, ip: str, ip_attr: object
) -> None:
    namespace: dict[str, object] = {"packed": ipaddress.ip_address(ip).packed}
    if ip_attr is not None:
        namespace["_ip"] = ip_attr
    ip_type = type(name, (), namespace)

    with maxminddb_rust.open_database(DB_PATH) as reader:
        expected = reader.get(ip)
        assert expected is not None
        assert reader.get(ip_type()) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("name", "ip"), [("IPv4Address", "81.2.69.142"), ("IPv6Address", "2001:2b8::")]
)
def test_get_does_not_convert_ip_attribute_that_is_not_int(name: str, ip: str) -> None:
    calls: list[str] = []

    class Index:
        def __index__(self) -> int:
            calls.append("__index__")
            msg = "__index__ failed"
            raise RuntimeError(msg)

    ip_type = type(
        name, (), {"_ip": Index(), "packed": ipaddress.ip_address(ip).packed}
    )

    with maxminddb_rust.open_database(DB_PATH) as reader:
        expected = reader.get(ip)
        assert expected is not None
        assert reader.get(ip_type()) == expected  # type: ignore[arg-type]
    assert calls == []


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


class _IPv4Subclass(ipaddress.IPv4Address):
    pass


class _IPv6Subclass(ipaddress.IPv6Address):
    pass


_Lookup = Callable[[maxminddb_rust.Reader, Any], Any]

_LOOKUPS: dict[str, _Lookup] = {
    "get": lambda reader, ip: reader.get(ip),
    "get_with_prefix_len": lambda reader, ip: reader.get_with_prefix_len(ip),
    "get_path": lambda reader, ip: reader.get_path(ip, ("country", "iso_code")),
    "get_many": lambda reader, ip: reader.get_many([ip]),
    "get_many_path": lambda reader, ip: reader.get_many_path(
        [ip], ("country", "iso_code")
    ),
}


@pytest.mark.parametrize("lookup", _LOOKUPS.values(), ids=_LOOKUPS.keys())
@pytest.mark.parametrize(
    ("make", "ip"),
    [
        (_IPv4Subclass, "81.2.69.142"),
        (_IPv6Subclass, "2001:2b8::"),
        (lambda ip: ipaddress.IPv4Interface(f"{ip}/24"), "81.2.69.142"),
        (lambda ip: ipaddress.IPv6Interface(f"{ip}/64"), "2001:2b8::"),
    ],
    ids=[
        "IPv4Address subclass",
        "IPv6Address subclass",
        "IPv4Interface",
        "IPv6Interface",
    ],
)
def test_lookups_accept_ip_address_subclasses(
    lookup: _Lookup, make: Callable[[str], object], ip: str
) -> None:
    with maxminddb_rust.open_database(DB_PATH) as reader:
        assert reader.get(ip) is not None
        assert lookup(reader, make(ip)) == lookup(reader, ip)


def test_get_ignores_cached_class_that_is_not_a_type() -> None:
    # The address classes are cached on the first object lookup in the process,
    # so patch ipaddress in a new interpreter.
    code = """
import ipaddress
import sys
from unittest import mock

import maxminddb_rust

class Sub(ipaddress.IPv4Address):
    pass

reader = maxminddb_rust.open_database(sys.argv[1])
real = ipaddress.ip_address("81.2.69.142")
sub = Sub("81.2.69.142")
expected = reader.get("81.2.69.142")
assert expected is not None
with mock.patch("ipaddress.IPv4Address"):
    assert reader.get(real) == expected
# Known limitation: with a mock cached, subclasses match no cached class and
# are rejected with the usual error rather than an error from the check.
try:
    reader.get(sub)
except TypeError as err:
    assert str(err) == "argument 1 must be a string or ipaddress object", err
else:
    raise AssertionError("expected TypeError")
"""
    subprocess.run(  # noqa: S603 - Fixed argument vector; no shell.
        [sys.executable, "-c", code, str(DB_PATH)], check=True
    )
