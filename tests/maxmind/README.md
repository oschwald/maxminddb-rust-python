# Upstream MaxMind Test Compatibility

This directory contains compatibility tests copied and adapted from the
official
[MaxMind-DB-Reader-python](https://github.com/maxmind/MaxMind-DB-Reader-python)
repository to verify API compatibility.

## License

The tests in this directory are copyright MaxMind, Inc. and licensed under the
Apache License, Version 2.0. See [UPSTREAM_LICENSE](./UPSTREAM_LICENSE) for the
full license text.

## Syncing with Upstream

Run these commands from the repository root unless noted otherwise.

### 1. Fetch Latest Upstream Test File

```bash
# Clone or update the upstream repository.
UPSTREAM_REPO=/tmp/MaxMind-DB-Reader-python
if [ -d "$UPSTREAM_REPO/.git" ]; then
    git -C "$UPSTREAM_REPO" pull --ff-only
else
    git clone https://github.com/maxmind/MaxMind-DB-Reader-python.git "$UPSTREAM_REPO"
fi

# Copy the upstream test file into this repository.
cp "$UPSTREAM_REPO/tests/reader_test.py" tests/maxmind/reader_test.py
```

### 2. Apply Required Adaptations

The upstream test file needs these local adaptations to test the
`maxminddb_rust` module.

#### a. Update Copyright Header

Replace the existing header with:

```python
# Copyright (c) MaxMind, Inc.
# Licensed under the Apache License, Version 2.0
# Copied from: https://github.com/maxmind/MaxMind-DB-Reader-python
# Original file: tests/reader_test.py
#
# This file has been adapted for maxminddb_rust compatibility testing.
# See tests/maxmind/UPSTREAM_LICENSE for full license text.
```

#### b. Update Imports

Replace the main package import:

```python
# From:
import maxminddb

# To:
import maxminddb_rust as maxminddb
```

Replace the constants import:

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

Update the `TYPE_CHECKING` import, if present:

```python
# From:
if TYPE_CHECKING:
    from maxminddb.reader import Reader

# To:
if TYPE_CHECKING:
    from maxminddb_rust import Reader
```

#### c. Verify Mode Coverage

The adapted upstream tests should exercise each supported open mode, including
`MODE_FD`.

#### d. Verify Test Data Paths

Ensure test data paths reference the `tests/data` submodule:

```python
# Should be:
"tests/data/test-data/MaxMind-DB-test-..."

# Not:
"tests/data/MaxMind-DB-test-..."
```

### 3. Update Test Data Submodule

```bash
# Update test data to the latest upstream version.
git submodule update --remote tests/data

# Verify the compatibility tests.
uv run pytest tests/maxmind/
```

### 4. Review Changes

After syncing:

1. Run the tests to ensure they pass: `uv run pytest tests/maxmind/`
2. Check for new upstream test methods or features that need adaptation.
3. Update skip markers if previously missing features have been implemented.
4. Commit the updated test file and any submodule changes.

## Running Tests

```bash
# Initialize the test data submodule the first time.
git submodule update --init --recursive

# Run upstream compatibility tests.
uv run pytest tests/maxmind/

# Run with verbose output.
uv run pytest tests/maxmind/ -v
```
