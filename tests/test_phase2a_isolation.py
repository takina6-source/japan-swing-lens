from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
PACKAGE_ROOT = ROOT / "engine" / "state_machine"
FORBIDDEN = "engine.database"


def _is_forbidden(module: str) -> bool:
    return module == FORBIDDEN or module.startswith(f"{FORBIDDEN}.")


def _from_import_targets(node: ast.ImportFrom, module_name: str) -> list[str]:
    if node.level:
        package_parts = module_name.split(".")[:-1]
        keep = len(package_parts) - (node.level - 1)
        base_parts = package_parts[: max(0, keep)]
        if node.module:
            base_parts.extend(node.module.split("."))
        base = ".".join(base_parts)
    else:
        base = node.module or ""
    if node.module:
        return [base, *(f"{base}.{alias.name}" for alias in node.names)]
    return [f"{base}.{alias.name}".strip(".") for alias in node.names]


def test_state_machine_never_imports_product_database():
    violations: list[str] = []
    files = sorted(PACKAGE_ROOT.glob("*.py"))
    assert files, "engine.state_machine package has no Python files"

    for path in files:
        module_name = f"engine.state_machine.{path.stem}"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        importlib_aliases = {"importlib"}
        dynamic_import_names = {"__import__", "import_module"}

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "importlib":
                        importlib_aliases.add(alias.asname or alias.name)
                    if _is_forbidden(alias.name):
                        violations.append(f"{path}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module == "importlib":
                    for alias in node.names:
                        if alias.name == "import_module":
                            dynamic_import_names.add(alias.asname or alias.name)
                for target in _from_import_targets(node, module_name):
                    if _is_forbidden(target):
                        violations.append(f"{path}:{node.lineno}: from {target} import ...")
            elif isinstance(node, ast.Call):
                is_dynamic_import = (
                    isinstance(node.func, ast.Name)
                    and node.func.id in dynamic_import_names
                ) or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "import_module"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in importlib_aliases
                )
                if is_dynamic_import:
                    if not node.args or not isinstance(node.args[0], ast.Constant):
                        violations.append(
                            f"{path}:{node.lineno}: non-literal dynamic import is not allowed"
                        )
                    elif isinstance(node.args[0].value, str) and _is_forbidden(
                        node.args[0].value
                    ):
                        violations.append(
                            f"{path}:{node.lineno}: dynamic import {node.args[0].value}"
                        )

    runtime_probe = """
import importlib
import pkgutil
import sys
import engine.state_machine as package

for item in pkgutil.walk_packages(package.__path__, package.__name__ + '.'):
    importlib.import_module(item.name)

loaded = sorted(
    name for name in sys.modules
    if name == 'engine.database' or name.startswith('engine.database.')
)
if loaded:
    raise SystemExit('forbidden transitive imports: ' + ', '.join(loaded))
"""
    probe = subprocess.run(
        [sys.executable, "-c", runtime_probe],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if probe.returncode:
        violations.append(
            "runtime import probe failed: "
            + (probe.stderr.strip() or probe.stdout.strip() or str(probe.returncode))
        )

    assert not violations, "\n".join(violations)
