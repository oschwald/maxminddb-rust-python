# maxminddb-rust

A high-performance Rust-based Python module for MaxMind DB files. Provides 100% API compatibility with the official [`maxminddb`](https://github.com/maxmind/MaxMind-DB-Reader-python) module with significantly better performance.

## Performance

Benchmark results (250,000 lookups with random IPs):

| Database         | Size  | Lookups/sec |
| ---------------- | ----- | ----------- |
| GeoLite2-Country | 9.4MB | 492,768     |
| GeoLite2-City    | 61MB  | 318,882     |
| GeoIP2-City      | 117MB | 308,254     |

**Average: 373,301 lookups/second**

### Optimizations Implemented

- **Memory-mapped files**: Uses `mmap` for efficient file I/O instead of loading entire database into memory
- **GIL management**: Releases Python GIL during IP parsing and database lookups for better concurrency
- **Link-time optimization**: Aggressive compiler optimizations (thin LTO, single codegen unit)
- **Zero-copy operations**: Minimal data copying between Rust and Python
- **Batch lookups**: `get_many()` method for processing multiple IPs efficiently

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

**Not Yet Implemented:**

- ⏸️ MODE_FILE mode (currently only MODE_AUTO, MODE_MMAP, and MODE_MEMORY supported)
- ⏸️ File descriptor support in constructor

## Installation

### From PyPI (when published)

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

# MODE_MEMORY: Load entire database into memory (useful for embedded systems or when file handle limits are a concern)
reader = maxminddb_rust.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb", mode=maxminddb_rust.MODE_MEMORY)
```

## Examples

The `examples/` directory contains complete working examples demonstrating various use cases:

- **[basic_usage.py](examples/basic_usage.py)** - Simple IP lookups, metadata access, and database lifecycle
- **[context_manager.py](examples/context_manager.py)** - Using `with` statement for automatic resource cleanup
- **[iterator_demo.py](examples/iterator_demo.py)** - Iterating over all networks in the database
- **[batch_processing.py](examples/batch_processing.py)** - High-performance batch lookups with `get_many()`

Run any example:

```bash
.venv/bin/python examples/basic_usage.py
.venv/bin/python examples/batch_processing.py
```

## Documentation

- **API Documentation**: All classes and methods include comprehensive docstrings. Use Python's built-in `help()`:
  ```python
  import maxminddb_rust
  help(maxminddb_rust.open_database)
  help(maxminddb_rust.Reader.get)
  ```
- **Type Hints**: Full type stub file (`maxminddb_rust.pyi`) included for IDE autocomplete and type checking
- **Changelog**: See [CHANGELOG.md](CHANGELOG.md) for version history and release notes
- **Migration Guide**: See [MIGRATION.md](MIGRATION.md) for migrating from the official `maxminddb` package

## Benchmarking

Run the included benchmarks (after building from source):

```bash
# Single lookup benchmark
.venv/bin/python benchmark.py --file /var/lib/GeoIP/GeoIP2-City.mmdb --count 250000

# Comprehensive benchmark across multiple databases
.venv/bin/python benchmark_comprehensive.py --count 250000

# Batch lookup benchmark
.venv/bin/python benchmark_batch.py --file /var/lib/GeoIP/GeoIP2-City.mmdb --batch-size 100
```

## Testing

### Running Tests

This project includes upstream compatibility tests from MaxMind-DB-Reader-python to ensure API compatibility.

```bash
# Initialize test data submodule (first time only)
git submodule update --init --recursive

# Install test dependencies
uv pip install pytest

# Run all tests
uv run pytest

# Run only upstream compatibility tests
uv run pytest tests/maxmind/
```

### Upstream Test License

The tests in `tests/maxmind/` are copyright MaxMind, Inc. and licensed under Apache License 2.0. See `tests/maxmind/UPSTREAM_LICENSE` for full license text.

The test data in `tests/data/` is from the [MaxMind-DB repository](https://github.com/maxmind/MaxMind-DB) and is licensed under Creative Commons Attribution-ShareAlike 3.0.

### Syncing Upstream Tests

To sync with latest upstream tests from MaxMind-DB-Reader-python:

```bash
# Update local copy of upstream repo
cd /path/to/MaxMind-DB-Reader-python
git pull

# Copy updated test file
cp tests/reader_test.py /path/to/maxminddb-pyo3/tests/maxmind/

# Re-apply required adaptations:
# 1. Add copyright header
# 2. Update imports: from maxminddb.const import (...) → from maxminddb import (...)
# 3. Update imports: from maxminddb.reader import Reader → from maxminddb import Reader
# 4. Add: import pytest
# 5. Add @pytest.mark.skip to TestFileReader and TestFDReader
# 6. Replace maxminddb.reader.Reader with maxminddb.Reader
# 7. Replace maxminddb.extension.Reader with maxminddb.Reader
# 8. Update test data paths: tests/data/ → tests/data/test-data/

# Update test data submodule
cd /path/to/maxminddb-pyo3
git submodule update --remote tests/data
```

## Development

### Code Quality Tools

This project uses [precious](https://github.com/houseabsolute/precious/) to manage linters and formatters. Install precious and run:

```bash
# Lint all files
precious lint --all

# Format all files
precious tidy --all

# Lint specific files
precious lint path/to/file.py

# Run a specific linter
precious lint --all --command rustfmt
```

Individual tools can also be run directly:

```bash
# Rust
cargo clippy --lib --all-features -- -D warnings
cargo fmt --all

# Python
ruff check .
ruff format .

# Markdown/YAML
prettier --check "**/*.md" "**/*.yml"
prettier --write "**/*.md" "**/*.yml"
```

### Continuous Integration

GitHub Actions workflows automatically run:

- **Tests** on Python 3.9-3.13 across Linux, macOS, and Windows
- **Linters** including clippy, rustfmt, ruff, and prettier
- **Security scans** with CodeQL and zizmor
- **Dependency updates** via Dependabot

## License

ISC License - see LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
