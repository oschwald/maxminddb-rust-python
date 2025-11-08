# maxminddb-rust

A Rust-based Python binding for MaxMind DB files, providing a drop-in replacement for the [`maxminddb`](https://github.com/maxmind/MaxMind-DB-Reader-python) module with API compatibility.

## Performance

Benchmark results (250,000 lookups with random IPs):

| Database              | Size  | Lookups/sec |
|-----------------------|-------|-------------|
| GeoLite2-Country      | 9.6MB | 347,214     |
| GeoLite2-City         | 61MB  | 214,506     |
| GeoIP2-City           | 117MB | 210,746     |

**Average: 257,489 lookups/second**

### Optimizations Implemented

- **Memory-mapped files**: Uses `mmap` for efficient file I/O instead of loading entire database into memory
- **GIL management**: Releases Python GIL during IP parsing and database lookups for better concurrency
- **Link-time optimization**: Aggressive compiler optimizations (LTO, single codegen unit)
- **Zero-copy operations**: Minimal data copying between Rust and Python
- **Batch lookups**: `get_many()` method for processing multiple IPs efficiently

## Features

### API Compatibility

This package provides a **drop-in replacement** for the [`maxminddb`](https://github.com/maxmind/MaxMind-DB-Reader-python) Python module with the following API:

**Supported:**
- ✅ `Reader` class with `get()`, `get_with_prefix_len()`, `metadata()`, and `close()` methods
- ✅ `open_database()` function
- ✅ Context manager support (`with` statement)
- ✅ MODE_* constants (MODE_AUTO, MODE_MMAP, etc.)
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

This module can be used as a **drop-in replacement** for `maxminddb`. After installing `maxminddb-rust`, the import works the same:

```python
import maxminddb  # This now uses the Rust implementation!

# Open database
reader = maxminddb.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb")

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
with maxminddb.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb") as reader:
    result = reader.get("1.1.1.1")
    print(result)
```

### Batch Lookup (Extension)

The `get_many()` method is an extension not available in the original `maxminddb` module:

```python
import maxminddb

reader = maxminddb.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb")

# Lookup multiple IPs at once
ips = ["8.8.8.8", "1.1.1.1", "208.67.222.222"]
results = reader.get_many(ips)

for ip, result in zip(ips, results):
    print(f"{ip}: {result}")
```

### Iterator Support

Iterate over all networks in the database:

```python
import maxminddb

reader = maxminddb.open_database("/var/lib/GeoIP/GeoLite2-Country.mmdb")

# Iterate over all networks in the database
for network, data in reader:
    print(f"{network}: {data['country']['iso_code']}")
```

### Database Modes

Choose between memory-mapped files (default, best performance) and in-memory mode:

```python
import maxminddb

# MODE_AUTO: Uses memory-mapped files (default, fastest)
reader = maxminddb.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb", mode=maxminddb.MODE_AUTO)

# MODE_MMAP: Explicitly use memory-mapped files
reader = maxminddb.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb", mode=maxminddb.MODE_MMAP)

# MODE_MEMORY: Load entire database into memory (useful for embedded systems or when file handle limits are a concern)
reader = maxminddb.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb", mode=maxminddb.MODE_MEMORY)
```

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

## License

ISC License - see LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
