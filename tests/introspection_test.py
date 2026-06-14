from __future__ import annotations

import ast
from pathlib import Path

import maxminddb_rust


STUB_PATH = Path(__file__).resolve().parents[1] / "maxminddb_rust.pyi"


def _stub_tree() -> ast.Module:
    return ast.parse(STUB_PATH.read_text(encoding="utf-8"))


def _stub_all(tree: ast.Module) -> list[str]:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            break
        return [
            element.value
            for element in node.value.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
    raise AssertionError("__all__ not found in maxminddb_rust.pyi")


def _stub_class_member_names(tree: ast.Module, class_name: str) -> set[str]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            members = set()
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name != "__init__":
                    members.add(child.name)
                elif isinstance(child, ast.AnnAssign) and isinstance(
                    child.target, ast.Name
                ):
                    members.add(child.target.id)
            return members
    raise AssertionError(f"{class_name} not found in maxminddb_rust.pyi")


def _literal_value(annotation: ast.expr) -> int | None:
    if not isinstance(annotation, ast.Subscript):
        return None
    if not isinstance(annotation.value, ast.Name) or annotation.value.id != "Literal":
        return None

    value = annotation.slice
    index_type = getattr(ast, "Index", None)
    if index_type is not None and isinstance(value, index_type):
        value = value.value
    if isinstance(value, ast.Constant) and isinstance(value.value, int):
        return value.value
    return None


def _stub_mode_values(tree: ast.Module) -> dict[str, int]:
    values = {}
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name) or not node.target.id.startswith(
            "MODE_"
        ):
            continue
        value = _literal_value(node.annotation)
        if value is not None:
            values[node.target.id] = value
    return values


def test_public_types_report_public_module() -> None:
    assert maxminddb_rust.Reader.__module__ == "maxminddb_rust"
    assert maxminddb_rust.Metadata.__module__ == "maxminddb_rust"
    assert maxminddb_rust.InvalidDatabaseError.__module__ == "maxminddb_rust"


def test_runtime_exports_match_stub_exports() -> None:
    stub_exports = _stub_all(_stub_tree())

    assert maxminddb_rust.__all__ == stub_exports
    assert {name for name in dir(maxminddb_rust) if not name.startswith("_")} >= set(
        stub_exports
    )


def test_runtime_mode_values_match_stub_literals() -> None:
    for name, expected in _stub_mode_values(_stub_tree()).items():
        assert getattr(maxminddb_rust, name) == expected


def test_runtime_reader_members_match_stub_members() -> None:
    reader_members = _stub_class_member_names(_stub_tree(), "Reader")
    runtime_members = set(dir(maxminddb_rust.Reader))

    assert reader_members <= runtime_members


def test_runtime_metadata_members_match_stub_members() -> None:
    metadata_members = _stub_class_member_names(_stub_tree(), "Metadata")
    runtime_members = set(dir(maxminddb_rust.Metadata))

    assert metadata_members <= runtime_members
