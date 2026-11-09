# maxminddb-rust (PyO3 extension `maxminddb_rust`)

## Build and test

- `git submodule update --init` - tests need `tests/data` (MaxMind-DB fixtures)
- `maturin develop --release --extras test && python -m pytest -q` - build into
  the active venv with pytest, run tests
- `cargo clippy --all-targets -- -D warnings`, `ruff check .`,
  `ruff format --check .`, `prettier --check "**/*.md" "**/*.yml"` - what CI
  lints
- Oldest supported Python is 3.9: run the suite there too. Create and activate
  a venv outside the repo (`uv venv -p 3.9 /tmp/venv-3.9`, then
  `. /tmp/venv-3.9/bin/activate`), then run the build and test command above.
  An untracked venv in the repo blocks `dev-bin/release.sh`
- `benchmarks/compare_refs.py` - A/B two git refs. Run-to-run layout noise is
  about ±2%

## Code gotchas

- Allocating a GC-tracked Python object can run a finalizer that re-enters a
  thread-local cache. Never hold a `RefCell` borrow across one (`src/decode.rs`,
  `src/lib.rs` path cache)
- `ReaderIterator` builds `ipaddress` networks with `__new__`.
  `NETWORK_CHECK_SOURCE` in `src/iterator.rs` falls back to the constructors if
  `__init__`/`__new__` are patched or sample networks differ
- Propagate unexpected Python errors. Fall back only on specific exception types
  (see `is_mismatch_error`, `is_out_of_range`)

## CI and release

- `release.yml` lists Windows ARM64 Python versions separately (no 3.9/3.10).
  Update both lists when adding a version
- Pyodide build: `RUSTUP_TOOLCHAIN` must match `RUST_TOOLCHAIN` in Pyodide's
  `Makefile.envs` for `CIBW_PYODIDE_VERSION`
- Add `CHANGELOG.md` entries under `## [Unreleased]` in the PR that makes the
  change
- Release: date the top heading `## [x.y.z] - YYYY-MM-DD` (today) and commit
  it, then run `dev-bin/release.sh`. It needs a clean tree (no changes and no
  untracked files), bumps `Cargo.toml`/`pyproject.toml`, pushes `main`, and the
  GitHub release publishes to PyPI
