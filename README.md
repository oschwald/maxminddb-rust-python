# maxminddb-rust

A Rust-backed Python module for reading MaxMind DB files.

It mirrors the public API of the official
[`maxminddb`](https://github.com/maxmind/MaxMind-DB-Reader-python) package and
keeps the same programming model with the `maxminddb_rust` import name.

## Performance

This project is intended to provide performance comparable to the `maxminddb` C
extension while keeping API compatibility.

Performance depends on the database, lookup pattern, and hardware. Run the
benchmark scripts in `benchmarks/` against your own databases to measure
expected throughput in your environment.

The reader is thread-safe and can be shared across threads. Lookup methods
create Python objects and hold the GIL while doing so, so CPU-bound lookups from
Python threads are still constrained by normal Python GIL behavior.

## Features

### API Compatibility

This package provides API compatibility with the official
[`maxminddb`](https://github.com/maxmind/MaxMind-DB-Reader-python) Python
module.

Supported:

- `Reader` class with `get()`, `get_with_prefix_len()`, `metadata()`, and
  `close()` methods
- `open_database()` function
- Context manager support (`with` statement)
- MODE\_\* constants (`MODE_AUTO`, `MODE_MMAP`, `MODE_MMAP_EXT`, `MODE_FILE`,
  `MODE_MEMORY`, and `MODE_FD`)
- `InvalidDatabaseError` exception
- `Metadata` class with all attributes and computed properties
- Support for string IP addresses and `ipaddress.IPv4Address`/`IPv6Address`
  objects
- `closed` attribute
- Iterator support (`__iter__`) for iterating all database records

Extensions (not in the original package):

- `get_many()` for batch lookups
- `get_path()` for retrieving a specific field from a record (for example,
  `('country', 'iso_code')`) without decoding the entire record
- `get_many_path()` for retrieving a specific field for many IP addresses

## Installation

### From PyPI

```bash
pip install maxminddb-rust
```

### From Source

```bash
git clone https://github.com/oschwald/maxminddb-rust-python.git
cd maxminddb-rust-python
uv run --with maturin maturin develop --release
```

## Usage

This module follows the same API as `maxminddb`, with a different import name:

```python
import ipaddress

import maxminddb_rust

# Open the database and release resources automatically when done.
with maxminddb_rust.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb") as reader:
    result = reader.get("8.8.8.8")
    print(result)

    result, prefix_len = reader.get_with_prefix_len("8.8.8.8")
    print(f"Result: {result}, Prefix: {prefix_len}")

    ip = ipaddress.IPv4Address("8.8.8.8")
    result = reader.get(ip)
    print(result)

    metadata = reader.metadata()
    print(f"Database type: {metadata.database_type}")
    print(f"Node count: {metadata.node_count}")
```

### Batch Lookup (Extension)

`get_many()` is an extension that is not available in the original `maxminddb`
module:

```python
import maxminddb_rust

with maxminddb_rust.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb") as reader:
    ips = ["8.8.8.8", "1.1.1.1", "208.67.222.222"]
    results = reader.get_many(ips)

    for ip, result in zip(ips, results):
        print(f"{ip}: {result}")
```

### Selective Field Lookup (Extension)

`get_path()` retrieves a specific field from a record:

```python
import maxminddb_rust

with maxminddb_rust.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb") as reader:
    # Retrieve a specific field without decoding the entire record.
    # Path elements can be strings (map keys) or integers (array indices).
    iso_code = reader.get_path("8.8.8.8", ("country", "iso_code"))
    print(f"ISO Code: {iso_code}")

    subdivision = reader.get_path("8.8.8.8", ("subdivisions", 0, "iso_code"))
    print(f"Subdivision: {subdivision}")

    ips = ["8.8.8.8", "1.1.1.1", "208.67.222.222"]
    iso_codes = reader.get_many_path(ips, ("country", "iso_code"))
    print(iso_codes)
```

### Iterator Support

Iterate over all networks in the database:

```python
import maxminddb_rust

with maxminddb_rust.open_database(
    "/var/lib/GeoIP/GeoLite2-Country.mmdb"
) as reader:
    for network, data in reader:
        print(f"{network}: {data['country']['iso_code']}")
```

### Database Modes

Choose between memory-mapped files (default) and read-file modes:

```python
import maxminddb_rust

# MODE_AUTO: currently resolves to MODE_MMAP.
reader = maxminddb_rust.open_database(
    "/var/lib/GeoIP/GeoIP2-City.mmdb", mode=maxminddb_rust.MODE_AUTO
)

# MODE_MMAP: explicitly use memory-mapped files.
reader = maxminddb_rust.open_database(
    "/var/lib/GeoIP/GeoIP2-City.mmdb", mode=maxminddb_rust.MODE_MMAP
)

# MODE_MMAP_EXT: accepted for compatibility; same Rust mmap reader as MODE_MMAP.
reader = maxminddb_rust.open_database(
    "/var/lib/GeoIP/GeoIP2-City.mmdb", mode=maxminddb_rust.MODE_MMAP_EXT
)

# MODE_MEMORY: load the database file into memory.
reader = maxminddb_rust.open_database(
    "/var/lib/GeoIP/GeoIP2-City.mmdb", mode=maxminddb_rust.MODE_MEMORY
)

# MODE_FILE: compatibility mode that also reads the file into memory.
reader = maxminddb_rust.open_database(
    "/var/lib/GeoIP/GeoIP2-City.mmdb", mode=maxminddb_rust.MODE_FILE
)

# MODE_FD: read from a file-like object into memory.
with open("/var/lib/GeoIP/GeoIP2-City.mmdb", "rb") as database:
    reader = maxminddb_rust.open_database(database, mode=maxminddb_rust.MODE_FD)
```

`MODE_FD` follows the official package's pure Python behavior: pass a readable
binary object and the reader calls `read()` from its current position. Raw
integer OS file descriptors are not accepted directly.

`MODE_MMAP_EXT` is accepted for official-package compatibility. This package
does not have a separate C extension backend, so it uses the same Rust
memory-mapped reader as `MODE_MMAP`.

## Examples

The `examples/` directory contains complete working examples:

- **[basic_usage.py](examples/basic_usage.py)**: simple IP lookups, metadata
  access, and database lifecycle
- **[context_manager.py](examples/context_manager.py)**:
  using `with` for automatic cleanup
- **[iterator_demo.py](examples/iterator_demo.py)**:
  iterating over all networks in the database
- **[batch_processing.py](examples/batch_processing.py)**: batch lookups with
  `get_many()`

Run any example:

```bash
uv run python examples/basic_usage.py
uv run python examples/batch_processing.py
```

## Documentation

- **API documentation**: classes and methods include docstrings. Use `help()`:

  ```python
  import maxminddb_rust
  help(maxminddb_rust.open_database)
  help(maxminddb_rust.Reader.get)
  ```

- **Type hints**: full type stub file (`maxminddb_rust.pyi`) is included for
  IDE autocomplete and type checking
- **Changelog**: see [CHANGELOG.md](CHANGELOG.md) for version history and
  release notes
- **Migration Guide**: see [MIGRATION.md](MIGRATION.md) for migrating from the
  official `maxminddb` package

## Benchmarking

Benchmark scripts are consolidated in the `benchmarks/` directory.

Run the included benchmarks after building from source:

```bash
# Single lookup benchmark
uv run python benchmarks/benchmark.py --file /var/lib/GeoIP/GeoIP2-City.mmdb --count 250000

# Comprehensive benchmark across multiple databases
uv run python benchmarks/benchmark_comprehensive.py --count 250000

# Batch lookup benchmark
uv run python benchmarks/benchmark_batch.py --file /var/lib/GeoIP/GeoIP2-City.mmdb --batch-size 100

# Threaded lookup benchmark (shared Reader across Python threads, default DB set)
uv run python benchmarks/benchmark_parallel.py --count 500000 --workers 1,2,4,8

# get() vs get_path() benchmark
uv run python benchmarks/benchmark_path.py --file /var/lib/GeoIP/GeoLite2-City.mmdb --count 250000

# Compare benchmark throughput between two git refs
uv run python benchmarks/compare_refs.py --baseline-ref origin/main --candidate-ref HEAD

# CI-friendly comparison with JSON output and a 5% regression threshold
uv run python benchmarks/compare_refs.py --json-output bench.json --max-regression-pct 5

# Path cache profiling: cached tuple, new tuple per call, list path per call
uv run python benchmarks/compare_refs.py --case get_path --case get_path_new_tuple --case get_path_list
```

## Testing

This project includes Python tests, Rust unit tests, and adapted upstream
compatibility tests from
[MaxMind-DB-Reader-python](https://github.com/maxmind/MaxMind-DB-Reader-python).

```bash
# Initialize test data submodule (first time only)
git submodule update --init --recursive

# Run Python tests.
uv run pytest

# Run Rust unit tests.
cargo test --all-targets --all-features

# Run configured linters and formatters.
uv run precious lint .
```

For upstream test compatibility and syncing instructions, see
[tests/maxmind/README.md](tests/maxmind/README.md).

## License

ISC License. See [LICENSE](LICENSE) for details.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, code quality
guidelines, and pull request procedures.
