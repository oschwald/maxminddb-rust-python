#!/usr/bin/env python3
"""
Basic usage example for maxminddb_rust module.

Demonstrates simple IP address lookups using maxminddb_rust,
a high-performance Rust-based alternative to the original Python maxminddb package.
"""

import maxminddb_rust

# Path to your MaxMind database file
DATABASE_PATH = "/var/lib/GeoIP/GeoIP2-City.mmdb"


def main():
    """Demonstrate basic maxminddb usage."""
    print("MaxMindDB Basic Usage Example")
    print("=" * 60)

    # Open the database
    print(f"\nOpening database: {DATABASE_PATH}")
    reader = maxminddb_rust.open_database(DATABASE_PATH)

    # Example 1: Simple IP lookup with string
    print("\n1. Looking up IP address: 8.8.8.8")
    result = reader.get("8.8.8.8")
    if result:
        print(
            f"   Country: {result.get('country', {}).get('names', {}).get('en', 'N/A')}"
        )
        print(f"   City: {result.get('city', {}).get('names', {}).get('en', 'N/A')}")
        print(f"   Latitude: {result.get('location', {}).get('latitude', 'N/A')}")
        print(f"   Longitude: {result.get('location', {}).get('longitude', 'N/A')}")
    else:
        print("   No data found for this IP")

    # Example 2: Lookup with ipaddress module objects
    print("\n2. Looking up IP address using ipaddress module: 1.1.1.1")
    import ipaddress

    ip = ipaddress.IPv4Address("1.1.1.1")
    result = reader.get(ip)
    if result:
        print(
            f"   Country: {result.get('country', {}).get('names', {}).get('en', 'N/A')}"
        )
        print(f"   City: {result.get('city', {}).get('names', {}).get('en', 'N/A')}")
    else:
        print("   No data found for this IP")

    # Example 3: Lookup with prefix length
    print("\n3. Looking up IP with prefix length: 208.67.222.222")
    result, prefix_len = reader.get_with_prefix_len("208.67.222.222")
    if result:
        print(
            f"   Country: {result.get('country', {}).get('names', {}).get('en', 'N/A')}"
        )
        print(f"   Network prefix length: /{prefix_len}")
    else:
        print("   No data found for this IP")

    # Example 4: Access database metadata
    print("\n4. Database metadata:")
    metadata = reader.metadata()
    print(f"   Database type: {metadata.database_type}")
    print(f"   Build date: {metadata.build_epoch}")
    print(f"   IP version: {metadata.ip_version}")
    print(f"   Node count: {metadata.node_count:,}")

    # Example 5: Check if database is closed
    print(f"\n5. Database closed status: {reader.closed}")

    # Close the database
    print("\n6. Closing database...")
    reader.close()
    print(f"   Database closed status: {reader.closed}")

    # Attempting to use a closed database will raise an error
    print("\n7. Attempting to query closed database...")
    try:
        reader.get("8.8.8.8")
    except ValueError as e:
        print(f"   Error (expected): {e}")

    print("\n" + "=" * 60)
    print("Example completed successfully!")


if __name__ == "__main__":
    main()
