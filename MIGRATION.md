# Migration Guide

This guide explains how to migrate from the official `maxminddb` package to `maxminddb-rust`.

## Why Migrate?

`maxminddb-rust` can offer significant performance improvements over the official `maxminddb` package:

- Benchmarkable Rust-backed lookup performance
- The same `Reader` API for typical use, with a few
  [known differences](#known-differences)
- All open modes supported
- Additional `get_many()`, `get_path()` and `get_many_path()` methods

## Quick Migration

For typical use, the only change required is updating your import statement:

### Before (official maxminddb)

```python
import maxminddb

reader = maxminddb.open_database("/path/to/GeoIP2-City.mmdb")
result = reader.get("8.8.8.8")
```

### After (maxminddb-rust)

```python
import maxminddb_rust

reader = maxminddb_rust.open_database("/path/to/GeoIP2-City.mmdb")
result = reader.get("8.8.8.8")
```

Code that uses the `Reader` API this way needs no other changes. Check the
[known differences](#known-differences) if your code imports the official
package's submodules, passes integer file descriptors or relies on its exact
error messages.

## Automated Migration

### Option 1: Find and Replace

Replace all occurrences in your codebase:

- `import maxminddb` → `import maxminddb_rust`
- `maxminddb.` → `maxminddb_rust.` (within your code)

### Option 2: Compatibility Alias (Temporary)

If you want to minimize changes initially, use an alias:

```python
import maxminddb_rust as maxminddb

# Rest of your code remains unchanged
reader = maxminddb.open_database("/path/to/GeoIP2-City.mmdb")
result = reader.get("8.8.8.8")
```

This allows gradual migration while maintaining compatibility.

### Option 3: Shell Script Migration

For Unix-like systems with GNU sed:

```bash
# Find all Python files using maxminddb
find . -name "*.py" -type f -exec grep -l "import maxminddb" {} \;

# Replace imports in place (review before running!). The alias keeps later
# uses such as maxminddb.open_database() and maxminddb.MODE_MEMORY working.
find . -name "*.py" -type f -exec sed -i 's/^\([[:space:]]*\)import maxminddb$/\1import maxminddb_rust as maxminddb/' {} \;
find . -name "*.py" -type f -exec sed -i 's/^\([[:space:]]*\)from maxminddb import /\1from maxminddb_rust import /' {} \;
```

Imports from the official package's submodules, such as `maxminddb.errors` or
`maxminddb.const`, need a manual change because `maxminddb_rust` has no
submodules. Import the same names from `maxminddb_rust` instead.

## Installation

### Uninstall Official Package (Optional)

If you want to completely switch:

```bash
pip uninstall maxminddb
pip install maxminddb-rust
```

### Install Alongside Official Package

You can keep both installed and choose which to use:

```bash
pip install maxminddb-rust
# Keep existing `maxminddb` package
```

Then explicitly choose in your code:

```python
import maxminddb_rust  # Use Rust implementation
import maxminddb  # Use official implementation (if still needed)
```

## API Compatibility

The compatibility tests in `tests/maxmind/` are adapted from the official
package's 3.3.0 test suite. The [known differences](#known-differences) below
are the places where this package deliberately behaves differently.

### Supported Features

These work the same way as in the official package:

- `Reader` class with `get()`, `get_with_prefix_len()`, `metadata()`,
  `close()` and the `closed` attribute
- `open_database()` function, with `str`, `bytes` and `os.PathLike` paths and
  binary file objects
- Context manager support (`with` statement)
- Iterator support (iterating over all networks)
- `Metadata` class and all properties
- The `Mode` enum and the `MODE_AUTO`, `MODE_MMAP`, `MODE_MMAP_EXT`,
  `MODE_FILE`, `MODE_MEMORY` and `MODE_FD` constants
- `InvalidDatabaseError` exception
- `__version__`
- String and ipaddress object support

### Extension Features

⭐ **Bonus features** in `maxminddb-rust`:

- `get_many()` - Batch IP lookup method
- `get_path()` - Retrieve one field of a record without decoding the whole
  record
- `get_many_path()` - Retrieve one field for many IP addresses

### Mode Notes

`MODE_FD` is supported for readable binary objects, matching the official package's pure Python reader behavior. It reads from the object's current position and implies `MODE_MEMORY`. Raw integer OS file descriptors are not accepted directly.

`MODE_AUTO` memory-maps a path and reads a binary file object into memory, as
the official package does. `MODE_MMAP_EXT` is accepted for compatibility with
the official package, but `maxminddb-rust` does not have a separate C extension
backend; it uses the same Rust memory-mapped reader as `MODE_MMAP`.

### Known Differences

These differences from the official `maxminddb` 3.3.0 package are deliberate:

- There are no `maxminddb.errors`, `maxminddb.const`, `maxminddb.types`,
  `maxminddb.reader`, `maxminddb.decoder` or `maxminddb.extension`
  submodules. Import `InvalidDatabaseError`, `Mode` and the `MODE_*` constants
  from `maxminddb_rust` itself.
- Integer OS file descriptors are not accepted as the database argument, in any
  mode. Pass a path or a binary file object instead.
- `Reader` has no reader to reinitialize: calling `__init__()` again does not
  reopen it, and `Reader.__new__()` needs a database.
- An iterator that is still running when the reader is closed finishes instead
  of raising `ValueError`.
- IPv4 addresses with leading zeros, such as `01.1.1.1`, raise `ValueError`.
  The official C extension accepts them; its pure Python reader rejects them.
- Error messages for corrupt databases differ, and some come from the Rust
  `maxminddb` crate. The exception types are the same.
- A database with a `build_epoch` of 0 opens without an error.
- A map key stored as bytes rather than as a string is decoded as a string
  instead of raising `InvalidDatabaseError`.
- When iterating a corrupt database whose search tree points back to its root,
  the iterator can return some networks before it raises
  `InvalidDatabaseError`.
- `__version__` is listed in `__all__`, because the package re-exports the
  extension module's `__all__`. So `from maxminddb_rust import *` also imports
  `__version__`.

## Example Migration

### Before: Web Application Using Official maxminddb

```python
# app.py
import maxminddb
from flask import Flask, request

app = Flask(__name__)
reader = maxminddb.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb")


@app.route("/lookup")
def lookup():
    ip = request.args.get("ip")
    result = reader.get(ip)
    return result
```

### After: Using maxminddb-rust

```python
# app.py
import maxminddb_rust
from flask import Flask, request

app = Flask(__name__)
reader = maxminddb_rust.open_database("/var/lib/GeoIP/GeoIP2-City.mmdb")


@app.route("/lookup")
def lookup():
    ip = request.args.get("ip")
    result = reader.get(ip)
    return result
```

**Only 1 line changed!**

## Performance Comparison

Performance depends on the database, lookup pattern, Python version, and hardware. Run the benchmarks against your own workload before relying on a specific throughput number:

```bash
uv run python benchmarks/benchmark.py --file /path/to/GeoIP2-City.mmdb --count 250000
uv run python benchmarks/benchmark_batch.py --file /path/to/GeoIP2-City.mmdb --batch-size 100
uv run python benchmarks/compare_refs.py --baseline-ref origin/main --candidate-ref HEAD
```

The benchmarks default to databases installed under `/var/lib/GeoIP`; use
`--file` to select another database.

`get_many()` is an extension that the official package does not provide, so compare it against your current loop of `get()` calls when evaluating a migration.

## Troubleshooting

### ImportError: No module named 'maxminddb_rust'

Make sure the package is installed:

```bash
pip install maxminddb-rust
```

### Both packages installed, wrong one being used

Check which one is imported:

```python
import maxminddb_rust

print(maxminddb_rust.__file__)
```

### Tests failing after migration

Update test imports:

```python
# Before
import maxminddb

# After
import maxminddb_rust as maxminddb  # Use alias for minimal test changes
```

## Getting Help

- **GitHub Issues**: https://github.com/oschwald/maxminddb-rust-python/issues
- **API Documentation**: Use `help(maxminddb_rust)` in Python
- **Examples**: See the `examples/` directory in the repository

## Rollback

If you need to revert to the official package:

```bash
pip uninstall maxminddb-rust
pip install maxminddb
```

Then revert your import changes:

```python
# Change back
import maxminddb  # Official package
```

## Comparison Table

| Feature           | Official maxminddb | maxminddb-rust          |
| ----------------- | ------------------ | ----------------------- |
| Package name      | `maxminddb`        | `maxminddb-rust`        |
| Import name       | `import maxminddb` | `import maxminddb_rust` |
| Performance       | Baseline           | Measure with benchmarks |
| Implementation    | Pure Python + C    | Rust (PyO3)             |
| API compatibility | N/A                | Reader API, see above   |
| get_many()        | ❌                 | ✅                      |
| MODE_FILE         | ✅                 | ✅                      |
| MODE_FD           | ✅                 | ✅                      |
| Maintained by     | MaxMind (official) | Community (unofficial)  |

## FAQ

**Q: Is maxminddb-rust official?**
A: No, this is an unofficial high-performance alternative. The official package is maintained by MaxMind.

**Q: Will my code break?**
A: Code that uses the `Reader` API needs only the import change. Code that
imports the official package's submodules or relies on the
[known differences](#known-differences) needs small changes.

**Q: Can I use both packages?**
A: Yes, you can have both installed and choose which to use in your code.

**Q: What about type hints?**
A: Full type stub file included (`maxminddb_rust.pyi`) for IDE autocomplete and type checking.

**Q: Is it production-ready?**
A: The package has been thoroughly tested and includes the official MaxMind test suite. However, it's an unofficial package, so use appropriate caution for production deployments.
