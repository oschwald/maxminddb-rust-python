#!/usr/bin/python
"""
Comprehensive benchmark for maxminddb module.
Tests various lookup patterns and database sizes.
"""

import argparse
import random
import socket
import struct
import timeit
import maxminddb_rust


def generate_ips(count):
    random.seed(0)
    return [
        socket.inet_ntoa(struct.pack("!L", random.getrandbits(32)))
        for _ in range(count)
    ]


def format_number(n):
    """Format number with thousands separator."""
    return f"{int(n):,}"


def run_benchmark(file_path, count, description):
    """Run a single benchmark and return results."""
    reader = maxminddb_rust.open_database(file_path)
    ips = generate_ips(count)

    def lookup_ip_addresses():
        for ip in ips:
            reader.get(ip)

    # Warmup
    for ip in ips[:1000]:
        reader.get(ip)

    elapsed = timeit.timeit(
        lookup_ip_addresses,
        number=1,
    )

    lookups_per_sec = count / elapsed
    print(f"{description:50s} {format_number(lookups_per_sec):>15s} lookups/sec")
    return lookups_per_sec


def main():
    parser = argparse.ArgumentParser(description="Comprehensive maxminddb benchmarks")
    parser.add_argument(
        "--count", default=250000, type=int, help="number of lookups per test"
    )
    args = parser.parse_args()

    print("=" * 70)
    print("MaxMind DB Rust Performance Benchmark")
    print("=" * 70)
    print(f"Total lookups per test: {format_number(args.count)}")
    print("=" * 70)
    print()

    databases = [
        ("/var/lib/GeoIP/GeoLite2-Country.mmdb", "GeoLite2-Country (9.6MB)"),
        ("/var/lib/GeoIP/GeoLite2-City.mmdb", "GeoLite2-City (61MB)"),
        ("/var/lib/GeoIP/GeoIP2-City.mmdb", "GeoIP2-City (117MB)"),
    ]

    results = []
    for db_path, description in databases:
        try:
            result = run_benchmark(db_path, args.count, description)
            results.append((description, result))
        except Exception as e:
            print(f"{description:50s} {'Error: ' + str(e):>15s}")

    if results:
        print()
        print("=" * 70)
        avg_lookups = sum(r[1] for r in results) / len(results)
        print(f"Average performance: {format_number(avg_lookups)} lookups/sec")
        print("=" * 70)


if __name__ == "__main__":
    main()
