# Contributing to maxminddb-rust

Thank you for your interest in contributing! This document provides guidelines for development, testing, and code quality.

## Development Setup

### Prerequisites

- Rust toolchain (latest stable)
- Python 3.9 or later
- [uv](https://docs.astral.sh/uv/) for Python dependency management
- [maturin](https://www.maturin.rs/) for building the extension module

### Building from Source

```bash
# Install maturin
pip install maturin

# Build and install in development mode
maturin develop --release

# Or build wheel
maturin build --release
```

### Running the Project

```bash
# Run Python code with the built module
uv run python your_script.py

# Run benchmarks
uv run python benchmark.py --file /path/to/database.mmdb
```

## Testing

### Test Structure

This project includes two types of tests:

1. **Upstream compatibility tests** (`tests/maxmind/`) - Copied from MaxMind-DB-Reader-python to ensure API compatibility
2. **Custom tests** - Additional tests specific to maxminddb-rust

### Running Tests

```bash
# Initialize test data submodule (first time only)
git submodule update --init --recursive

# Install test dependencies
uv pip install pytest

# Run all tests
uv run pytest

# Run specific test directory
uv run pytest tests/maxmind/

# Run with verbose output
uv run pytest -v

# Run with coverage
uv run pytest --cov=maxminddb_rust
```

### Upstream Test Syncing

For instructions on syncing compatibility tests from the upstream MaxMind-DB-Reader-python repository, see [tests/maxmind/README.md](tests/maxmind/README.md).

## Code Quality

This project uses [precious](https://github.com/houseabsolute/precious/) to manage linters and formatters consistently.

### Using Precious

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

### Individual Linters

You can also run linters and formatters directly:

#### Rust

```bash
# Check code with Clippy
cargo clippy --lib --all-features -- -D warnings

# Format code
cargo fmt --all

# Run Rust tests
cargo test
```

#### Python

```bash
# Check code with Ruff
ruff check .

# Format code with Ruff
ruff format .

# Type check with mypy
mypy .
```

#### Markdown and YAML

```bash
# Check formatting
prettier --check "**/*.md" "**/*.yml"

# Format files
prettier --write "**/*.md" "**/*.yml"
```

## Git Pre-commit Hook

The repository includes a pre-commit hook that automatically runs precious lint on staged files before each commit.

### Enabling the Hook

```bash
# Configure git to use the .githooks directory
git config core.hooksPath .githooks
```

### If Linting Fails

If linting fails during a commit, fix the issues:

```bash
# Automatically fix formatting on staged files
precious tidy --staged

# Re-add fixed files
git add -u

# Retry commit
git commit
```

### Temporarily Bypassing the Hook

In rare cases where you need to bypass the hook (e.g., work-in-progress commits):

```bash
git commit --no-verify
```

**Note:** Avoid using `--no-verify` for commits that will be pushed to the repository.

## Pull Request Guidelines

When submitting a pull request:

1. **Run tests**: Ensure all tests pass with `uv run pytest`
2. **Run linters**: Fix all linting issues with `precious tidy --all`
3. **Update tests**: Add or update tests for any new functionality
4. **Update documentation**: Update README.md, docstrings, or type stubs as needed
5. **Follow commit conventions**: Write clear, descriptive commit messages
6. **Update CHANGELOG.md**: Add an entry describing your changes (for non-trivial changes)

### Commit Message Format

Use clear, imperative commit messages:

```
Add support for MODE_FILE

Implement file-based reading mode to match maxminddb API.
Includes tests and documentation updates.
```

## API Compatibility

This project aims for 100% API compatibility with the official `maxminddb` Python module. When adding features:

1. Match the exact API of the upstream module
2. Add upstream compatibility tests when possible
3. Document any intentional deviations or extensions
4. Ensure type stubs match the runtime behavior

## Performance Considerations

This is a performance-focused project. When making changes:

1. Run benchmarks before and after changes
2. Profile code for bottlenecks
3. Prefer zero-copy operations where possible
4. Minimize Python/Rust boundary crossings
5. Document any performance tradeoffs

## Questions or Issues?

- **Bug reports**: Open an issue on GitHub
- **Feature requests**: Open an issue to discuss before implementing
- **Questions**: Feel free to open a discussion or issue

Thank you for contributing! 🎉
