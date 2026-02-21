# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-02-21

### Breaking

- `Reader.__iter__` now yields `(ipaddress.IPv4Network | ipaddress.IPv6Network, dict)`
  tuples instead of `(str, dict)`. Update callers that expect string networks
  to handle network objects directly, or convert with `str(network)`.

### Changed

- Consolidated benchmark scripts under `benchmarks/` and removed
  `examples/benchmark.py`.
- Updated benchmark documentation in `README.md` to match the consolidated
  benchmark layout.
- Simplified internal IP parsing and iterator code paths to reduce duplication
  and improve maintainability.
- Refactored database opening and reader initialization flow:
  - centralized open-mode parsing/validation
  - unified mmap vs memory reader loading
  - centralized metadata conversion
- Dependency updates:
  - upgraded `PyO3` from 0.27 to 0.28
  - upgraded `maxminddb` from 0.27.1 to 0.27.3
  - added `rustc-hash`

### Performance

- Improved `get()` and `get_with_prefix_len()` throughput by optimizing decode
  map-key caching:
  - switched to a faster key cache strategy for Python map keys
  - reduced key insertion overhead in the hot decode path
- Simplified lookup-path internals in performance-neutral ways to reduce
  complexity without regressions.

## [0.3.0] - 2025-12-18

### Added

- Added `get_path()` method to `Reader` class. This allows high-performance
  retrieval of specific fields (e.g.,
  `reader.get_path(ip, ('country', 'iso_code'))`) without decoding the entire
  record. This can be over 6x faster than `get()` when only partial data is
  needed.

### Changed

- Upgraded maxminddb crate from 0.27.0 to 0.27.1.
- Enabled `simdutf8` feature in `maxminddb` crate. This improves lookup
  performance by using SIMD instructions for faster UTF-8 validation.

## [0.2.0] - 2025-11-28

### Changed

- Upgraded maxminddb crate from 0.26.0 to 0.27.0. This brings internal
  improvements from the upstream Rust library with no breaking changes
  to the Python API.

## [0.1.1] - 2025-11-08

- Attempt at republishing with tweaked workflow.
- Minor doc updates.

## [0.1.0] - 2025-11-08

### Added

- High-performance Rust-based Python module for MaxMind DB files
- 100% API compatibility with the official `maxminddb` Python package
  - `Reader` class with `get()`, `get_with_prefix_len()`, `metadata()`, and `close()` methods
  - `open_database()` function
  - Context manager support (`with` statement)
  - MODE\_\* constants (MODE_AUTO, MODE_MMAP, MODE_MMAP_EXT, MODE_MEMORY)
  - `InvalidDatabaseError` exception
  - `Metadata` class with all attributes and computed properties
  - Support for string IP addresses and `ipaddress.IPv4Address`/`IPv6Address` objects
  - `closed` attribute
  - Iterator support for iterating over all database records
- Extension method `get_many()` for efficient batch IP lookups (not in official package)
- Comprehensive Python API docstrings for all public methods and classes
- Type stub file (`maxminddb_rust.pyi`) for IDE autocomplete and type checking support
- `py.typed` marker file for PEP 561 compliance
- Example scripts demonstrating common usage patterns:
  - basic_usage.py: Simple lookups and database lifecycle
  - context_manager.py: Using 'with' statement patterns
  - iterator_demo.py: Iterating over all database networks
  - batch_processing.py: High-performance batch lookups with get_many()
- Migration guide (MIGRATION.md) for users switching from the official maxminddb package
- Comprehensive test suite with upstream compatibility tests from MaxMind
- This CHANGELOG file

### Performance

- 45% faster average performance: 373K lookups/second vs official package
  - GeoLite2-Country: 493K lookups/sec
  - GeoLite2-City: 319K lookups/sec
  - GeoIP2-City: 308K lookups/sec
- Memory-mapped file I/O for optimal performance
- GIL release during lookups for better concurrency
- Thin LTO and aggressive compiler optimizations
- Zero-copy operations where possible
- Optimized Python object deserialization with reduced allocations
- Reduced reader lock contention with RwLock
- Batch processing support with `get_many()`

### Supported Modes

- MODE_AUTO: Automatically choose the best mode (uses MODE_MMAP)
- MODE_MMAP: Memory-mapped file I/O (default, best performance)
- MODE_MMAP_EXT: Same as MODE_MMAP
- MODE_MEMORY: Load entire database into memory

### Not Yet Implemented

- MODE_FILE: Read database as standard file
- MODE_FD: Load from file descriptor

### Dependencies

- PyO3 0.27.1: Python bindings for Rust
- maxminddb 0.26.0: MaxMind DB file format reader
- memmap2 0.9: Memory mapping
- ipnetwork 0.21: IP network types
- serde 1.0: Serialization framework

### Python Support

- Python 3.8+
- CPython implementation

[Unreleased]: https://github.com/oschwald/maxminddb-rust-python/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/oschwald/maxminddb-rust-python/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/oschwald/maxminddb-rust-python/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/oschwald/maxminddb-rust-python/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/oschwald/maxminddb-rust-python/releases/tag/v0.1.0
