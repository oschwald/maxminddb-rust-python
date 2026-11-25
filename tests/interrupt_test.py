from __future__ import annotations

import functools
import itertools
import signal
from ipaddress import ip_address
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import maxminddb_rust

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from types import FrameType

DB_PATH = Path(__file__).parent / "data" / "test-data" / "GeoIP2-City-Test.mmdb"

# Enough addresses, each a database hit that decodes a record, that the batch
# runs for seconds without signal checks. A timer can fire tens of
# milliseconds late on a busy CI runner, so a fast batch could finish first.
BATCH_SIZE = 2_000_000
BATCH_IP = "81.2.69.142"

pytestmark = pytest.mark.skipif(
    not hasattr(signal, "setitimer"), reason="needs signal.setitimer"
)


class _AlarmError(Exception):
    pass


def _raise_alarm(_signum: int, _frame: FrameType | None) -> None:
    raise _AlarmError


def _get_many(reader: maxminddb_rust.Reader, ips: Iterable[object]) -> object:
    return reader.get_many(ips)  # type: ignore[arg-type]


def _get_many_path(reader: maxminddb_rust.Reader, ips: Iterable[object]) -> object:
    return reader.get_many_path(ips, ("subdivisions",))  # type: ignore[arg-type]


@pytest.mark.parametrize("method", [_get_many, _get_many_path])
@pytest.mark.parametrize("container", [list, tuple, iter])
def test_batch_lookup_handles_signal_before_it_finishes(
    method: Callable[[maxminddb_rust.Reader, Iterable[object]], object],
    container: Callable[[list[object]], Iterable[object]],
) -> None:
    started: list[bool] = []
    address = int(ip_address("1.1.1.1"))

    def start_timer(_self: object) -> int:
        # The first address starts the timer, so it fires during the batch.
        started.append(True)
        signal.setitimer(signal.ITIMER_REAL, 0.01)
        return address

    # The last address counts its lookups without running Python code, which
    # would handle the signal itself. `next(counter, self)` returns the next
    # count, an IPv4 address as an int.
    counter = itertools.count(address)
    first_type = type("IPv4Address", (), {"_ip": property(start_timer)})
    last_type = type(
        "IPv4Address", (), {"_ip": property(functools.partial(next, counter))}
    )
    ips: list[object] = [first_type()]
    ips.extend([BATCH_IP] * BATCH_SIZE)
    ips.append(last_type())
    batch = container(ips)

    previous = signal.signal(signal.SIGALRM, _raise_alarm)
    try:
        with (
            maxminddb_rust.open_database(DB_PATH) as reader,
            pytest.raises(_AlarmError),
        ):
            method(reader, batch)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)

    # The batch started and stopped before the last address.
    assert started == [True]
    assert next(counter) == address
