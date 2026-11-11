"""The package as installed: typed, one runtime dependency, stdlib-only webhook verification."""

from __future__ import annotations

import ast
import sys
from importlib import metadata, resources
from pathlib import Path

import runearc


def test_version_and_exports_match_the_distribution() -> None:
    assert runearc.__version__ == runearc.VERSION == "0.1.0"
    assert metadata.version("runearc") == runearc.VERSION
    for name in runearc.__all__:
        assert hasattr(runearc, name)


def test_ships_the_py_typed_marker() -> None:
    assert resources.files("runearc").joinpath("py.typed").is_file()


def test_has_a_single_runtime_dependency_on_httpx() -> None:
    requires = metadata.requires("runearc") or []
    names = {
        r.split(";")[0].replace(">", " ").replace("<", " ").replace("=", " ").split()[0]
        for r in requires
    }
    assert names == {"httpx"}


def test_the_license_is_mit_and_shipped() -> None:
    meta = metadata.metadata("runearc")
    assert meta["License-Expression"] == "MIT"
    assert not any(c.startswith("License ::") for c in meta.get_all("Classifier") or [])
    assert any("LICENSE" in str(f) for f in metadata.files("runearc") or [])


def test_webhook_verification_imports_only_the_standard_library() -> None:
    source = Path(runearc.__file__).with_name("webhooks.py").read_text()
    stdlib = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else None
    for node in ast.walk(ast.parse(source)):
        modules = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules = [node.module]
        for module in modules:
            top = module.split(".")[0]
            assert top != "httpx"
            if stdlib is not None:
                assert top in stdlib or top == "__future__"
