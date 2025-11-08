#!/usr/bin/env python3
"""
Batch processing example for maxminddb_rust module.

Demonstrates the use of the get_many() extension method for efficient
batch IP lookups. This method is significantly faster than calling get()
repeatedly, as it reduces Python call overhead and releases the GIL
during the entire batch operation.

Note: get_many() is an extension method not available in the official
maxminddb package.
"""

import time
import maxminddb_rust
from typing import List

# Path to your MaxMind database file
DATABASE_PATH = "/var/lib/GeoIP/GeoIP2-City.mmdb"


def generate_sample_ips(count: int = 100) -> List[str]:
    """Generate a list of sample IP addresses for testing."""
    sample_ips = [
        "8.8.8.8",  # Google DNS
        "1.1.1.1",  # Cloudflare DNS
        "208.67.222.222",  # OpenDNS
        "9.9.9.9",  # Quad9 DNS
        "149.112.112.112",  # Quad9 secondary
        "64.6.64.6",  # Verisign DNS
        "8.26.56.26",  # Comodo DNS
        "156.154.70.1",  # Neustar DNS
        "198.41.0.4",  # a.root-servers.net
        "199.9.14.201",  # b.root-servers.net
    ]

    # Repeat the sample IPs to reach the desired count
    result = []
    while len(result) < count:
        result.extend(sample_ips)

    return result[:count]


def basic_batch_lookup():
    """Basic usage of get_many() for batch lookups."""
    print("\n1. Basic batch lookup")
    print("-" * 60)

    ips = [
        "8.8.8.8",
        "1.1.1.1",
        "208.67.222.222",
        "9.9.9.9",
        "149.112.112.112",
    ]

    with maxminddb_rust.open_database(DATABASE_PATH) as reader:
        print(f"   Looking up {len(ips)} IP addresses...")
        results = reader.get_many(ips)

        for ip, result in zip(ips, results):
            if result:
                country = result.get("country", {}).get("names", {}).get("en", "N/A")
                city = result.get("city", {}).get("names", {}).get("en", "N/A")
                print(f"   {ip:15s} -> {country:20s} {city}")
            else:
                print(f"   {ip:15s} -> No data found")


def performance_comparison():
    """Compare performance of get() vs get_many()."""
    print("\n2. Performance comparison: get() vs get_many()")
    print("-" * 60)

    ips = generate_sample_ips(1000000)

    with maxminddb_rust.open_database(DATABASE_PATH) as reader:
        # Method 1: Individual get() calls
        print(f"   Testing {len(ips)} lookups with individual get() calls...")
        start = time.time()
        results_individual = []
        for ip in ips:
            results_individual.append(reader.get(ip))
        time_individual = time.time() - start

        # Method 2: Batch get_many() call
        print(f"   Testing {len(ips)} lookups with get_many()...")
        start = time.time()
        _ = reader.get_many(ips)
        time_batch = time.time() - start

        # Display results
        print(f"\n   Individual get() calls: {time_individual:.4f} seconds")
        print(f"   Batch get_many():       {time_batch:.4f} seconds")
        print(f"   Speedup:                {time_individual / time_batch:.2f}x faster")
        print(f"   Throughput (get_many): {len(ips) / time_batch:,.0f} lookups/sec")


def process_log_file_simulation():
    """Simulate processing a log file with batch lookups."""
    print("\n3. Simulated log file processing")
    print("-" * 60)

    # Simulate log entries with IP addresses
    log_entries = [
        "2024-01-15 10:23:45 8.8.8.8 GET /api/users",
        "2024-01-15 10:23:46 1.1.1.1 POST /api/login",
        "2024-01-15 10:23:47 208.67.222.222 GET /api/data",
        "2024-01-15 10:23:48 9.9.9.9 GET /api/status",
        "2024-01-15 10:23:49 149.112.112.112 POST /api/update",
    ]

    # Extract IP addresses from log entries
    ips = [line.split()[2] for line in log_entries]

    print(f"   Processing {len(log_entries)} log entries...")

    with maxminddb_rust.open_database(DATABASE_PATH) as reader:
        # Perform batch lookup
        results = reader.get_many(ips)

        # Process results
        for log_entry, ip, geo_data in zip(log_entries, ips, results):
            if geo_data:
                country = geo_data.get("country", {}).get("iso_code", "??")
                city = geo_data.get("city", {}).get("names", {}).get("en", "Unknown")
            else:
                country = "??"
                city = "Unknown"

            print(f"   [{country}] {city:15s} - {log_entry}")


def aggregate_statistics():
    """Aggregate statistics from batch lookup results."""
    print("\n4. Aggregate statistics from batch lookups")
    print("-" * 60)

    ips = generate_sample_ips(500)

    with maxminddb_rust.open_database(DATABASE_PATH) as reader:
        print(f"   Looking up {len(ips)} IP addresses...")
        results = reader.get_many(ips)

        # Aggregate by country
        country_counts = {}
        city_counts = {}

        for result in results:
            if result:
                country = (
                    result.get("country", {}).get("names", {}).get("en", "Unknown")
                )
                city = result.get("city", {}).get("names", {}).get("en", "Unknown")

                country_counts[country] = country_counts.get(country, 0) + 1
                city_counts[city] = city_counts.get(city, 0) + 1

        # Display top countries
        print("\n   Top 5 countries:")
        for country, count in sorted(
            country_counts.items(), key=lambda x: x[1], reverse=True
        )[:5]:
            print(f"      {country:20s}: {count:3d} IPs")

        # Display top cities
        print("\n   Top 5 cities:")
        for city, count in sorted(
            city_counts.items(), key=lambda x: x[1], reverse=True
        )[:5]:
            print(f"      {city:20s}: {count:3d} IPs")


def batch_with_error_handling():
    """Demonstrate error handling with batch lookups."""
    print("\n5. Batch lookup with error handling")
    print("-" * 60)

    # Mix of valid and invalid IPs
    ips = [
        "8.8.8.8",
        "1.1.1.1",
        "invalid-ip",  # This will cause an error
        "208.67.222.222",
    ]

    with maxminddb_rust.open_database(DATABASE_PATH) as reader:
        try:
            results = reader.get_many(ips)
            print(f"   Successfully looked up {len(results)} IPs")
        except ValueError as e:
            print(f"   Error during batch lookup: {e}")
            print("   Note: get_many() validates all IPs before processing")


def chunked_batch_processing():
    """Process large datasets in chunks using get_many()."""
    print("\n6. Chunked batch processing for large datasets")
    print("-" * 60)

    # Simulate a large dataset
    all_ips = generate_sample_ips(10000)
    chunk_size = 1000

    print(f"   Processing {len(all_ips)} IPs in chunks of {chunk_size}...")

    with maxminddb_rust.open_database(DATABASE_PATH) as reader:
        total_processed = 0
        start = time.time()

        # Process in chunks
        for i in range(0, len(all_ips), chunk_size):
            chunk = all_ips[i : i + chunk_size]
            results = reader.get_many(chunk)
            total_processed += len(results)

            chunk_num = (i // chunk_size) + 1
            total_chunks = (len(all_ips) + chunk_size - 1) // chunk_size
            print(f"   Processed chunk {chunk_num}/{total_chunks} ({len(chunk)} IPs)")

        elapsed = time.time() - start
        print(f"\n   Total processed: {total_processed:,} IPs")
        print(f"   Total time: {elapsed:.4f} seconds")
        print(f"   Throughput: {total_processed / elapsed:,.0f} lookups/sec")


def main():
    """Run all batch processing examples."""
    print("MaxMindDB Batch Processing Examples")
    print("=" * 60)

    basic_batch_lookup()
    performance_comparison()
    process_log_file_simulation()
    aggregate_statistics()
    batch_with_error_handling()
    chunked_batch_processing()

    print("\n" + "=" * 60)
    print("All examples completed successfully!")


if __name__ == "__main__":
    main()
