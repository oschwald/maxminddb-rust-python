#!/usr/bin/env python3
"""Measure lookup throughput across worker threads."""

from __future__ import annotations

import argparse
import concurrent.futures
import random
import socket
import struct
import time

from _common import chunks, default_databases, resolve_database

import maxminddb_rust


def main() -> None:
    """Run the benchmark command and report its results."""
    parser = argparse.ArgumentParser(
        description="Benchmark maxminddb threaded lookups with a shared Reader."
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
        msg = "--workers must contain one or more positive integers"
        raise ValueError(msg)

    if args.file:
        paths = [resolve_database(args.file)]
    else:
        paths = default_databases()
        if not paths:
            resolve_database(None)  # Raise the shared, actionable error.

    databases = [
        (
            str(path),
            f"{path.name} ({path.stat().st_size / 1024 / 1024:.1f} MiB)",
        )
        for path in paths
    ]

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

            def lookup_chunk(values: list[str]) -> int:
                for ip in values:
                    reader.get(ip)
                return len(values)

            baseline = None
            for workers in worker_counts:
                worker_chunks = chunks(ips, workers)
                started = time.perf_counter()
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=workers
                ) as executor:
                    futures = [
                        executor.submit(lookup_chunk, values)
                        for values in worker_chunks
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
