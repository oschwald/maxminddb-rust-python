import maxminddb_rust


def test_public_types_report_public_module() -> None:
    assert maxminddb_rust.Reader.__module__ == "maxminddb_rust"
    assert maxminddb_rust.Metadata.__module__ == "maxminddb_rust"
    assert maxminddb_rust.InvalidDatabaseError.__module__ == "maxminddb_rust"
