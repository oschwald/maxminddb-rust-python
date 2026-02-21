#!/usr/bin/env python3

from __future__ import annotations

import argparse
import concurrent.futures
import random
import socket
import struct
import time

import maxminddb_rust


def chunk_ranges(total: int, workers: int) -> list[tuple[int, int]]:
    base = total // workers
    remainder = total % workers
    ranges = []
    start = 0
    for i in range(workers):
        size = base + (1 if i < remainder else 0)
        end = start + size
        ranges.append((start, end))
        start = end
    return ranges


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark maxminddb parallel lookups with shared Reader."
    )
    parser.add_argument("--count", default=500000, type=int, help="total lookups")
    parser.add_argument(
        "--workers",
        default="1,2,4,8",
        help="comma-separated worker counts (e.g. 1,2,4,8)",
    )
    parser.add_argument("--file", default=None, help="path to mmdb file")
    args = parser.parse_args()

    worker_counts = [
        int(value.strip()) for value in args.workers.split(",") if value.strip()
    ]
    if not worker_counts or any(w <= 0 for w in worker_counts):
        raise ValueError("--workers must contain one or more positive integers")

    databases = (
        [(args.file, args.file)]
        if args.file
        else [
            ("/var/lib/GeoIP/GeoLite2-Country.mmdb", "GeoLite2-Country (9.6MB)"),
            ("/var/lib/GeoIP/GeoLite2-City.mmdb", "GeoLite2-City (61MB)"),
            ("/var/lib/GeoIP/GeoIP2-City.mmdb", "GeoIP2-City (117MB)"),
        ]
    )

    random.seed(0)
    ips = [
        socket.inet_ntoa(struct.pack("!L", random.getrandbits(32)))
        for _ in range(args.count)
    ]

    print(f"Total lookups: {args.count:,}")
    print(f"Workers: {worker_counts}")
    print()

    for database_path, database_label in databases:
        print(f"Database: {database_label}")
        with maxminddb_rust.open_database(database_path) as reader:

            def lookup_range(start: int, end: int) -> int:
                for ip in ips[start:end]:
                    reader.get(ip)
                return end - start

            baseline = None
            for workers in worker_counts:
                ranges = chunk_ranges(args.count, workers)
                started = time.perf_counter()
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=workers
                ) as executor:
                    futures = [
                        executor.submit(lookup_range, start, end)
                        for (start, end) in ranges
                    ]
                    completed = sum(f.result() for f in futures)
                elapsed = time.perf_counter() - started
                throughput = int(completed / elapsed)
                if baseline is None:
                    baseline = throughput
                    speedup = 1.0
                else:
                    speedup = throughput / baseline
                print(
                    f"{workers:>2d} worker(s): {throughput:>10,} lookups/s"
                    f"  ({speedup:>4.2f}x vs 1 worker)"
                )
        print()


if __name__ == "__main__":
    main()
