#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any


BENCHMARK_RUNNER = r"""
from __future__ import annotations

import argparse
import json
import random
import socket
import statistics
import struct
import time

import maxminddb_rust


def generate_ips(count: int) -> list[str]:
    random.seed(0)
    return [
        socket.inet_ntoa(struct.pack("!L", random.getrandbits(32)))
        for _ in range(count)
    ]


def chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[start : start + size] for start in range(0, len(values), size)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--count", required=True, type=int)
    parser.add_argument("--batch-size", required=True, type=int)
    parser.add_argument("--repeats", required=True, type=int)
    parser.add_argument("--warmups", required=True, type=int)
    args = parser.parse_args()

    reader = maxminddb_rust.open_database(args.file)
    ips = generate_ips(args.count)
    batches = chunks(ips, args.batch_size)
    path = ("country", "iso_code")

    if args.case == "get":

        def run_once() -> int:
            for ip in ips:
                reader.get(ip)
            return len(ips)

    elif args.case == "get_many":
        if not hasattr(reader, "get_many"):
            print(json.dumps({"supported": False}))
            return

        def run_once() -> int:
            for batch in batches:
                reader.get_many(batch)
            return len(ips)

    elif args.case == "get_path":
        if not hasattr(reader, "get_path"):
            print(json.dumps({"supported": False}))
            return

        def run_once() -> int:
            for ip in ips:
                reader.get_path(ip, path)
            return len(ips)

    elif args.case == "get_many_path":
        if not hasattr(reader, "get_many_path"):
            print(json.dumps({"supported": False}))
            return

        def run_once() -> int:
            for batch in batches:
                reader.get_many_path(batch, path)
            return len(ips)

    else:
        raise ValueError(f"unknown benchmark case: {args.case}")

    for _ in range(args.warmups):
        run_once()

    samples = []
    for _ in range(args.repeats):
        start = time.perf_counter()
        operations = run_once()
        elapsed = time.perf_counter() - start
        samples.append(operations / elapsed)

    reader.close()
    print(
        json.dumps(
            {
                "supported": True,
                "median": statistics.median(samples),
                "samples": samples,
            }
        )
    )


if __name__ == "__main__":
    main()
"""


DEFAULT_CASES = ("get", "get_many", "get_path", "get_many_path")


def run_command(
    command: list[str],
    *,
    cwd: Path,
    verbose: bool,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    if verbose:
        print("+", " ".join(command), file=sys.stderr)
    return subprocess.run(
        command,
        check=True,
        cwd=cwd,
        text=True,
        capture_output=capture_output,
    )


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def safe_ref_name(ref: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in ref).strip("-") or "ref"


def prepare_ref(
    ref: str,
    *,
    base_dir: Path,
    root: Path,
    verbose: bool,
) -> tuple[Path, Path]:
    worktree = base_dir / f"worktree-{safe_ref_name(ref)}"
    venv = base_dir / f"venv-{safe_ref_name(ref)}"

    run_command(
        ["git", "worktree", "add", "--detach", str(worktree), ref],
        cwd=root,
        verbose=verbose,
    )
    run_command(["uv", "venv", str(venv)], cwd=root, verbose=verbose)
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    run_command(
        ["uv", "pip", "install", "--python", str(python), str(worktree)],
        cwd=root,
        verbose=verbose,
    )
    return worktree, python


def benchmark_case(
    python: Path,
    *,
    case: str,
    database: Path,
    count: int,
    batch_size: int,
    repeats: int,
    warmups: int,
    root: Path,
    verbose: bool,
) -> dict[str, Any]:
    result = run_command(
        [
            str(python),
            "-c",
            BENCHMARK_RUNNER,
            "--case",
            case,
            "--file",
            str(database),
            "--count",
            str(count),
            "--batch-size",
            str(batch_size),
            "--repeats",
            str(repeats),
            "--warmups",
            str(warmups),
        ],
        cwd=root,
        verbose=verbose,
        capture_output=True,
    )
    return json.loads(result.stdout)


def format_rate(result: dict[str, Any]) -> str:
    if not result.get("supported"):
        return "-"
    return f"{result['median']:,.0f}"


def format_delta(baseline: dict[str, Any], candidate: dict[str, Any]) -> str:
    if not baseline.get("supported") or not candidate.get("supported"):
        return "-"
    delta = (candidate["median"] / baseline["median"] - 1) * 100
    return f"{delta:+.1f}%"


def print_table(
    cases: list[str],
    baseline_ref: str,
    candidate_ref: str,
    results: dict[str, dict[str, dict[str, Any]]],
) -> None:
    rows = [
        (
            case,
            format_rate(results[baseline_ref][case]),
            format_rate(results[candidate_ref][case]),
            format_delta(results[baseline_ref][case], results[candidate_ref][case]),
        )
        for case in cases
    ]
    headers = ("case", baseline_ref, candidate_ref, "delta")
    widths = [
        max(len(str(row[index])) for row in (headers, *rows))
        for index in range(len(headers))
    ]

    print(
        " | ".join(
            str(value).ljust(widths[index]) for index, value in enumerate(headers)
        )
    )
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(
            " | ".join(
                str(value).ljust(widths[index]) for index, value in enumerate(row)
            )
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare benchmark throughput between two git refs."
    )
    parser.add_argument("--baseline-ref", default="origin/main")
    parser.add_argument("--candidate-ref", default="HEAD")
    parser.add_argument(
        "--file",
        default="tests/data/test-data/GeoIP2-City-Test.mmdb",
        help="path to the mmdb file to benchmark",
    )
    parser.add_argument("--count", type=int, default=250_000)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument(
        "--case",
        action="append",
        choices=DEFAULT_CASES,
        help="benchmark case to run; may be specified more than once",
    )
    parser.add_argument("--keep-worktrees", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = repo_root()
    database = Path(args.file)
    if not database.is_absolute():
        database = root / database
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be positive")
    if args.count <= 0:
        raise ValueError("--count must be positive")
    if args.repeats <= 0:
        raise ValueError("--repeats must be positive")
    if args.warmups < 0:
        raise ValueError("--warmups must be non-negative")

    cases = args.case or list(DEFAULT_CASES)
    refs = [args.baseline_ref, args.candidate_ref]
    results: dict[str, dict[str, dict[str, Any]]] = {ref: {} for ref in refs}

    temp_context = None
    if args.keep_worktrees:
        temp_dir = Path(tempfile.mkdtemp(prefix="maxminddb-pyo3-bench."))
    else:
        temp_context = tempfile.TemporaryDirectory(prefix="maxminddb-pyo3-bench.")
        temp_dir = Path(temp_context.name)

    worktrees: list[Path] = []
    try:
        interpreters: dict[str, Path] = {}
        for ref in refs:
            started = time.perf_counter()
            worktree, python = prepare_ref(
                ref, base_dir=temp_dir, root=root, verbose=args.verbose
            )
            worktrees.append(worktree)
            interpreters[ref] = python
            elapsed = time.perf_counter() - started
            print(f"Prepared {ref} in {elapsed:.1f}s", file=sys.stderr)

        for ref in refs:
            for case in cases:
                results[ref][case] = benchmark_case(
                    interpreters[ref],
                    case=case,
                    database=database,
                    count=args.count,
                    batch_size=args.batch_size,
                    repeats=args.repeats,
                    warmups=args.warmups,
                    root=root,
                    verbose=args.verbose,
                )
                print(
                    f"Benchmarked {ref} {case}: {format_rate(results[ref][case])}",
                    file=sys.stderr,
                )
    finally:
        if args.keep_worktrees:
            print(f"Keeping temporary worktrees under {temp_dir}", file=sys.stderr)
        else:
            for worktree in reversed(worktrees):
                if worktree.exists():
                    run_command(
                        ["git", "worktree", "remove", "--force", str(worktree)],
                        cwd=root,
                        verbose=args.verbose,
                    )
            if temp_context is not None:
                temp_context.cleanup()

    print()
    print(f"Database: {database}")
    print(
        textwrap.dedent(
            f"""\
            Count: {args.count:,}
            Batch size: {args.batch_size:,}
            Repeats: {args.repeats:,}
            Warmups: {args.warmups:,}
            """
        ).rstrip()
    )
    print()
    print_table(cases, args.baseline_ref, args.candidate_ref, results)


if __name__ == "__main__":
    main()
