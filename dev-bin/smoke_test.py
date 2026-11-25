"""Check that an installed maxminddb-rust build can look up an address.

The release workflow runs this after installing a built wheel or the sdist,
so a broken or mislabeled artifact fails before anything reaches PyPI.
"""

import argparse
import sys
import sysconfig

import maxminddb_rust

LOOKUP_IP = "81.2.69.160"
EXPECTED_ISO_CODE = "GB"


def main() -> None:
    """Open the test database, look up one address and check the result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", help="path to GeoIP2-City-Test.mmdb")
    parser.add_argument(
        "--free-threaded",
        action="store_true",
        help="fail unless this is a free-threaded build with the GIL disabled",
    )
    args = parser.parse_args()

    with maxminddb_rust.open_database(args.database) as reader:
        record = reader.get(LOOKUP_IP)

    iso_code = (record or {}).get("country", {}).get("iso_code")
    if iso_code != EXPECTED_ISO_CODE:
        sys.exit(f"{LOOKUP_IP} returned {iso_code!r}, expected {EXPECTED_ISO_CODE!r}")

    # A free-threaded interpreter re-enables the GIL when it imports an
    # extension that is not marked as supporting free threading.
    free_threaded = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    if args.free_threaded and not free_threaded:
        sys.exit("--free-threaded was given, but this Python is not free-threaded")
    if free_threaded and sys._is_gil_enabled():  # noqa: SLF001
        sys.exit("importing maxminddb_rust re-enabled the GIL")

    print(
        f"maxminddb_rust from {maxminddb_rust.__file__} on Python "
        f"{sys.version.split()[0]}: {LOOKUP_IP} -> {iso_code}",
    )


if __name__ == "__main__":
    main()
