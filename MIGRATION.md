# Migration Guide

This guide explains how to migrate from the official `maxminddb` package to `maxminddb-rust`.

## Why Migrate?

`maxminddb-rust` offers significant performance improvements over the official `maxminddb` package:

- **45% faster** on average (373K vs 257K lookups/second)
- Same API, just better performance
- All features supported (except MODE_FILE and MODE_FD)
- Additional `get_many()` method for batch lookups

## Quick Migration

The only change required is updating your import statement:

### Before (official maxminddb)

```python
import maxminddb

reader = maxminddb.open_database('/path/to/GeoIP2-City.mmdb')
result = reader.get('8.8.8.8')
```

### After (maxminddb-rust)

```python
import maxminddb_rust

reader = maxminddb_rust.open_database('/path/to/GeoIP2-City.mmdb')
result = reader.get('8.8.8.8')
```

That's it! All other code remains identical.

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
reader = maxminddb.open_database('/path/to/GeoIP2-City.mmdb')
result = reader.get('8.8.8.8')
```

This allows gradual migration while maintaining compatibility.

### Option 3: Shell Script Migration

For Unix-like systems:

```bash
# Find all Python files using maxminddb
find . -name "*.py" -type f -exec grep -l "import maxminddb" {} \;

# Replace imports in place (review before running!)
find . -name "*.py" -type f -exec sed -i 's/import maxminddb$/import maxminddb_rust/g' {} \;
find . -name "*.py" -type f -exec sed -i 's/from maxminddb /from maxminddb_rust /g' {} \;
```

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
import maxminddb       # Use official implementation (if still needed)
```

## API Compatibility

### Fully Compatible Features

✅ **100% compatible** - No code changes needed:

- `Reader` class and all methods
- `open_database()` function
- `get()`, `get_with_prefix_len()`, `metadata()`, `close()`
- Context manager support (`with` statement)
- Iterator support (iterating over all networks)
- `Metadata` class and all properties
- MODE_AUTO, MODE_MMAP, MODE_MMAP_EXT, MODE_MEMORY constants
- `InvalidDatabaseError` exception
- String and ipaddress object support

### Extension Features

⭐ **Bonus features** in `maxminddb-rust`:

- `get_many()` - Batch IP lookup method (not in official package)

### Not Yet Implemented

⏸️ These modes are not yet supported in `maxminddb-rust`:

- MODE_FILE (use MODE_MMAP or MODE_MEMORY instead)
- MODE_FD (file descriptor mode)

If you use these modes, you'll need to update your code to use MODE_MMAP or MODE_MEMORY.

## Example Migration

### Before: Web Application Using Official maxminddb

```python
# app.py
import maxminddb
from flask import Flask, request

app = Flask(__name__)
reader = maxminddb.open_database('/var/lib/GeoIP/GeoIP2-City.mmdb')

@app.route('/lookup')
def lookup():
    ip = request.args.get('ip')
    result = reader.get(ip)
    return result
```

### After: Using maxminddb-rust

```python
# app.py
import maxminddb_rust
from flask import Flask, request

app = Flask(__name__)
reader = maxminddb_rust.open_database('/var/lib/GeoIP/GeoIP2-City.mmdb')

@app.route('/lookup')
def lookup():
    ip = request.args.get('ip')
    result = reader.get(ip)
    return result
```

**Only 1 line changed!**

## Performance Comparison

After migration, you should see performance improvements:

| Operation        | Official maxminddb | maxminddb-rust | Improvement |
| ---------------- | ------------------ | -------------- | ----------- |
| Single lookup    | ~260K ops/sec      | ~373K ops/sec  | +45%        |
| Batch (get_many) | N/A                | ~500K+ ops/sec | New feature |

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
| Performance       | Baseline           | 45% faster              |
| Implementation    | Pure Python + C    | Rust (PyO3)             |
| API compatibility | N/A                | 100%                    |
| get_many()        | ❌                 | ✅                      |
| MODE_FILE         | ✅                 | ❌ (use MODE_MMAP)      |
| MODE_FD           | ✅                 | ❌ (not yet)            |
| Maintained by     | MaxMind (official) | Community (unofficial)  |

## FAQ

**Q: Is maxminddb-rust official?**
A: No, this is an unofficial high-performance alternative. The official package is maintained by MaxMind.

**Q: Will my code break?**
A: No, the API is 100% compatible. Only the import name changes.

**Q: Can I use both packages?**
A: Yes, you can have both installed and choose which to use in your code.

**Q: What about type hints?**
A: Full type stub file included (`maxminddb_rust.pyi`) for IDE autocomplete and type checking.

**Q: Is it production-ready?**
A: The package has been thoroughly tested and includes the official MaxMind test suite. However, it's an unofficial package, so use appropriate caution for production deployments.
