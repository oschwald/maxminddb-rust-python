# maxminddb-rust (PyO3 extension `maxminddb_rust`)

## Build and test

- `git submodule update --init` - tests need `tests/data` (MaxMind-DB fixtures)
- `maturin develop --release --extras test && python -m pytest -q` - build into
  the active venv with pytest, run tests
- `cargo clippy --all-targets -- -D warnings`, `ruff check .`,
  `ruff format --check .`, `prettier --check "**/*.md" "**/*.yml"` - what CI
  lints
- Oldest supported Python is 3.10: run the suite there too. Create and activate
  a venv outside the repo (`uv venv -p 3.10 /tmp/venv-3.10`, then
  `. /tmp/venv-3.10/bin/activate`), then run the build and test command above.
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

- `release.yml` lists Windows ARM64 Python versions separately (no 3.10).
  Update both lists when adding a version. `test_wheels` installs each wheel
  artifact on a runner of its own architecture and runs
  `dev-bin/smoke_test.py`: when adding a build target or raising the oldest
  or newest Python, update its `platform` entries and `python` list too.
  `test_musllinux_wheels` does the same for the musllinux wheels in Alpine
  containers
- `upload_pypi` waits for the wheel tests, `test_sdist` and `check_versions`.
  `dev-bin/check_release_version.py` fails the release when the tag,
  `pyproject.toml`, `Cargo.toml` or an artifact name disagree on the version
- Pyodide build: `RUSTUP_TOOLCHAIN` must match `RUST_TOOLCHAIN` in Pyodide's
  `Makefile.envs` for `CIBW_PYODIDE_VERSION`
- Add `CHANGELOG.md` entries under `## [Unreleased]` in the PR that makes the
  change
- Before a release, refresh the reader comparison on an idle machine. Install
  the latest `maxminddb` and `maxminddb-zig` next to a release build of this
  tree (`maturin develop --release`). Run `benchmarks/compare_readers.py` with
  `--json-output` on GeoLite2 City and Country (`--rounds 5`). Then run it on
  City with free-threaded 3.14t at `--threads` 1, 2, 4 and 8
  (`--lib rust --lib official-c --case get --case get_path --rounds 3`).
  maxminddb-zig crashes on free-threaded Python. Update the README Performance
  table and `BENCHMARKS.md` (Summary ratios, result tables, Method), and check
  that each claim still holds. In the Readers table, set each version to the one
  measured. For maxminddb-rust, that is the version being released
- Release: date the top heading `## [x.y.z] - YYYY-MM-DD` (today) and commit
  it, then run `dev-bin/release.sh`. It needs a clean tree (no changes and no
  untracked files), bumps `Cargo.toml`/`pyproject.toml`, pushes `main`, and the
  GitHub release publishes to PyPI
