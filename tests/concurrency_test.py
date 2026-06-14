from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from pathlib import Path
from threading import Barrier, Event
from time import sleep

import maxminddb_rust


DATA_DIR = Path(__file__).parent / "data" / "test-data"
CITY_DB = DATA_DIR / "GeoIP2-City-Test.mmdb"


def test_shared_reader_concurrent_lookup_paths_and_iteration() -> None:
    reader = maxminddb_rust.open_database(CITY_DB)
    start = Barrier(8)
    ips = ["81.2.69.142", "1.1.1.1", "2001:220::"]
    path = ("country", "iso_code")

    def worker(worker_id: int) -> int:
        start.wait(timeout=10)
        operations = 0
        for index in range(200):
            ip = ips[(worker_id + index) % len(ips)]
            reader.get(ip)
            reader.get_path(ip, path)
            reader.get_many(ips)
            reader.get_many_path(ips, path)
            operations += 4
            if index % 50 == 0:
                operations += sum(1 for _ in islice(reader, 3))
        return operations

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            operations = list(executor.map(worker, range(8)))
    finally:
        reader.close()

    assert sum(operations) > 0


def test_close_during_concurrent_lookups_only_reports_closed_reader() -> None:
    reader = maxminddb_rust.open_database(CITY_DB)
    start = Barrier(5)
    stop = Event()
    path = ("country", "iso_code")
    expected_error = "Attempt to read from a closed MaxMind DB."

    def worker() -> int:
        start.wait(timeout=10)
        operations = 0
        while not stop.is_set():
            try:
                reader.get("81.2.69.142")
                reader.get_path("81.2.69.142", path)
                operations += 2
            except ValueError as exc:
                if str(exc) != expected_error:
                    raise
                stop.set()
        return operations

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(worker) for _ in range(4)]
        start.wait(timeout=10)
        sleep(0.01)
        reader.close()
        stop.set()
        operations = [future.result() for future in futures]

    assert reader.closed
    assert sum(operations) > 0
