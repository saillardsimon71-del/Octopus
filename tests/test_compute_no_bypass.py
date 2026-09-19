import ast
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


def test_paid_compute_clients_cannot_be_imported_by_business_code():
    violations = []
    for relative, tree in _production_trees():
        if relative in ALLOWED_DIRECT_CLIENT_IMPORTS:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in DIRECT_CLIENT_MODULES:
                imported = DIRECT_CLIENT_NAMES.intersection(alias.name for alias in node.names)
                if imported:
                    violations.append(f"{relative}:{node.lineno} imports {sorted(imported)}")
            elif isinstance(node, ast.Import):
                imported = DIRECT_CLIENT_MODULES.intersection(alias.name for alias in node.names)
                if imported:
                    violations.append(f"{relative}:{node.lineno} imports {sorted(imported)}")
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
