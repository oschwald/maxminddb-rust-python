# Upstream MaxMind Test Compatibility

This directory contains compatibility tests copied and adapted from the official [MaxMind-DB-Reader-python](https://github.com/maxmind/MaxMind-DB-Reader-python) repository to ensure API compatibility.

## License

The tests in this directory are copyright MaxMind, Inc. and licensed under Apache License 2.0. See [UPSTREAM_LICENSE](UPSTREAM_LICENSE) for full license text.

## Syncing with Upstream

To sync with the latest upstream tests:

### 1. Fetch Latest Upstream Test File

```bash
# Clone or update upstream repository
cd /path/to/MaxMind-DB-Reader-python
git pull

# Copy the test file to this directory
cp tests/reader_test.py /path/to/maxminddb-pyo3/tests/maxmind/
```

### 2. Apply Required Adaptations

The upstream test file needs these modifications to work with maxminddb-rust:

#### a. Update Copyright Header

Replace the existing header with:

```python
# Copyright (c) MaxMind, Inc.
# Licensed under the Apache License, Version 2.0
# Copied from: https://github.com/maxmind/MaxMind-DB-Reader-python
# Original file: tests/reader_test.py
#
# This file has been adapted for maxminddb-rust compatibility testing.
# See tests/maxmind/UPSTREAM_LICENSE for full license text.
```

#### b. Update Imports

**Change main maxminddb import:**

```python
# From:
import maxminddb

# To:
import maxminddb_rust as maxminddb
```

**Change constants import:**

```python
# From:
from maxminddb.const import (
    MODE_AUTO,
    MODE_FD,
    MODE_FILE,
    MODE_MEMORY,
    MODE_MMAP,
    MODE_MMAP_EXT,
)

# To:
from maxminddb_rust import (
    InvalidDatabaseError,
    MODE_AUTO,
    MODE_FD,
    MODE_FILE,
    MODE_MEMORY,
    MODE_MMAP,
    MODE_MMAP_EXT,
    open_database,
)
```

**Update TYPE_CHECKING import (if present):**

```python
# From:
if TYPE_CHECKING:
    from maxminddb.reader import Reader

# To:
if TYPE_CHECKING:
    from maxminddb import Reader  # Uses the alias from above
```

**Add pytest import:**

```python
import pytest
```

#### c. Skip Unsupported Test Classes

Add skip markers for test classes that rely on unimplemented features:

```python
@pytest.mark.skip(reason="MODE_FILE not yet supported in maxminddb-rust")
class TestFileReader(BaseTestReader):
    mode = MODE_FILE
    ...

@pytest.mark.skip(reason="MODE_FD not yet supported in maxminddb-rust")
class TestFDReader(BaseTestReader):
    ...
```

#### d. Verify Test Data Paths

Ensure test data paths reference the submodule:

```python
# Should be:
"tests/data/test-data/MaxMind-DB-test-..."

# Not:
"tests/data/MaxMind-DB-test-..."
```

### 3. Update Test Data Submodule

```bash
cd /path/to/maxminddb-pyo3

# Update test data to latest version
git submodule update --remote tests/data

# Verify tests pass
uv run pytest tests/maxmind/
```

### 4. Review Changes

After syncing:

1. Run the tests to ensure they pass: `uv run pytest tests/maxmind/`
2. Check for any new test methods or features that might need adaptation
3. Update skip markers if any features have been implemented
4. Commit the updated test file and submodule changes

## Running Tests

```bash
# Initialize test data submodule (first time only)
git submodule update --init --recursive

# Run upstream compatibility tests
uv run pytest tests/maxmind/

# Run with verbose output
uv run pytest tests/maxmind/ -v
```
