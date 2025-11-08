#!/usr/bin/env python3
"""
Iterator usage example for maxminddb_rust module.

Demonstrates how to iterate over all networks in a MaxMind DB database.
This is useful for exporting data, analyzing database contents, or
performing bulk operations.
"""

import maxminddb_rust
from collections import Counter

# Path to your MaxMind database file
# Note: Using Country database for faster iteration (smaller than City databases)
DATABASE_PATH = "/var/lib/GeoIP/GeoLite2-Country.mmdb"


def basic_iteration():
    """Basic iteration over database networks."""
    print("\n1. Basic iteration - first 10 networks")
    print("-" * 60)

    with maxminddb_rust.open_database(DATABASE_PATH) as reader:
        count = 0
        for network, data in reader:
            country_code = data.get("country", {}).get("iso_code", "N/A")
            country_name = data.get("country", {}).get("names", {}).get("en", "N/A")
            print(f"   {str(network):20s} -> {country_code} ({country_name})")

            count += 1
            if count >= 10:
                break


def count_networks_by_country():
    """Count how many networks belong to each country."""
    print("\n2. Count networks by country (top 10)")
    print("-" * 60)

    with maxminddb_rust.open_database(DATABASE_PATH) as reader:
        country_counter = Counter()

        for network, data in reader:
            country_code = data.get("country", {}).get("iso_code", "Unknown")
            country_counter[country_code] += 1

        # Display top 10 countries by network count
        for country, count in country_counter.most_common(10):
            print(f"   {country:3s}: {count:,} networks")


def filter_networks():
    """Filter networks by specific criteria."""
    print("\n3. Filter networks - US networks only (first 10)")
    print("-" * 60)

    with maxminddb_rust.open_database(DATABASE_PATH) as reader:
        count = 0

        for network, data in reader:
            country_code = data.get("country", {}).get("iso_code")

            if country_code == "US":
                print(f"   {network}")
                count += 1

                if count >= 10:
                    break


def get_network_statistics():
    """Collect statistics about the database networks."""
    print("\n4. Network statistics")
    print("-" * 60)

    with maxminddb_rust.open_database(DATABASE_PATH) as reader:
        total_networks = 0
        ipv4_networks = 0
        ipv6_networks = 0
        continents = set()
        countries = set()

        for network, data in reader:
            total_networks += 1

            # Check IP version
            if network.version == 4:
                ipv4_networks += 1
            else:
                ipv6_networks += 1

            # Collect continent and country info
            if "continent" in data:
                continent_code = data["continent"].get("code")
                if continent_code:
                    continents.add(continent_code)

            if "country" in data:
                country_code = data["country"].get("iso_code")
                if country_code:
                    countries.add(country_code)

        print(f"   Total networks: {total_networks:,}")
        print(f"   IPv4 networks: {ipv4_networks:,}")
        print(f"   IPv6 networks: {ipv6_networks:,}")
        print(f"   Unique continents: {len(continents)}")
        print(f"   Unique countries: {len(countries)}")


def export_to_csv():
    """Export database contents to CSV format (first 20 entries)."""
    print("\n5. Export to CSV format (first 20 entries)")
    print("-" * 60)

    import csv
    import sys

    with maxminddb_rust.open_database(DATABASE_PATH) as reader:
        # Create CSV writer
        writer = csv.writer(sys.stdout)
        writer.writerow(["Network", "Country Code", "Country Name", "Continent Code"])

        count = 0
        for network, data in reader:
            country_code = data.get("country", {}).get("iso_code", "")
            country_name = data.get("country", {}).get("names", {}).get("en", "")
            continent_code = data.get("continent", {}).get("code", "")

            writer.writerow([str(network), country_code, country_name, continent_code])

            count += 1
            if count >= 20:
                break


def search_for_network():
    """Search for a specific IP address in the database by iterating."""
    print("\n6. Search for IP by iteration (8.8.8.8)")
    print("-" * 60)

    import ipaddress

    target_ip = ipaddress.IPv4Address("8.8.8.8")

    with maxminddb_rust.open_database(DATABASE_PATH) as reader:
        for network, data in reader:
            # Check if target IP is in this network
            if target_ip in network:
                country = data.get("country", {}).get("names", {}).get("en", "N/A")
                print(f"   Found in network: {network}")
                print(f"   Country: {country}")
                break
        else:
            print(f"   IP {target_ip} not found in database")


def main():
    """Run all iterator examples."""
    print("MaxMindDB Iterator Examples")
    print("=" * 60)

    basic_iteration()
    count_networks_by_country()
    filter_networks()
    get_network_statistics()
    export_to_csv()
    search_for_network()

    print("\n" + "=" * 60)
    print("All examples completed successfully!")


if __name__ == "__main__":
    main()
