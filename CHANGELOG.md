# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Comprehensive Python API docstrings for all public methods and classes
- Type stub file (`maxminddb.pyi`) for IDE autocomplete and type checking support
- `py.typed` marker file to indicate type hint support
- Example scripts demonstrating common usage patterns
- This CHANGELOG file to track project history

### Changed
- Performance improvements: 45% average speedup across all database types
  - GeoLite2-Country: 347K → 493K lookups/sec (+42%)
  - GeoLite2-City: 215K → 319K lookups/sec (+49%)
  - GeoIP2-City: 211K → 308K lookups/sec (+46%)

### Performance Optimizations
- Avoided cloning map keys during deserialization
- Optimized Python object deserialization with reduced allocations
- Optimized lookup performance with caching
- Reduced parsing overhead in get() lookups
- Reduced reader lock contention with RwLock

## [0.1.0] - Initial Release

### Added
- Drop-in replacement for the `maxminddb` Python module
- Full API compatibility with original maxminddb package
  - `Reader` class with `get()`, `get_with_prefix_len()`, `metadata()`, and `close()` methods
  - `open_database()` function
  - Context manager support (`with` statement)
  - MODE_* constants (MODE_AUTO, MODE_MMAP, MODE_MMAP_EXT, MODE_MEMORY)
  - `InvalidDatabaseError` exception
  - `Metadata` class with all attributes and computed properties
  - Support for string IP addresses and `ipaddress.IPv4Address`/`IPv6Address` objects
  - `closed` attribute
  - Iterator support for iterating over all database records
- Extension method `get_many()` for efficient batch IP lookups
- Memory-mapped file I/O for optimal performance
- GIL release during lookups for better concurrency
- Thin LTO and aggressive compiler optimizations
- Support for Python 3.8+
- Comprehensive test suite with upstream compatibility tests

### Supported
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

### Performance
- Average: 257K lookups/second (initial release, before optimizations)
- Memory-efficient with memory-mapped files
- Zero-copy operations where possible
- Batch processing support with `get_many()`

[Unreleased]: https://github.com/oschwald/maxminddb-pyo3/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/oschwald/maxminddb-pyo3/releases/tag/v0.1.0
