from __future__ import annotations

from pathlib import Path

DEFAULT_DATABASE_DIR = Path("/var/lib/GeoIP")
PREFERRED_DATABASE_NAMES = (
    "GeoLite2-City.mmdb",
    "GeoIP2-City.mmdb",
    "GeoLite2-Country.mmdb",
    "GeoIP2-Country.mmdb",
)


def installed_databases() -> list[Path]:
    """Return installed databases in a stable, useful benchmark order."""
    preferred = [
        DEFAULT_DATABASE_DIR / name
        for name in PREFERRED_DATABASE_NAMES
        if (DEFAULT_DATABASE_DIR / name).is_file()
    ]
    remaining = sorted(
        path for path in DEFAULT_DATABASE_DIR.glob("*.mmdb") if path not in preferred
    )
    return [*preferred, *remaining]


def default_databases() -> list[Path]:
    """Return the usual installed benchmark set, falling back to any MMDB files."""
    databases = installed_databases()
    preferred_names = set(PREFERRED_DATABASE_NAMES)
    preferred = [path for path in databases if path.name in preferred_names]
    return preferred or databases


def resolve_database(path: str | None) -> Path:
    """Resolve an explicit database path or choose an installed default."""
    if path is not None:
        database = Path(path).expanduser()
        if not database.is_file():
            msg = f"MaxMind DB file does not exist: {database}"
            raise FileNotFoundError(msg)
        return database

    databases = default_databases()
    if databases:
        return databases[0]

    msg = (
        f"No MaxMind DB files found under {DEFAULT_DATABASE_DIR}. "
        "Install a database there or pass --file /path/to/database.mmdb."
    )
    raise FileNotFoundError(msg)


def chunks(values: list[str], workers: int) -> list[list[str]]:
    """Divide addresses into balanced, private lists for each worker."""
    base = len(values) // workers
    remainder = len(values) % workers
    result = []
    start = 0
    for i in range(workers):
        size = base + (1 if i < remainder else 0)
        end = start + size
        result.append(values[start:end])
        start = end
    return result
