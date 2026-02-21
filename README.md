# maxminddb-rust

A Rust-backed Python module for MaxMind DB files.

It is API-compatible with the official
[`maxminddb`](https://github.com/maxmind/MaxMind-DB-Reader-python) package and
keeps the same programming model.

## Performance

This project is intended to be in the same performance class as the
`maxminddb` C extension while keeping full compatibility.

Performance depends on the database, lookup pattern, and hardware. Run the
benchmark scripts in `benchmarks/` against your own databases to measure
expected throughput in your environment.

The reader is thread-safe and can be shared across threads for parallel
lookups.

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
- MODE\_\* constants (`MODE_AUTO`, `MODE_MMAP`, etc.)
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

Not yet implemented:

- `MODE_FILE`
- File descriptor support in constructor

## Installation

### From PyPI

```bash
pip install maxminddb-rust
```

### From Source

```bash
maturin develop --release
```

## Usage

This module follows the same API as `maxminddb`, with a different import name:

```python
import maxminddb_rust

# Open database
reader = maxminddb_rust.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb")

# Lookup single IP
result = reader.get("8.8.8.8")
print(result)

# Lookup with prefix length
result, prefix_len = reader.get_with_prefix_len("8.8.8.8")
print(f"Result: {result}, Prefix: {prefix_len}")

# Use with ipaddress objects
import ipaddress
ip = ipaddress.IPv4Address("8.8.8.8")
result = reader.get(ip)

# Access metadata
metadata = reader.metadata()
print(f"Database type: {metadata.database_type}")
print(f"Node count: {metadata.node_count}")

# Context manager support
with maxminddb_rust.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb") as reader:
    result = reader.get("1.1.1.1")
    print(result)
```

### Batch Lookup (Extension)

`get_many()` is an extension that is not available in the original `maxminddb`
module:

```python
import maxminddb_rust

reader = maxminddb_rust.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb")

# Lookup multiple IPs at once
ips = ["8.8.8.8", "1.1.1.1", "208.67.222.222"]
results = reader.get_many(ips)

for ip, result in zip(ips, results):
    print(f"{ip}: {result}")
```

### Selective Field Lookup (Extension)

`get_path()` retrieves a specific field from a record:

```python
import maxminddb_rust

reader = maxminddb_rust.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb")

# Retrieve specific field without decoding the entire record
# Path elements can be strings (map keys) or integers (array indices)
iso_code = reader.get_path("8.8.8.8", ("country", "iso_code"))
print(f"ISO Code: {iso_code}")

# Accessing arrays by index
# e.g., reader.get_path("8.8.8.8", ("subdivisions", 0, "iso_code"))
```

### Iterator Support

Iterate over all networks in the database:

```python
import maxminddb_rust

reader = maxminddb_rust.open_database("/var/lib/GeoIP/GeoLite2-Country.mmdb")

# Iterate over all networks in the database
for network, data in reader:
    print(f"{network}: {data['country']['iso_code']}")
```

### Database Modes

Choose between memory-mapped files (default) and in-memory mode:

```python
import maxminddb_rust

# MODE_AUTO: Uses memory-mapped files (default)
reader = maxminddb_rust.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb", mode=maxminddb_rust.MODE_AUTO)

# MODE_MMAP: Explicitly use memory-mapped files
reader = maxminddb_rust.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb", mode=maxminddb_rust.MODE_MMAP)

# MODE_MEMORY: Load entire database into memory
reader = maxminddb_rust.open_database(
    "/var/lib/GeoIP/GeoIP2-City.mmdb", mode=maxminddb_rust.MODE_MEMORY
)
```

## Examples

The `examples/` directory contains complete working examples:

- **[basic_usage.py](https://github.com/oschwald/maxminddb-rust-python/blob/main/examples/basic_usage.py)**:
  simple IP lookups, metadata access, and database lifecycle
- **[context_manager.py](https://github.com/oschwald/maxminddb-rust-python/blob/main/examples/context_manager.py)**:
  using `with` for automatic cleanup
- **[iterator_demo.py](https://github.com/oschwald/maxminddb-rust-python/blob/main/examples/iterator_demo.py)**:
  iterating over all networks in the database
- **[batch_processing.py](https://github.com/oschwald/maxminddb-rust-python/blob/main/examples/batch_processing.py)**:
  batch lookups with `get_many()`

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
- **Changelog**: See [CHANGELOG.md](https://github.com/oschwald/maxminddb-rust-python/blob/main/CHANGELOG.md) for version history and release notes
- **Migration Guide**: See [MIGRATION.md](https://github.com/oschwald/maxminddb-rust-python/blob/main/MIGRATION.md) for migrating from the official `maxminddb` package

## Benchmarking

Benchmark scripts are consolidated in the `benchmarks/` directory.

Run the included benchmarks (after building from source):

```bash
# Single lookup benchmark
uv run python benchmarks/benchmark.py --file /var/lib/GeoIP/GeoIP2-City.mmdb --count 250000

# Comprehensive benchmark across multiple databases
uv run python benchmarks/benchmark_comprehensive.py --count 250000

# Batch lookup benchmark
uv run python benchmarks/benchmark_batch.py --file /var/lib/GeoIP/GeoIP2-City.mmdb --batch-size 100

# Parallel lookup benchmark (shared Reader across threads, default DB set)
uv run python benchmarks/benchmark_parallel.py --count 500000 --workers 1,2,4,8

# get() vs get_path() benchmark
uv run python benchmarks/benchmark_path.py --file /var/lib/GeoIP/GeoLite2-City.mmdb --count 250000
```

## Testing

This project includes comprehensive tests, including upstream compatibility tests from MaxMind-DB-Reader-python.

```bash
# Initialize test data submodule (first time only)
git submodule update --init --recursive

# Run all tests
uv run pytest

# Run with verbose output
uv run pytest -v
```

For contributor information including development setup, code quality tools, and test syncing, see [CONTRIBUTING.md](https://github.com/oschwald/maxminddb-rust-python/blob/main/CONTRIBUTING.md).

For upstream test compatibility and syncing instructions, see [tests/maxmind/README.md](https://github.com/oschwald/maxminddb-rust-python/blob/main/tests/maxmind/README.md).

## License

ISC License - see LICENSE file for details.

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](https://github.com/oschwald/maxminddb-rust-python/blob/main/CONTRIBUTING.md) for development setup, code quality guidelines, and pull request procedures.
