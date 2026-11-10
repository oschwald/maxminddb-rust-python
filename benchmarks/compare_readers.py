#!/usr/bin/env python3
r"""Compare maxminddb-rust with other Python MaxMind DB readers.

Each library and case runs in a fresh interpreter, and the rounds alternate
between the libraries to spread machine drift. A sample is the best of three
passes over the addresses. The result is the median of the samples.

Install the other readers in the same environment first, for example:

    uv run --with maxminddb --with maxminddb-zig \
        python benchmarks/compare_readers.py --file GeoLite2-City.mmdb
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import signal
import statistics
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from _common import chunks, resolve_database

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

LIBRARIES: tuple[str, ...] = ("rust", "official-c", "official-py", "zig", "zig-query")
CASES: tuple[str, ...] = ("get", "get_path", "iterate")
PATH = ("country", "iso_code")
BASELINE = "official-c"
PASSES = 3
TRIES_PER_HIT = 20


def main() -> None:
    """Run the comparison, or one sample when called with --worker."""
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        run_worker(sys.argv[2:])
        return

    parser = argparse.ArgumentParser(
        description="Compare Python MaxMind DB readers in fresh interpreters."
    )
    parser.add_argument("--file", default=None, help="path to mmdb file")
    parser.add_argument("--count", default=200_000, type=int, help="lookups")
    parser.add_argument("--rounds", default=5, type=int, help="samples per result")
    parser.add_argument("--threads", default=1, type=int, help="lookup threads")
    parser.add_argument(
        "--lib", action="append", choices=LIBRARIES, help="library (repeatable)"
    )
    parser.add_argument(
        "--case", action="append", choices=CASES, help="case (repeatable)"
    )
    parser.add_argument("--json-output", help="write medians to this JSON file")
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be at least 1")
    if args.rounds < 1:
        parser.error("--rounds must be at least 1")
    if not 1 <= args.threads <= args.count:
        parser.error("--threads must be between 1 and --count")
    libraries = list(dict.fromkeys(args.lib or LIBRARIES))
    cases = list(dict.fromkeys(args.case or CASES))
    if args.threads > 1 and "iterate" in cases:
        if args.case:
            parser.error(
                "--case iterate needs --threads 1, because every thread would "
                "read the same networks"
            )
        cases.remove("iterate")

    database = resolve_database(args.file)

    with tempfile.TemporaryDirectory() as tmp:
        ips_path = Path(tmp) / "ips.json"
        ips_path.write_text(json.dumps(database_hits(database, args.count)))
        samples = collect(args, database, ips_path, libraries, cases)

    medians = {key: statistics.median(values) for key, values in samples.items()}
    print_table(database, args, libraries, cases, medians)
    if args.json_output:
        output = {f"{case}|{lib}": value for (case, lib), value in medians.items()}
        Path(args.json_output).write_text(json.dumps(output, indent=2) + "\n")


def run_worker(argv: list[str]) -> None:
    """Measure one library and case, and print operations per second as JSON."""
    lib, case, database, ips_path, threads = argv
    ips = json.loads(Path(ips_path).read_text())
    reader = open_reader(lib, database)
    if (
        sysconfig.get_config_var("Py_GIL_DISABLED") and sys._is_gil_enabled()  # noqa: SLF001 - Documented, despite the underscore.
    ):
        msg = (
            f"The GIL is on after loading the {lib} reader, so this run would not "
            "measure free-threaded Python. An extension without free-threading "
            "support, or PYTHON_GIL=1, turns it on."
        )
        raise RuntimeError(msg)
    tasks = (make_work(lib, case, reader, chunk) for chunk in chunks(ips, int(threads)))
    work = [task for task in tasks if task is not None]
    if not work:
        print("null")
        return
    for task in work:
        task()  # Warm up.
    best = min(timed_pass(work) for _ in range(PASSES))
    print(json.dumps(len(ips) / best))


def open_reader(lib: str, database: str) -> Any:  # noqa: ANN401 - Each library has its own Reader type.
    """Open the database with the given library."""
    if lib == "rust":
        import maxminddb_rust  # noqa: PLC0415

        return maxminddb_rust.open_database(database)
    if lib.startswith("official"):
        import maxminddb  # noqa: PLC0415

        if lib == "official-c":
            return maxminddb.open_database(database, maxminddb.MODE_MMAP_EXT)
        return maxminddb.open_database(database, maxminddb.MODE_MMAP)
    import maxminddb_zig  # noqa: PLC0415

    return maxminddb_zig.Reader(database)


def make_work(
    lib: str,
    case: str,
    reader: Any,  # noqa: ANN401 - Each library has its own Reader type.
    ips: list[str],
) -> Callable[[], None] | None:
    """Return a function that runs one pass of the case, or None if unsupported."""
    if case == "iterate":
        if lib == "zig-query":
            return None
        return lambda: iterate(reader, len(ips))

    def run() -> None:
        # Each pass gets a new lookup, so every maxminddb-zig query() pass
        # starts with an empty record cache.
        if case == "get":
            lookup = full_record_lookup(lib, reader)
        else:
            lookup = field_lookup(lib, reader)
        for ip in ips:
            lookup(ip)

    return run


def iterate(reader: Any, count: int) -> None:  # noqa: ANN401 - Each library has its own Reader type.
    """Read the first count networks from the reader's iterator."""
    read = sum(1 for _ in itertools.islice(reader, count))
    if read < count:
        msg = f"The database has {read:,} networks, fewer than the {count:,} needed."
        raise RuntimeError(msg)


def full_record_lookup(lib: str, reader: Any) -> Callable[[str], object]:  # noqa: ANN401 - Each library has its own Reader type.
    """Return the function that decodes the full record for an address."""
    if lib == "zig-query":
        return reader.query().lookup
    if lib == "zig":
        return reader.lookup
    return reader.get


def field_lookup(lib: str, reader: Any) -> Callable[[str], object]:  # noqa: ANN401 - Each library has its own Reader type.
    """Return a function that reads PATH the fastest way each library offers.

    Each function binds its method once. Looking it up on every call would
    make the threads contend on the shared reader's reference count.
    """
    if lib == "rust":
        get_path = reader.get_path
        return lambda ip: get_path(ip, PATH)
    if lib == "zig-query":
        query_lookup = reader.query(PATH[0]).lookup
        return lambda ip: dig(query_lookup(ip)[0], PATH)
    if lib == "zig":
        lookup = reader.lookup
        return lambda ip: dig(lookup(ip, PATH[0])[0], PATH)
    get = reader.get
    return lambda ip: dig(get(ip), PATH)


def timed_pass(work: Sequence[Callable[[], None]]) -> float:
    """Run each task on its own thread and return the elapsed seconds."""
    if len(work) == 1:
        start = time.perf_counter()
        work[0]()
        return time.perf_counter() - start
    starts: list[float] = []
    # The action runs after every thread arrives and before any is released,
    # so the clock starts before any work does.
    barrier = threading.Barrier(
        len(work) + 1, action=lambda: starts.append(time.perf_counter())
    )
    errors: list[BaseException] = []

    def run(task: Callable[[], None]) -> None:
        barrier.wait()
        try:
            task()
        except BaseException as error:
            errors.append(error)
            raise

    threads = [threading.Thread(target=run, args=(task,)) for task in work]
    try:
        for thread in threads:
            thread.start()
    except BaseException:
        # Release the threads that started, so they do not wait forever.
        barrier.abort()
        raise
    barrier.wait()
    for thread in threads:
        thread.join()
    elapsed = time.perf_counter() - starts[0]
    if errors:
        msg = f"{len(errors)} of {len(work)} threads failed. See the tracebacks above."
        raise RuntimeError(msg) from errors[0]
    return elapsed


def database_hits(database: Path, count: int) -> list[str]:
    """Return random IPv4 addresses that the database contains."""
    import maxminddb_rust  # noqa: PLC0415 - The worker imports each library itself.

    rng = random.Random(0)  # noqa: S311 - Benchmark addresses, not secrets.
    ips: list[str] = []
    tries = count * TRIES_PER_HIT
    with maxminddb_rust.open_database(database) as reader:
        for _ in range(tries):
            ip = ".".join(str(rng.randrange(256)) for _ in range(4))
            if reader.get(ip) is not None:
                ips.append(ip)
                if len(ips) == count:
                    return ips
    msg = (
        f"{database.name} contains only {len(ips):,} of {tries:,} random IPv4 "
        f"addresses, and the benchmark needs {count:,}. Use a database with "
        "more IPv4 data, or lower --count."
    )
    raise RuntimeError(msg)


def collect(
    args: argparse.Namespace,
    database: Path,
    ips_path: Path,
    libraries: Sequence[str],
    cases: Sequence[str],
) -> dict[tuple[str, str], list[float]]:
    """Run every sample, alternating the libraries within each round."""
    samples: dict[tuple[str, str], list[float]] = {}
    for _ in range(args.rounds):
        for case in cases:
            for lib in libraries:
                command = [
                    sys.executable,
                    __file__,
                    "--worker",
                    lib,
                    case,
                    str(database),
                    str(ips_path),
                    str(args.threads),
                ]
                result = subprocess.run(  # noqa: S603 - Fixed argument vector; no shell.
                    command, capture_output=True, text=True, check=False
                )
                if result.returncode != 0:
                    status = exit_status(result.returncode)
                    msg = f"{lib} {case} failed with {status}:\n{result.stderr}"
                    raise RuntimeError(msg)
                try:
                    value = json.loads(result.stdout.splitlines()[-1])
                except (IndexError, json.JSONDecodeError) as error:
                    msg = (
                        f"{lib} {case} did not end its output with a JSON result."
                        f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
                    )
                    raise RuntimeError(msg) from error
                if value is not None:
                    samples.setdefault((case, lib), []).append(value)
    return samples


def print_table(
    database: Path,
    args: argparse.Namespace,
    libraries: Sequence[str],
    cases: Sequence[str],
    medians: dict[tuple[str, str], float],
) -> None:
    """Print operations per second as a Markdown table."""
    print(
        f"{database.name}, {args.count:,} database hits, {args.threads} thread(s), "
        f"median of {args.rounds} rounds, Python {sys.version.split()[0]}"
    )
    print()
    print("| Case | " + " | ".join(libraries) + " |")
    print("| --- |" + " ---: |" * len(libraries))
    for case in cases:
        baseline = medians.get((case, BASELINE))
        cells = []
        for lib in libraries:
            value = medians.get((case, lib))
            if value is None:
                cells.append("-")
            elif baseline and lib != BASELINE:
                cells.append(f"{value:,.0f} ({value / baseline:.2f}x)")
            else:
                cells.append(f"{value:,.0f}")
        print(f"| {case} | " + " | ".join(cells) + " |")


def dig(record: Any, path: tuple[str, ...]) -> object:  # noqa: ANN401 - Records are nested mappings.
    """Follow a path of keys through a record, returning None if one is missing."""
    for key in path:
        if record is None:
            return None
        record = record.get(key)
    return record


def exit_status(returncode: int) -> str:
    """Describe a worker's exit status, with the signal name if a signal ended it."""
    if returncode >= 0:
        return f"exit code {returncode}"
    try:
        return signal.Signals(-returncode).name
    except ValueError:
        return f"signal {-returncode}"


if __name__ == "__main__":
    main()
