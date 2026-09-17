"""CLI OCTOPUS.

  python -m octopus report [--days 7] [--legacy-db agents/data/podalux.db]
  python -m octopus bench --models code,deepseek/flash [--tasks podalux.write_job] [--allow-paid] [--max-cost 0.05]
  python -m octopus models
  python -m octopus doctor
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys

from . import __version__, catalog, enabled, journal, llm, paths, report


def _safe_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def cmd_report(args) -> int:
    legacy = report.reprice_legacy(args.legacy_db) if args.legacy_db else None
    print(report.render(report.build(args.days), legacy))
    return 0


def cmd_bench(args) -> int:
    from . import bench
    result = bench.run_bench(args.suite, [m.strip() for m in args.models.split(",") if m.strip()],
                             tasks=[t.strip() for t in args.tasks.split(",")] if args.tasks else None,
                             repeats=args.repeats, allow_paid=args.allow_paid, max_cost_usd=args.max_cost)
    print()
    print(bench.format_matrix(result["matrix"]))
    print("Fichiers : " + ", ".join(result["files"]))
    return 0


def cmd_models(args) -> int:
    cat = catalog.load()
    for model_id, model in cat.raw["models"].items():
        ok, why = llm.provider_status(model["provider"], cat.provider(model["provider"]))
        print(f"{model_id:24} {model['cost_class']:10} {','.join(model.get('capabilities', [])):28} "
              f"{'disponible' if ok else 'indisponible'} ({why})")
    rules = cat.evidence_rules()
    print("\nPreuves du banc par tache :")
    for task in cat.raw.get("tasks", {}):
        for model_id in cat.raw["models"]:
            proof = journal.evidence(task, model_id, rules)
            if "n" in proof:
                print(f"  {task:24} {model_id:24} {proof['reason']}")
    return 0


def _ram_gb() -> float | None:
    if sys.platform == "win32":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.ullTotalPhys / 2**30
        return None
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            return int(fh.readline().split()[1]) / 2**20
    except OSError:
        return None


def cmd_doctor(args) -> int:
    print(f"OCTOPUS {__version__} - Python {platform.python_version()} ({sys.executable}) - {platform.platform()}")
    print(f"Actif : {'oui' if enabled() else 'non (OCTOPUS=off)'} ; profil : {os.environ.get('OCTOPUS_PROFILE') or catalog.load().default_profile}")
    print(f"Journal : {paths.journal_path()}")
    conn = journal.connect()
    n = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    conn.close()
    print(f"  accessible, {n} appels journalises")
    ram = _ram_gb()
    print(f"CPU logiques : {os.cpu_count()} ; RAM : {ram:.1f} Go" if ram else f"CPU logiques : {os.cpu_count()}")
    if shutil.which("nvidia-smi"):
        try:
            gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
            print(f"GPU : {gpu}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"GPU : nvidia-smi a echoue ({exc})")
    else:
        print("GPU : nvidia-smi introuvable")
    print("Fournisseurs :")
    cat = catalog.load()
    for name, provider in cat.raw["providers"].items():
        ok, why = llm.provider_status(name, provider)
        print(f"  {name:12} {provider['kind']:6} {'OK' if ok else '--'} {why}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _safe_console()
    parser = argparse.ArgumentParser(prog="octopus", description="OCTOPUS : journal, passerelle LLM, banc d'evaluation")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("report", help="couts et appels LLM")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--legacy-db", default=None, help="relire aussi la table costs historique (lecture seule)")
    p = sub.add_parser("bench", help="banc d'evaluation")
    p.add_argument("--suite", default="agents.evals")
    p.add_argument("--models", required=True, help="liste separee par des virgules, ex. code,deepseek/flash")
    p.add_argument("--tasks", default=None)
    p.add_argument("--repeats", type=int, default=None)
    p.add_argument("--allow-paid", action="store_true")
    p.add_argument("--max-cost", type=float, default=0.05, help="plafond du banc en USD (defaut 0.05)")
    sub.add_parser("models", help="catalogue, disponibilite et preuves")
    sub.add_parser("doctor", help="verification de l'installation")
    args = parser.parse_args(argv)
    return {"report": cmd_report, "bench": cmd_bench, "models": cmd_models, "doctor": cmd_doctor}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
