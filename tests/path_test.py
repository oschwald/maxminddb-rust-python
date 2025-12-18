import os
import pytest
import maxminddb_rust


def test_get_path():
    db_path = os.path.join(
        os.path.dirname(__file__), "data", "test-data", "GeoIP2-City-Test.mmdb"
    )
    with maxminddb_rust.open_database(db_path) as reader:
        ip = "81.2.69.142"

        # Verify full record first
        record = reader.get(ip)
        assert record["country"]["iso_code"] == "GB"
        assert record["city"]["names"]["en"] == "London"

        # Test get_path for various depths
        assert reader.get_path(ip, ("country", "iso_code")) == "GB"
        assert reader.get_path(ip, ("city", "names", "en")) == "London"
        assert (
            reader.get_path(ip, ("location", "latitude"))
            == record["location"]["latitude"]
        )

        # Test non-existent path
        assert reader.get_path(ip, ("non", "existent")) is None

        # Test non-existent IP
        assert reader.get_path("1.1.1.1", ("country", "iso_code")) is None

        # Test array indexing (if subdivisions exist)
        if "subdivisions" in record and len(record["subdivisions"]) > 0:
            assert (
                reader.get_path(ip, ("subdivisions", 0, "iso_code"))
                == record["subdivisions"][0]["iso_code"]
            )


def test_get_path_ipv6():
    db_path = os.path.join(
        os.path.dirname(__file__), "data", "test-data", "GeoIP2-City-Test.mmdb"
    )
    with maxminddb_rust.open_database(db_path) as reader:
        ip = "2001:2b8::"

        assert reader.get_path(ip, ("country", "iso_code")) == "KR"
        assert reader.get_path(ip, ("continent", "names", "en")) == "Asia"


def test_get_path_invalid_types():
    db_path = os.path.join(
        os.path.dirname(__file__), "data", "test-data", "GeoIP2-City-Test.mmdb"
    )
    with maxminddb_rust.open_database(db_path) as reader:
        ip = "81.2.69.142"

        # Invalid path element type (float)
        with pytest.raises(
            TypeError, match="Path elements must be strings or integers"
        ):
            reader.get_path(ip, ("country", 3.14))

        # Invalid path argument type (not a sequence)
        with pytest.raises(TypeError, match="Path must be a sequence"):
            reader.get_path(ip, "country")  # type: ignore


def test_get_path_closed_db():
    db_path = os.path.join(
        os.path.dirname(__file__), "data", "test-data", "GeoIP2-City-Test.mmdb"
    )
    reader = maxminddb_rust.open_database(db_path)
    reader.close()

    with pytest.raises(ValueError, match="closed"):
        reader.get_path("81.2.69.142", ("country", "iso_code"))


def test_get_path_mixed_invalid():
    db_path = os.path.join(
        os.path.dirname(__file__), "data", "test-data", "GeoIP2-City-Test.mmdb"
    )
    with maxminddb_rust.open_database(db_path) as reader:
        ip = "81.2.69.142"

        # Mixed valid and invalid types
        with pytest.raises(
            TypeError, match="Path elements must be strings or integers"
        ):
            reader.get_path(ip, ("country", 3.14, "iso_code"))
