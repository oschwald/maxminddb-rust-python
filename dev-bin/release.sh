#!/bin/bash

set -eu -o pipefail

changelog=$(cat CHANGELOG.md)

regex='## \[([0-9]+\.[0-9]+\.[0-9]+)\] - ([0-9]{4}-[0-9]{2}-[0-9]{2})

((.|
)*)'

if [[ ! $changelog =~ $regex ]]; then
      echo "Could not find version and date line in change log!"
      exit 1
fi

version="${BASH_REMATCH[1]}"
date="${BASH_REMATCH[2]}"
notes_raw="${BASH_REMATCH[3]}"

# Extract notes until the next version heading or [Unreleased] or end
notes="$(echo "$notes_raw" | sed -n -e '/^## \[/q;p')"

if [[ "$date" !=  $(date +"%Y-%m-%d") ]]; then
    echo "$date is not today!"
    exit 1
fi

tag="v$version"

if [ -n "$(git status --porcelain)" ]; then
    echo ". is not clean." >&2
    exit 1
fi

# Update version in pyproject.toml
perl -pi -e "s/(?<=^version = \").+?(?=\")/$version/gsm" pyproject.toml

# Update version in Cargo.toml
perl -pi -e "s/(?<=^version = \").+?(?=\")/$version/gsm" Cargo.toml

echo $"Test results:"
# Run Rust tests
cargo test

# Build the package
maturin develop

# Run Python tests
uv run pytest

echo $'\nDiff:'
git diff

echo $'\nRelease notes:'
echo "$notes"

read -e -p "Commit changes and push to origin? " should_push

if [ "$should_push" != "y" ]; then
    echo "Aborting"
    exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
    git commit -m "Update for $tag" -a
fi

git push

gh release create --target "$(git branch --show-current)" -t "$version" -n "$notes" "$tag"

git push --tags
