# Python wrapper of maxminddb crate

A high-performance Python wrapper around the Rust maxminddb crate using PyO3.

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

## Installation

```bash
maturin develop --release
```

## Usage

### Single Lookup

```python
import maxminddb_pyo3

# Open database
reader = maxminddb_pyo3.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb")

# Lookup single IP
result = reader.get("8.8.8.8")
print(result)

# Access metadata
metadata = reader.metadata()
```

### Batch Lookup

```python
import maxminddb_pyo3

reader = maxminddb_pyo3.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb")

# Lookup multiple IPs at once
ips = ["8.8.8.8", "1.1.1.1", "208.67.222.222"]
results = reader.get_many(ips)

for ip, result in zip(ips, results):
    print(f"{ip}: {result}")
```

## Benchmarking

Run the included benchmarks:

```bash
# Single lookup benchmark
python benchmark.py --file /var/lib/GeoIP/GeoIP2-City.mmdb --count 250000

# Comprehensive benchmark across multiple databases
python benchmark_comprehensive.py --count 250000

# Batch lookup benchmark
python benchmark_batch.py --file /var/lib/GeoIP/GeoIP2-City.mmdb --batch-size 100
```
