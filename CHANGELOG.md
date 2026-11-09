# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added Python 3.15 support, with wheels for the standard and free-threaded
  builds. CI now tests 3.15 and free-threaded 3.15.
- Releases now include free-threaded wheels for Windows ARM64.

### Changed

- Updated PyO3 from 0.29 to 0.30. Calls to `open_database` are faster on
  CPython 3.11 and later. The garbage collector now traverses the extension's
  class objects, which fixes a possible memory leak of those classes.
- Updated Rust and Python dependencies, GitHub Actions, the Pyodide build, and
  the MaxMind-DB test submodule. The CodeQL action is now pinned to a commit
  SHA like the other actions.

### Removed

- Dropped support for Python 3.8, which reached end of life in October 2024.
  PyO3 0.30 no longer supports it. Python 3.9 or later is now required.

## [0.8.0] - 2026-09-12

### Changed

- Updated the `maxminddb` crate from 0.30.0 to 0.32.0, adding decoding resource
  limits, fixing overflowing extended data types, and improving record decoding,
  search-tree lookups, and selective path lookups.
- Databases that exceed the new decoding limits now raise `InvalidDatabaseError`,
  including databases that previously decoded successfully.
- Updated Rust and Python dependencies and the MaxMind-DB test submodule.
- Dependabot now covers all Cargo and Python dependency types and test-submodule
  updates, with seven-day cooldowns across all ecosystems.
- Synced applicable reader tests from the official Python reader's 3.2.0 release,
  including bounded payload-amplification and pointer fan-out tests.

## [0.7.0] - 2026-07-19

### Changed

- Updated the `maxminddb` crate to 0.30.0.
- Benchmarks now default to commonly installed databases under
  `/var/lib/GeoIP`, while retaining `--file` overrides for custom paths.
- CI now tests every declared CPython version from 3.8 through 3.14, plus the
  free-threaded 3.14 build, and releases include a free-threaded 3.14 wheel.
- The threaded benchmark now gives each worker a private input chunk, avoiding
  shared-list contention on free-threaded Python.

### Performance

- Record strings are validated while constructing Python strings, avoiding a
  duplicate UTF-8 validation pass through an intermediate Rust string.
- Ref-comparison benchmarks can now generate successful lookups from database
  contents, and CI uses this workload to exercise record decoding.
- Lookups of `ipaddress.IPv4Address` and `ipaddress.IPv6Address` objects avoid
  allocating temporary packed byte strings.
- Equivalent immutable tuple paths now reuse parsed path-cache entries even
  when callers construct a new tuple for each lookup.
- Memory-mode database opening uses the standard preallocated whole-file read
  path.

### Fixed

- Database paths preserve platform filesystem encoding, including
  surrogate-escaped Unix filenames, and exceptions from `__fspath__` are no
  longer replaced with a generic argument error.

## [0.6.0] - 2026-06-14

### Added

- Added `MODE_FILE` and `MODE_FD` support. `MODE_FD` accepts readable binary
  file-like objects and reads from their current position.
- Added `Reader.get_many_path()` for batched lookups of a specific record path.
- Added a `benchmarks/compare_refs.py` helper and manual GitHub Actions
  workflow for comparing benchmark results between git refs.
- Added reader concurrency stress tests.
- Added release workflow support for CPython 3.14 Pyodide/WebAssembly wheels
  using the `pyemscripten_2026_0_wasm32` platform tag.

### Changed

- Updated dependencies, including PyO3 0.29.0 and maxminddb 0.28.1.
- Removed the now-unnecessary PyO3 `generate-import-lib` feature; PyO3 0.29
  uses raw-dylib linking on Windows.
- `Reader.get_many()` now accepts any iterable of IP addresses instead of
  requiring a pre-built sequence, while preserving output order.
- Expanded type hints for decoded record values and IP address inputs.
- Runtime classes and exceptions now report the public `maxminddb_rust` module.
- Refreshed migration and benchmark guidance to avoid relying on stale
  throughput claims.

### Fixed

- Reworked reader iteration internals to avoid an unsafe lifetime transmute.
- Tightened path parsing so invalid path element types, including `bool`, are
  rejected consistently.
- Report unsupported open modes before validating the database argument.

### Performance

- Added path-cache and iterator cases to the ref-comparison benchmarks, with
  configurable regression thresholds and JSON output.
- Pre-generated benchmark input IP addresses to reduce benchmark harness
  overhead.
- Reduced `get_many_path()` overhead by converting parsed lookup paths to
  borrowed `PathElement` slices once per call instead of once per IP lookup.
- Added a strict fast path for IPv4 string parsing to reduce per-lookup input
  parsing overhead.
- Reduced batch lookup iteration overhead by fast-pathing list and tuple inputs
  for `get_many()` and `get_many_path()`.
- Reduced lookup synchronization overhead by replacing the hot-path
  `RwLock<Option<Arc<_>>>` reader access with `ArcSwapOption` and borrowing the
  loaded reader for single-lookups instead of cloning an `Arc` every time.

## [0.5.0] - 2026-04-25

### Performance

- Upgraded `maxminddb` crate to 0.28.0, which includes several performance
  improvements.

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

[Unreleased]: https://github.com/oschwald/maxminddb-rust-python/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/oschwald/maxminddb-rust-python/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/oschwald/maxminddb-rust-python/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/oschwald/maxminddb-rust-python/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/oschwald/maxminddb-rust-python/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/oschwald/maxminddb-rust-python/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/oschwald/maxminddb-rust-python/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/oschwald/maxminddb-rust-python/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/oschwald/maxminddb-rust-python/releases/tag/v0.1.0
