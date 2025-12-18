# maxminddb-rust

A high-performance Rust-based Python module for MaxMind DB files. Provides 100% API compatibility with the official [`maxminddb`](https://github.com/maxmind/MaxMind-DB-Reader-python) module with similar performance.

## Performance

Benchmark results using 10 million random IP lookups per database (single-threaded):

| Database         | Size  | Lookups/sec |
| ---------------- | ----- | ----------- |
| GeoLite2-Country | 9.4MB | 527,297     |
| GeoLite2-City    | 61MB  | 338,879     |
| GeoIP2-City      | 117MB | 332,827     |

**Test Environment:** Intel Core Ultra 7 265K (20 cores, up to 6.5GHz), Linux 6.17

These are single-threaded results; the reader is fully thread-safe and can be shared across multiple threads for parallel lookups.

## Features

### API Compatibility

This package provides **100% API compatibility** with the official [`maxminddb`](https://github.com/maxmind/MaxMind-DB-Reader-python) Python module:

**Supported:**

- ✅ `Reader` class with `get()`, `get_with_prefix_len()`, `metadata()`, and `close()` methods
- ✅ `open_database()` function
- ✅ Context manager support (`with` statement)
- ✅ MODE\_\* constants (MODE_AUTO, MODE_MMAP, etc.)
- ✅ `InvalidDatabaseError` exception
- ✅ `Metadata` class with all attributes and computed properties
- ✅ Support for string IP addresses and `ipaddress.IPv4Address`/`IPv6Address` objects
- ✅ `closed` attribute
- ✅ Iterator support (`__iter__`) for iterating over all database records

**Extensions (not in original):**

- ⭐ `get_many()` - Batch lookup method for processing multiple IPs efficiently
- ⭐ `get_path()` - Efficiently retrieve a specific field from a record (e.g.,
  `('country', 'iso_code')`) without decoding the entire record. This can be
  over 6x faster than `get()` when only partial data is needed.

**Not Yet Implemented:**

- ⏸️ MODE_FILE mode (currently only MODE_AUTO, MODE_MMAP, and MODE_MEMORY supported)
- ⏸️ File descriptor support in constructor

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

This module provides the same API as `maxminddb`, just with a different import name:

```python
import maxminddb_rust  # High-performance Rust implementation

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

The `get_many()` method is an extension not available in the original `maxminddb` module:

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

The `get_path()` method allows you to efficiently retrieve a specific field from a record:

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

Choose between memory-mapped files (default, best performance) and in-memory mode:

```python
import maxminddb_rust

# MODE_AUTO: Uses memory-mapped files (default, fastest)
reader = maxminddb_rust.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb", mode=maxminddb_rust.MODE_AUTO)

# MODE_MMAP: Explicitly use memory-mapped files
reader = maxminddb_rust.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb", mode=maxminddb_rust.MODE_MMAP)

# MODE_MEMORY: Load entire database into memory (useful for embedded systems
# or when file handle limits are a concern)
reader = maxminddb_rust.open_database(
    "/var/lib/GeoIP/GeoIP2-City.mmdb", mode=maxminddb_rust.MODE_MEMORY
)
```

## Examples

The `examples/` directory contains complete working examples demonstrating various use cases:

- **[basic_usage.py](https://github.com/oschwald/maxminddb-rust-python/blob/main/examples/basic_usage.py)** - Simple IP lookups, metadata access, and database lifecycle
- **[context_manager.py](https://github.com/oschwald/maxminddb-rust-python/blob/main/examples/context_manager.py)** - Using `with` statement for automatic resource cleanup
- **[iterator_demo.py](https://github.com/oschwald/maxminddb-rust-python/blob/main/examples/iterator_demo.py)** - Iterating over all networks in the database
- **[batch_processing.py](https://github.com/oschwald/maxminddb-rust-python/blob/main/examples/batch_processing.py)** - High-performance batch lookups with `get_many()`
- **[benchmark_path.py](https://github.com/oschwald/maxminddb-rust-python/blob/main/examples/benchmark_path.py)**
  - Performance comparison between `get()` and `get_path()`

Run any example:

```bash
uv run python examples/basic_usage.py
uv run python examples/batch_processing.py
```

## Documentation

- **API Documentation**: All classes and methods include comprehensive docstrings. Use Python's built-in `help()`:
  ```python
  import maxminddb_rust
  help(maxminddb_rust.open_database)
  help(maxminddb_rust.Reader.get)
  ```
- **Type Hints**: Full type stub file (`maxminddb_rust.pyi`) included for IDE autocomplete and type checking
- **Changelog**: See [CHANGELOG.md](https://github.com/oschwald/maxminddb-rust-python/blob/main/CHANGELOG.md) for version history and release notes
- **Migration Guide**: See [MIGRATION.md](https://github.com/oschwald/maxminddb-rust-python/blob/main/MIGRATION.md) for migrating from the official `maxminddb` package

## Benchmarking

Run the included benchmarks (after building from source):

```bash
# Single lookup benchmark
uv run python benchmark.py --file /var/lib/GeoIP/GeoIP2-City.mmdb --count 250000

# Comprehensive benchmark across multiple databases
uv run python benchmark_comprehensive.py --count 250000

# Batch lookup benchmark
uv run python benchmark_batch.py --file /var/lib/GeoIP/GeoIP2-City.mmdb --batch-size 100
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
