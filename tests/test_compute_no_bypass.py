import ast

import pytest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (ROOT / "octopus", ROOT / "agents", ROOT / "ops")
DIRECT_CLIENT_MODULES = {"octopus.salad", "octopus.gpuai"}
DIRECT_CLIENT_NAMES = {"SaladClient", "GPUAIClient"}
ALLOWED_DIRECT_CLIENT_IMPORTS = {
    Path("octopus/salad.py"),
    Path("octopus/gpuai.py"),
    Path("ops/compute_watchdog.py"),
}


def _production_trees():
    for root in PRODUCTION_ROOTS:
        for path in root.rglob("*.py"):
            yield path.relative_to(ROOT), ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(relative: Path, node: ast.AST) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if not isinstance(node, ast.ImportFrom):
        return set()
    if node.level:
        package = relative.with_suffix("").parts[:-1]
        up = node.level - 1
        if up > len(package):
            return set()
        base = package[: len(package) - up]
    else:
        base = ()
    module = tuple(part for part in (node.module or "").split(".") if part)
    prefix = ".".join((*base, *module))
    imported = {prefix} if prefix else set()
    for alias in node.names:
        candidate = ".".join(part for part in (prefix, alias.name) if part)
        if candidate:
            imported.add(candidate)
    return imported


def test_paid_compute_clients_cannot_be_imported_by_business_code():
    violations = []
    for relative, tree in _production_trees():
        if relative in ALLOWED_DIRECT_CLIENT_IMPORTS:
            continue
        for node in ast.walk(tree):
            modules = _imported_modules(relative, node)
            direct_modules = DIRECT_CLIENT_MODULES.intersection(modules)
            direct_names = DIRECT_CLIENT_NAMES.intersection(
                alias.name for alias in node.names
            ) if isinstance(node, (ast.Import, ast.ImportFrom)) else set()
            if direct_modules or direct_names:
                violations.append(
                    f"{relative}:{getattr(node, 'lineno', '?')} imports "
                    f"{sorted(direct_modules or direct_names)}"
                )
    assert violations == []


def test_paid_provider_create_is_only_called_by_guarded_compute_manager():
    violations = []
    allowed = Path("octopus/compute_finance.py")
    for relative, tree in _production_trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "create":
                continue
            receiver = node.func.value
            is_direct_client = (
                isinstance(receiver, ast.Call)
                and isinstance(receiver.func, ast.Name)
                and receiver.func.id in DIRECT_CLIENT_NAMES
            )
            is_guarded_provider_call = isinstance(receiver, ast.Name) and receiver.id == "provider"
            if (is_direct_client or is_guarded_provider_call) and relative != allowed:
                violations.append(f"{relative}:{node.lineno}")
    assert violations == []


@pytest.mark.parametrize("source", [
    "from .salad import SaladClient",
    "from .gpuai import GPUAIClient",
    "from octopus.salad import SaladClient",
    "from octopus.gpuai import GPUAIClient",
    "import octopus.salad",
    "import octopus.gpuai",
])
def test_import_resolver_detects_direct_paid_clients(source):
    node = ast.parse(source).body[0]
    modules = _imported_modules(Path("octopus/business.py"), node)
    assert modules & DIRECT_CLIENT_MODULES
