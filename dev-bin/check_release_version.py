"""Check that the release artifacts carry the version being released.

The release workflow runs this on the downloaded wheels and sdist before
publishing. It checks that pyproject.toml and Cargo.toml agree, that every
artifact is named with that version, and, when given a release tag, that the
tag is "v" followed by that version. PyPI files cannot be replaced, so a
mismatch has to be caught before upload.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_NAME = "maxminddb_rust"
SDIST_SUFFIX = ".tar.gz"
WHEEL_SUFFIX = ".whl"


# The first top-level "version =" line, the same line dev-bin/release.sh
# rewrites. It is the [project] version in pyproject.toml and the [package]
# version in Cargo.toml.
VERSION_LINE = re.compile(r'^version = "([^"]+)"', re.MULTILINE)


def read_version(path: Path) -> str:
    """Return the version that dev-bin/release.sh sets in a manifest."""
    match = VERSION_LINE.search(path.read_text(encoding="utf-8"))
    if match is None:
        sys.exit(f"no version line in {path}")
    return match.group(1)


def artifact_errors(dist: Path, version: str) -> list[str]:
    """Return a message for each artifact problem found in dist."""
    files = sorted(p for p in dist.rglob("*") if p.is_file())
    errors = []
    wheels = 0
    sdists = 0
    for path in files:
        name = path.name
        if name.endswith(WHEEL_SUFFIX):
            wheels += 1
            # {distribution}-{version}(-{build})?-{python}-{abi}-{platform}.whl
            dist_name, _, rest = name.partition("-")
            file_version = rest.partition("-")[0]
        elif name.endswith(SDIST_SUFFIX):
            sdists += 1
            dist_name, _, file_version = name.removesuffix(SDIST_SUFFIX).partition("-")
        else:
            errors.append(f"{name}: not a wheel or sdist")
            continue
        if dist_name != DIST_NAME:
            errors.append(f"{name}: distribution {dist_name!r}, expected {DIST_NAME!r}")
        if file_version != version:
            errors.append(f"{name}: version {file_version!r}, expected {version!r}")
    if wheels == 0:
        errors.append(f"no wheels in {dist}")
    if sdists != 1:
        errors.append(f"found {sdists} sdists in {dist}, expected 1")
    return errors


def main() -> None:
    """Check the versions and exit with an error listing every mismatch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path, help="directory holding the artifacts")
    parser.add_argument("--tag", default="", help="release tag, such as v1.2.3")
    args = parser.parse_args()

    version = read_version(ROOT / "pyproject.toml")
    errors = []

    cargo_version = read_version(ROOT / "Cargo.toml")
    if cargo_version != version:
        errors.append(
            f"Cargo.toml has version {cargo_version!r}, pyproject.toml has {version!r}",
        )

    if args.tag and args.tag != f"v{version}":
        errors.append(
            f"release tag {args.tag!r} does not match "
            f"pyproject.toml version {version!r} (expected 'v{version}')",
        )

    errors.extend(artifact_errors(args.dist, version))

    if errors:
        sys.exit("\n".join(errors))

    tag_note = f" and tag {args.tag}" if args.tag else ""
    print(f"All artifacts in {args.dist} match version {version}{tag_note}")


if __name__ == "__main__":
    main()
