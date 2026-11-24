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
    msg = "__all__ not found in maxminddb_rust.pyi"
    raise AssertionError(msg)


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
    msg = f"{class_name} not found in maxminddb_rust.pyi"
    raise AssertionError(msg)


def _stub_enum_members(tree: ast.Module, class_name: str) -> dict[str, int]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.targets[0].id: child.value.value
                for child in node.body
                if isinstance(child, ast.Assign)
                and isinstance(child.targets[0], ast.Name)
                and isinstance(child.value, ast.Constant)
                and isinstance(child.value.value, int)
            }
    msg = f"{class_name} not found in maxminddb_rust.pyi"
    raise AssertionError(msg)


def _literal_mode_member(annotation: ast.expr) -> str | None:
    """Return X for a ``Literal[Mode.X]`` annotation."""
    if not isinstance(annotation, ast.Subscript):
        return None
    if not isinstance(annotation.value, ast.Name) or annotation.value.id != "Literal":
        return None

    value = annotation.slice
    if (
        isinstance(value, ast.Attribute)
        and isinstance(value.value, ast.Name)
        and value.value.id == "Mode"
    ):
        return value.attr
    return None


def _stub_mode_constants(tree: ast.Module) -> dict[str, str]:
    constants = {}
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name) or not node.target.id.startswith(
            "MODE_"
        ):
            continue
        member = _literal_mode_member(node.annotation)
        if member is not None:
            constants[node.target.id] = member
    return constants


def test_public_types_report_public_module() -> None:
    assert maxminddb_rust.Reader.__module__ == "maxminddb_rust"
    assert maxminddb_rust.Metadata.__module__ == "maxminddb_rust"
    assert maxminddb_rust.InvalidDatabaseError.__module__ == "maxminddb_rust"


def test_runtime_exports_match_stub_exports() -> None:
    stub_exports = _stub_all(_stub_tree())

    assert maxminddb_rust.__all__ == stub_exports
    assert set(dir(maxminddb_rust)) >= set(stub_exports)


def test_runtime_mode_enum_matches_stub() -> None:
    members = _stub_enum_members(_stub_tree(), "Mode")

    assert members
    assert {member.name: member.value for member in maxminddb_rust.Mode} == members
    assert maxminddb_rust.Mode.__module__ == "maxminddb_rust"


def test_runtime_mode_constants_match_stub_literals() -> None:
    constants = _stub_mode_constants(_stub_tree())

    assert len(constants) == len(_stub_enum_members(_stub_tree(), "Mode"))
    for name, member in constants.items():
        assert getattr(maxminddb_rust, name) is maxminddb_rust.Mode[member]


def test_version_is_a_string() -> None:
    assert isinstance(maxminddb_rust.__version__, str)
    assert maxminddb_rust.__version__


def test_runtime_reader_members_match_stub_members() -> None:
    reader_members = _stub_class_member_names(_stub_tree(), "Reader")
    runtime_members = set(dir(maxminddb_rust.Reader))

    assert reader_members <= runtime_members


def test_runtime_metadata_members_match_stub_members() -> None:
    metadata_members = _stub_class_member_names(_stub_tree(), "Metadata")
    runtime_members = set(dir(maxminddb_rust.Metadata))

    assert metadata_members <= runtime_members
