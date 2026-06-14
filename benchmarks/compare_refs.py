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
    path_items = ["country", "iso_code"]

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

    elif args.case == "get_path_new_tuple":
        if not hasattr(reader, "get_path"):
            print(json.dumps({"supported": False}))
            return

        def run_once() -> int:
            for ip in ips:
                reader.get_path(ip, tuple(path_items))
            return len(ips)

    elif args.case == "get_path_list":
        if not hasattr(reader, "get_path"):
            print(json.dumps({"supported": False}))
            return

        def run_once() -> int:
            for ip in ips:
                reader.get_path(ip, path_items)
            return len(ips)

    elif args.case == "get_many_path":
        if not hasattr(reader, "get_many_path"):
            print(json.dumps({"supported": False}))
            return

        def run_once() -> int:
            for batch in batches:
                reader.get_many_path(batch, path)
            return len(ips)

    elif args.case == "iterate":

        def run_once() -> int:
            operations = 0
            while operations < args.count:
                found_record = False
                for _network, _record in reader:
                    found_record = True
                    operations += 1
                    if operations >= args.count:
                        break
                if not found_record:
                    break
            return operations

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


DEFAULT_CASES = ("get", "get_many", "get_path", "get_many_path", "iterate")
ALL_CASES = (*DEFAULT_CASES, "get_path_new_tuple", "get_path_list")


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
    label: str,
    ref: str,
    *,
    base_dir: Path,
    root: Path,
    verbose: bool,
) -> tuple[Path, Path]:
    safe_name = f"{label}-{safe_ref_name(ref)}"
    worktree = base_dir / f"worktree-{safe_name}"
    venv = base_dir / f"venv-{safe_name}"

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
    delta = delta_percent(baseline, candidate)
    if delta is None:
        return "-"
    return f"{delta:+.1f}%"


def delta_percent(baseline: dict[str, Any], candidate: dict[str, Any]) -> float | None:
    if not baseline.get("supported") or not candidate.get("supported"):
        return None
    baseline_median = baseline["median"]
    if baseline_median <= 0:
        return None
    return (candidate["median"] / baseline_median - 1) * 100


def build_summary(
    *,
    baseline_ref: str,
    candidate_ref: str,
    database: Path,
    count: int,
    batch_size: int,
    repeats: int,
    warmups: int,
    cases: list[str],
    baseline_results: dict[str, dict[str, Any]],
    candidate_results: dict[str, dict[str, Any]],
    max_regression_pct: float | None,
) -> dict[str, Any]:
    case_summaries = {}
    regressions = []
    for case in cases:
        baseline = baseline_results[case]
        candidate = candidate_results[case]
        delta = delta_percent(baseline, candidate)
        case_summaries[case] = {
            "baseline": {"ref": baseline_ref, **baseline},
            "candidate": {"ref": candidate_ref, **candidate},
            "delta_percent": delta,
        }
        if max_regression_pct is None or not baseline.get("supported"):
            continue
        if not candidate.get("supported"):
            regressions.append(
                {
                    "case": case,
                    "reason": "candidate_unsupported",
                    "max_regression_pct": max_regression_pct,
                }
            )
        elif delta is not None and delta < -max_regression_pct:
            regressions.append(
                {
                    "case": case,
                    "delta_percent": delta,
                    "max_regression_pct": max_regression_pct,
                }
            )

    return {
        "baseline_ref": baseline_ref,
        "candidate_ref": candidate_ref,
        "database": str(database),
        "count": count,
        "batch_size": batch_size,
        "repeats": repeats,
        "warmups": warmups,
        "max_regression_pct": max_regression_pct,
        "cases": case_summaries,
        "regressions": regressions,
    }


def print_table(
    cases: list[str],
    baseline_label: str,
    candidate_label: str,
    baseline_results: dict[str, dict[str, Any]],
    candidate_results: dict[str, dict[str, Any]],
) -> None:
    rows = [
        (
            case,
            format_rate(baseline_results[case]),
            format_rate(candidate_results[case]),
            format_delta(baseline_results[case], candidate_results[case]),
        )
        for case in cases
    ]
    headers = ("case", baseline_label, candidate_label, "delta")
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
        choices=ALL_CASES,
        help="benchmark case to run; may be specified more than once",
    )
    parser.add_argument("--keep-worktrees", action="store_true")
    parser.add_argument(
        "--json",
        action="store_true",
        help="write the benchmark summary as JSON to stdout instead of a table",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="write the benchmark summary as JSON to this file",
    )
    parser.add_argument(
        "--max-regression-pct",
        type=float,
        help="exit non-zero if a supported case regresses by more than this percentage",
    )
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
    if args.max_regression_pct is not None and args.max_regression_pct < 0:
        raise ValueError("--max-regression-pct must be non-negative")

    cases = args.case or list(DEFAULT_CASES)
    refs = [
        ("baseline", args.baseline_ref),
        ("candidate", args.candidate_ref),
    ]
    results: dict[str, dict[str, dict[str, Any]]] = {
        "baseline": {},
        "candidate": {},
    }

    temp_context = None
    if args.keep_worktrees:
        temp_dir = Path(tempfile.mkdtemp(prefix="maxminddb-rust-python-bench."))
    else:
        temp_context = tempfile.TemporaryDirectory(
            prefix="maxminddb-rust-python-bench."
        )
        temp_dir = Path(temp_context.name)

    worktrees: list[Path] = []
    try:
        interpreters: dict[str, Path] = {}
        for label, ref in refs:
            started = time.perf_counter()
            worktree, python = prepare_ref(
                label,
                ref,
                base_dir=temp_dir,
                root=root,
                verbose=args.verbose,
            )
            worktrees.append(worktree)
            interpreters[label] = python
            elapsed = time.perf_counter() - started
            print(f"Prepared {ref} in {elapsed:.1f}s", file=sys.stderr)

        for label, ref in refs:
            for case in cases:
                results[label][case] = benchmark_case(
                    interpreters[label],
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
                    f"Benchmarked {ref} {case}: {format_rate(results[label][case])}",
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

    summary = build_summary(
        baseline_ref=args.baseline_ref,
        candidate_ref=args.candidate_ref,
        database=database,
        count=args.count,
        batch_size=args.batch_size,
        repeats=args.repeats,
        warmups=args.warmups,
        cases=cases,
        baseline_results=results["baseline"],
        candidate_results=results["candidate"],
        max_regression_pct=args.max_regression_pct,
    )

    if args.json_output is not None:
        args.json_output.write_text(json.dumps(summary, indent=2) + "\n")

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
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
        baseline_label = args.baseline_ref
        candidate_label = args.candidate_ref
        if baseline_label == candidate_label:
            baseline_label = f"baseline {baseline_label}"
            candidate_label = f"candidate {candidate_label}"
        print_table(
            cases,
            baseline_label,
            candidate_label,
            results["baseline"],
            results["candidate"],
        )

    if summary["regressions"]:
        print("Benchmark regressions exceeded threshold:", file=sys.stderr)
        for regression in summary["regressions"]:
            case = regression["case"]
            if regression.get("reason") == "candidate_unsupported":
                print(
                    f"  {case}: candidate does not support this case", file=sys.stderr
                )
            else:
                print(
                    f"  {case}: {regression['delta_percent']:+.1f}% "
                    f"(threshold -{regression['max_regression_pct']:.1f}%)",
                    file=sys.stderr,
                )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
