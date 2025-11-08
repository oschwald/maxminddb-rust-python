#!/usr/bin/env python3
"""
Context manager usage example for maxminddb_rust module.

Demonstrates the recommended way to use maxminddb_rust with Python's 'with' statement,
which ensures the database is properly closed even if an error occurs.
"""

import maxminddb_rust

# Path to your MaxMind database file
DATABASE_PATH = "/var/lib/GeoIP/GeoIP2-City.mmdb"


def basic_context_manager():
    """Basic context manager usage."""
    print("\n1. Basic context manager usage")
    print("-" * 60)

    with maxminddb_rust.open_database(DATABASE_PATH) as reader:
        print(f"   Database is open: {not reader.closed}")

        result = reader.get("8.8.8.8")
        if result:
            country = result.get("country", {}).get("names", {}).get("en", "N/A")
            print(f"   IP 8.8.8.8 is from: {country}")

    # Database is automatically closed after exiting the 'with' block
    print(f"   Database is closed after 'with' block: {reader.closed}")


def context_manager_with_exception_handling():
    """Context manager with exception handling."""
    print("\n2. Context manager with exception handling")
    print("-" * 60)

    try:
        with maxminddb_rust.open_database(DATABASE_PATH) as reader:
            print(f"   Database is open: {not reader.closed}")

            # Look up some IPs
            ips = ["8.8.8.8", "1.1.1.1", "invalid-ip", "208.67.222.222"]

            for ip in ips:
                try:
                    result = reader.get(ip)
                    if result:
                        country = (
                            result.get("country", {}).get("names", {}).get("en", "N/A")
                        )
                        print(f"   {ip:15s} -> {country}")
                    else:
                        print(f"   {ip:15s} -> No data found")
                except ValueError as e:
                    print(f"   {ip:15s} -> Error: {e}")

    except Exception as e:
        print(f"   Unexpected error: {e}")

    # Database is automatically closed even if an exception occurred
    print(f"   Database is closed: {reader.closed}")


def multiple_databases():
    """Using multiple databases simultaneously with context managers."""
    print("\n3. Using multiple databases simultaneously")
    print("-" * 60)

    city_db = "/var/lib/GeoIP/GeoIP2-City.mmdb"
    country_db = "/var/lib/GeoIP/GeoLite2-Country.mmdb"

    # Open multiple databases with nested context managers
    with maxminddb_rust.open_database(
        city_db
    ) as city_reader, maxminddb_rust.open_database(country_db) as country_reader:
        ip = "8.8.8.8"
        print(f"   Looking up {ip} in both databases:")

        # Query city database
        city_result = city_reader.get(ip)
        if city_result:
            city = city_result.get("city", {}).get("names", {}).get("en", "N/A")
            print(f"   City database: {city}")

        # Query country database
        country_result = country_reader.get(ip)
        if country_result:
            country = (
                country_result.get("country", {}).get("names", {}).get("en", "N/A")
            )
            print(f"   Country database: {country}")

    print(f"   Both databases closed: {city_reader.closed and country_reader.closed}")


def context_manager_with_different_modes():
    """Using context manager with different database modes."""
    print("\n4. Context manager with different database modes")
    print("-" * 60)

    # MODE_MMAP (default, best performance)
    print("   Using MODE_MMAP (memory-mapped):")
    with maxminddb_rust.open_database(
        DATABASE_PATH, mode=maxminddb_rust.MODE_MMAP
    ) as reader:
        result = reader.get("8.8.8.8")
        if result:
            country = result.get("country", {}).get("names", {}).get("en", "N/A")
            print(f"      8.8.8.8 -> {country}")

    # MODE_MEMORY (loads entire database into memory)
    print("   Using MODE_MEMORY (in-memory):")
    with maxminddb_rust.open_database(
        DATABASE_PATH, mode=maxminddb_rust.MODE_MEMORY
    ) as reader:
        result = reader.get("1.1.1.1")
        if result:
            country = result.get("country", {}).get("names", {}).get("en", "N/A")
            print(f"      1.1.1.1 -> {country}")


def main():
    """Run all context manager examples."""
    print("MaxMindDB Context Manager Examples")
    print("=" * 60)

    basic_context_manager()
    context_manager_with_exception_handling()
    multiple_databases()
    context_manager_with_different_modes()

    print("\n" + "=" * 60)
    print("All examples completed successfully!")


if __name__ == "__main__":
    main()
