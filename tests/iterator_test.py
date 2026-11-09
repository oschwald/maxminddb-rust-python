from __future__ import annotations

import functools
import ipaddress
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

import pytest

import maxminddb_rust

if TYPE_CHECKING:
    from collections.abc import Callable


def test_iterator_networks_preserve_hosts_behavior() -> None:
    db_path = (
        Path(__file__).parent / "data" / "test-data" / "MaxMind-DB-test-mixed-24.mmdb"
    )
    with maxminddb_rust.open_database(db_path) as reader:
        for net, _ in reader:
            expected = ipaddress.ip_network((int(net.network_address), net.prefixlen))
            assert net == expected
            assert net.netmask is expected.netmask
            assert str(net) == str(expected)
            assert type(net.hosts()) is type(expected.hosts())
            assert list(net.hosts()) == list(expected.hosts())
    # The network check must not leave a module behind.
    assert not [name for name in sys.modules if "network_check" in name]


def test_iterator_uses_constructors_when_ipaddress_internals_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = (
        Path(__file__).parent / "data" / "test-data" / "MaxMind-DB-test-mixed-24.mmdb"
    )
    with maxminddb_rust.open_database(db_path) as reader:
        # Iterate once first. A later patch must still apply to new iterators.
        assert not any(hasattr(net, "extra") for net, _ in reader)

        for cls in (ipaddress.IPv4Network, ipaddress.IPv6Network):

            def patched(
                self: object,
                *args: object,
                _orig: Callable[..., None] = cls.__init__,
                **kwargs: object,
            ) -> None:
                _orig(self, *args, **kwargs)
                self.extra = "set by __init__"  # type: ignore[attr-defined]

            monkeypatch.setattr(cls, "__init__", patched)

        nets = [net for net, _ in reader]
        assert nets
        assert all(net.extra == "set by __init__" for net in nets)

        monkeypatch.undo()
        assert not any(hasattr(net, "extra") for net, _ in reader)


def test_iterator_keeps_address_state_set_by_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TaggedAddress(ipaddress.IPv4Address):
        def __init__(self, address: object) -> None:
            super().__init__(address)  # type: ignore[arg-type]
            self.tag = "set by __init__"

    monkeypatch.setattr(ipaddress, "IPv4Address", TaggedAddress)
    db_path = (
        Path(__file__).parent / "data" / "test-data" / "MaxMind-DB-test-ipv4-24.mmdb"
    )
    with maxminddb_rust.open_database(db_path) as reader:
        nets = [net for net, _ in reader]
    assert nets
    assert all(net.network_address.tag == "set by __init__" for net in nets)


def test_iterator_keeps_slot_state_set_by_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # vars() does not show slots, so the network check must read them. Python
    # stores a private slot under its mangled name.
    class PublicSlotNetwork(ipaddress.IPv4Network):
        __slots__ = ("tag",)

        def __init__(self, address: object, strict: bool = True) -> None:  # noqa: FBT001, FBT002 - Matches the stdlib signature.
            super().__init__(address, strict)  # type: ignore[arg-type]
            self.tag = "set by __init__"

        def get_tag(self) -> str:
            return self.tag

    class PrivateSlotNetwork(ipaddress.IPv4Network):
        __slots__ = ("__tag",)

        def __init__(self, address: object, strict: bool = True) -> None:  # noqa: FBT001, FBT002 - Matches the stdlib signature.
            super().__init__(address, strict)  # type: ignore[arg-type]
            self.__tag = "set by __init__"

        def get_tag(self) -> str:
            return self.__tag

    db_path = (
        Path(__file__).parent / "data" / "test-data" / "MaxMind-DB-test-ipv4-24.mmdb"
    )
    for cls in (PublicSlotNetwork, PrivateSlotNetwork):
        monkeypatch.setattr(ipaddress, "IPv4Network", cls)
        with maxminddb_rust.open_database(db_path) as reader:
            nets = [net for net, _ in reader]
        assert nets
        assert all(net.get_tag() == "set by __init__" for net in nets)


def test_iterator_uses_constructors_when_a_sample_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The constructor rejects a /32, which the network check builds. Iterating
    # a database with no /32 networks must still work.
    class NoHostNetwork(ipaddress.IPv4Network):
        def __init__(self, address: object, strict: bool = True) -> None:  # noqa: FBT001, FBT002 - Matches the stdlib signature.
            if isinstance(address, tuple) and address[1] == ipaddress.IPV4LENGTH:
                msg = "no /32 networks"
                raise ValueError(msg)
            super().__init__(address, strict)  # type: ignore[arg-type]

    monkeypatch.setattr(ipaddress, "IPv4Network", NoHostNetwork)
    db_path = (
        Path(__file__).parent / "data" / "test-data" / "MaxMind-DB-test-ipv6-24.mmdb"
    )
    with maxminddb_rust.open_database(db_path) as reader:
        nets = [net for net, _ in reader]
    assert nets


def test_iterator_keeps_state_set_for_one_prefix_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The samples have no /28 network, so only the constructor check sees this
    # patch. functools.wraps must not hide it.
    original = ipaddress.IPv4Network.__init__

    @functools.wraps(original)
    def patched(self: ipaddress.IPv4Network, *args: object, **kwargs: object) -> None:
        original(self, *args, **kwargs)  # type: ignore[arg-type]
        if self.prefixlen == 28:  # noqa: PLR2004 - The /28 in the database.
            self.tag = "set by __init__"  # type: ignore[attr-defined]

    monkeypatch.setattr(ipaddress.IPv4Network, "__init__", patched)
    db_path = (
        Path(__file__).parent / "data" / "test-data" / "MaxMind-DB-test-ipv4-24.mmdb"
    )
    with maxminddb_rust.open_database(db_path) as reader:
        tagged = [net for net, _ in reader if net.prefixlen == 28]  # noqa: PLR2004
    assert tagged
    assert all(net.tag == "set by __init__" for net in tagged)


def test_iterator_does_not_call_replacement_constructor_for_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class CountingNetwork(ipaddress.IPv4Network):
        def __init__(self, address: object, strict: bool = True) -> None:  # noqa: FBT001, FBT002 - Matches the stdlib signature.
            calls.append(address)
            super().__init__(address, strict)  # type: ignore[arg-type]

    monkeypatch.setattr(ipaddress, "IPv4Network", CountingNetwork)
    db_path = (
        Path(__file__).parent / "data" / "test-data" / "MaxMind-DB-test-ipv4-24.mmdb"
    )
    with maxminddb_rust.open_database(db_path) as reader:
        nets = [net for net, _ in reader]
    assert nets
    assert len(calls) == len(nets)


def test_iterator_calls_non_type_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A test may replace a class with a callable that is not a type.
    replacement = mock.MagicMock()
    monkeypatch.setattr(ipaddress, "IPv4Network", replacement)
    db_path = (
        Path(__file__).parent / "data" / "test-data" / "MaxMind-DB-test-ipv4-24.mmdb"
    )
    with maxminddb_rust.open_database(db_path) as reader:
        nets = [net for net, _ in reader]
    assert nets
    assert all(net is replacement.return_value for net in nets)
    assert replacement.call_count == len(nets)


def test_iterator_uses_constructors_when_direct_construction_fails() -> None:
    # The constructors pass an address to __new__. Direct construction does not,
    # so it raises TypeError, and iteration must fall back to the constructors.
    code = """
import ipaddress
import sys

import maxminddb_rust

for cls in (ipaddress.IPv4Address, ipaddress.IPv6Address):
    cls.__new__ = staticmethod(lambda cls, address: object.__new__(cls))

with maxminddb_rust.open_database(sys.argv[1]) as reader:
    for net, _ in reader:
        expected = ipaddress.ip_network((int(net.network_address), net.prefixlen))
        assert net == expected
        assert str(net) == str(expected)
"""
    db_path = (
        Path(__file__).parent / "data" / "test-data" / "MaxMind-DB-test-mixed-24.mmdb"
    )
    subprocess.run(  # noqa: S603 - Fixed argument vector; no shell.
        [sys.executable, "-c", code, str(db_path)], check=True
    )


@pytest.mark.parametrize("error", [AttributeError, TypeError, ValueError])
def test_iterator_uses_constructors_when_network_check_raises(
    monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    # These errors mean direct construction does not fit this Python, for
    # example if a later version drops the IPv6Address _scope_id slot. The
    # classes keep the stdlib constructors, so the error comes from a sample.
    def failing_hash(_self: object) -> int:
        msg = "mismatch"
        raise error(msg)

    monkeypatch.setattr(ipaddress.IPv6Network, "__hash__", failing_hash)
    db_path = (
        Path(__file__).parent / "data" / "test-data" / "MaxMind-DB-test-ipv6-24.mmdb"
    )
    with maxminddb_rust.open_database(db_path) as reader:
        nets = [net for net, _ in reader if net.prefixlen == net.max_prefixlen]
    assert nets
    # The constructors set hosts to a lambda. The direct path does not.
    assert all(net.hosts.__name__ == "<lambda>" for net in nets)


def test_iterator_propagates_unexpected_network_check_errors() -> None:
    # A RuntimeError is not a sign that the ipaddress internals differ. It must
    # propagate, and a later iterator must still use direct construction.
    code = """
import ipaddress
import sys

import maxminddb_rust

classes = (ipaddress.IPv4Network, ipaddress.IPv6Network)

def failing_hash(self):
    raise RuntimeError("unexpected failure")

for cls in classes:
    cls.__hash__ = failing_hash

with maxminddb_rust.open_database(sys.argv[1]) as reader:
    try:
        next(iter(reader))
    except RuntimeError as err:
        assert str(err) == "unexpected failure", err
    else:
        raise AssertionError("RuntimeError was not raised")

    for cls in classes:
        del cls.__hash__

    for net, _ in reader:
        if net.prefixlen == net.max_prefixlen:
            # The constructors set hosts to a lambda; the direct path does not.
            assert net.hosts.__name__ != "<lambda>", net.hosts
            break
    else:
        raise AssertionError("no single-host network found")
"""
    db_path = (
        Path(__file__).parent / "data" / "test-data" / "MaxMind-DB-test-ipv4-32.mmdb"
    )
    subprocess.run(  # noqa: S603 - Fixed argument vector; no shell.
        [sys.executable, "-c", code, str(db_path)], check=True
    )
