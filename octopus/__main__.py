"""CLI OCTOPUS.

  python -m octopus report [--days 7] [--legacy-db agents/data/podalux.db]
  python -m octopus bench --models code,deepseek/flash [--tasks podalux.write_job] [--allow-paid] [--max-cost 0.05]
  python -m octopus models
  python -m octopus doctor
  python -m octopus worker [--once] [--max-tasks N]
  python -m octopus night-shift [--repo .] [--hours 8] [--max-tasks 4] [--dry-run]
  python -m octopus night-stop | night-resume
  python -m octopus promotion --report data/night-shift-reports/<run>.json
  python -m octopus enqueue podalux podalux.video_cycle --input '{"offer_id": "cash_devis_cgv01"}'
  python -m octopus tasks [--status queued] | cancel ID | ask | answer REQUEST_ID "texte"
  python -m octopus schedule octopus octopus.cost_report --every 86400 [--disable]
  python -m octopus events [--since ID]
  python -m octopus strategy add|list|show|move|link|mission|review|snapshot ...
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

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


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def cmd_worker(args) -> int:
    from . import worker
    worker.load_handlers()
    if args.once:
        result = worker.run_one()
        print(_dump(result) if result else "aucune tâche prête")
        return 0
    worker.loop(poll_s=args.poll, max_tasks=args.max_tasks)
    return 0


def cmd_night_shift(args) -> int:
    from . import night_shift

    try:
        plan = night_shift.load_plan(Path(args.plan).resolve() if args.plan else None)
        result = night_shift.run(
            Path(args.repo),
            plan,
            max_tasks=args.max_tasks,
            max_hours=args.hours,
            max_failures=args.max_failures,
            dry_run=args.dry_run,
        )
    except night_shift.NightShiftError as exc:
        print(f"night-shift refusé: {exc}")
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_night_stop(args) -> int:
    from . import night_shift

    print(f"arrêt night-shift demandé: {night_shift.request_stop()}")
    return 0


def cmd_night_resume(args) -> int:
    from . import night_shift

    removed = night_shift.clear_stop()
    print("arrêt night-shift levé" if removed else "aucun arrêt night-shift actif")
    return 0


def cmd_promotion(args) -> int:
    from . import promotion

    try:
        report = promotion.load_report(Path(args.report))
        manifest = promotion.build_manifest(report)
        manifest = promotion.verify_git(report, manifest)
    except promotion.PromotionError as exc:
        print(f"promotion refusée: {exc}")
        return 2
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def cmd_enqueue(args) -> int:
    from . import worker
    worker.load_handlers()
    if args.kind not in worker.HANDLERS:
        print(f"type de tâche inconnu : {args.kind} (connus : {', '.join(sorted(worker.HANDLERS))})")
        return 2
    task_id = worker.enqueue(args.business, args.kind, json.loads(args.input), priority=args.priority,
                             delay_s=args.delay)
    print(f"tâche #{task_id} en file")
    return 0


def cmd_tasks(args) -> int:
    from . import tasks
    for t in tasks.list_tasks(status=args.status, business=args.business, limit=args.limit):
        error = f"  erreur : {(t['error'] or '').splitlines()[0][:80]}" if t["error"] else ""
        print(f"#{t['id']:<5} {t['status']:14} {t['business']}/{t['kind']:24} tentatives {t['attempts']}/{t['max_attempts']}"
              f"  {_dump(t['output'])[:80] if t['output'] is not None else ''}{error}")
    return 0


def cmd_cancel(args) -> int:
    from . import tasks
    print(tasks.cancel(args.id))
    return 0


def cmd_ask(args) -> int:
    from . import tasks
    pending = tasks.pending_human_requests()
    for r in pending:
        print(f"#{r['id']} (tâche #{r['task_id']}, {r['business']}) {r['question']}")
    if not pending:
        print("aucune demande en attente")
    return 0


def cmd_answer(args) -> int:
    from . import tasks
    print(f"réponse enregistrée ; tâche #{tasks.answer(args.id, args.text)} remise en file")
    return 0


def cmd_schedule(args) -> int:
    from . import tasks, worker
    worker.load_handlers()
    if args.kind not in worker.HANDLERS:
        print(f"type de tâche inconnu : {args.kind}")
        return 2
    spec = worker.HANDLERS[args.kind]
    sid = tasks.schedule(args.business, args.kind, args.every, json.loads(args.input), enabled=not args.disable,
                         budget_usd=spec.budget_usd)
    print(f"planification #{sid} : {args.kind} toutes les {args.every:g} s ({'désactivée' if args.disable else 'active'})")
    for s in tasks.schedules():
        print(f"  #{s['id']} {s['business']}/{s['kind']} toutes les {s['interval_s']:g} s, "
              f"{'active' if s['enabled'] else 'désactivée'}")
    return 0


def cmd_events(args) -> int:
    from . import tasks
    for e in tasks.events(since_id=args.since, limit=args.limit):
        print(f"{e['id']:<6} tâche #{e['task_id']} {e['type']:22} {_dump(e['data'])[:120]}")
    return 0


def cmd_businesses(args) -> int:
    from . import businesses
    print(businesses.render(businesses.overview(args.days), args.days))
    return 0


def cmd_resources(args) -> int:
    """Inventaire des ressources reelles : ce qui existe, ce qui repond, ce qui manque."""
    from . import resources, tasks
    action = args.resources_cmd or "list"
    if action == "sync":
        result = resources.sync()
        print(f"declarees : {len(result['declared'])} ; ajoutees : {', '.join(result['added']) or 'aucune'} ; "
              f"mises a jour : {', '.join(result['updated']) or 'aucune'}")
        if result["unknown"]:
            print("en base mais plus declarees : " + ", ".join(result["unknown"]))
        return 0
    if action == "check":
        rows = [resources.check(args.key)] if args.key else resources.check_all()
        print(resources.render(rows))
        return 0
    if action == "show":
        item = resources.get(args.key)
        if item is None:
            print(f"ressource inconnue : {args.key}")
            return 2
        print(json.dumps(item, ensure_ascii=False, indent=1, default=str))
        return 0
    if action == "request":
        task_id = resources.request(args.key, args.need, args.question, created_by="human")
        print(f"demande en file : tache #{task_id} (lancer un worker, puis repondre avec « octopus answer »)")
        return 0
    if action == "grant":
        item = resources.update(args.key, actor="human", access=args.access,
                                state=args.state, notes=args.notes)
        print(f"{item['key']} : acces {item['access']}, etat {item['state']}")
        return 0
    if action == "blocked":
        rows = resources.blocked()
        for r in rows:
            needs = (" ; humain : " + ", ".join(r["needs"])) if r["needs"] else ""
            print(f"{r['key']:22} {r['reason']}{needs}")
        if not rows:
            print("aucune ressource bloquee")
        return 0
    if action == "overview":
        print(json.dumps(resources.overview(), ensure_ascii=False, indent=1))
        return 0
    print(resources.render(resources.list_resources(state=args.state, kind=args.kind)))
    return 0


def main(argv: list[str] | None = None) -> int:
    _safe_console()
    parser = argparse.ArgumentParser(prog="octopus", description="OCTOPUS : expériences économiques supervisées, travail et preuves")
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
    p = sub.add_parser("worker", help="execute les taches de la file")
    p.add_argument("--once", action="store_true", help="une seule tache puis sortie")
    p.add_argument("--max-tasks", type=int, default=None)
    p.add_argument("--poll", type=float, default=2.0)
    p = sub.add_parser("night-shift", help="canary autonome borné, sans push ni merge vers main")
    p.add_argument("--repo", default=".", help="racine Git propre à utiliser comme base")
    p.add_argument("--plan", default=None, help="plan JSON; défaut: octopus/config/night_shift.json")
    p.add_argument("--hours", type=float, default=8.0)
    p.add_argument("--max-tasks", type=int, default=4)
    p.add_argument("--max-failures", type=int, default=2)
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("night-stop", help="demande l'arrêt immédiat du night-shift/Kilo actif")
    sub.add_parser("night-resume", help="lève le kill switch night-shift")
    p = sub.add_parser("promotion", help="valide un rapport night-shift avant revue humaine")
    p.add_argument("--report", required=True, help="chemin du rapport JSON night-shift")
    p = sub.add_parser("enqueue", help="ajoute une tache")
    p.add_argument("business")
    p.add_argument("kind")
    p.add_argument("--input", default="{}")
    p.add_argument("--priority", type=int, default=0)
    p.add_argument("--delay", type=float, default=0)
    p = sub.add_parser("tasks", help="liste les taches")
    p.add_argument("--status", default=None)
    p.add_argument("--business", default=None)
    p.add_argument("--limit", type=int, default=30)
    p = sub.add_parser("cancel", help="annule une tache")
    p.add_argument("id", type=int)
    sub.add_parser("ask", help="demandes humaines en attente")
    p = sub.add_parser("answer", help="repond a une demande humaine")
    p.add_argument("id", type=int)
    p.add_argument("text")
    p = sub.add_parser("schedule", help="planifie une tache recurrente")
    p.add_argument("business")
    p.add_argument("kind")
    p.add_argument("--every", type=float, required=True, help="intervalle en secondes (>= 60)")
    p.add_argument("--input", default="{}")
    p.add_argument("--disable", action="store_true")
    p = sub.add_parser("events", help="evenements recents")
    p.add_argument("--since", type=int, default=0)
    p.add_argument("--limit", type=int, default=100)
    p = sub.add_parser("resources", help="inventaire des ressources reelles (etat, sondes, blocages)")
    rsub = p.add_subparsers(dest="resources_cmd")
    lst = rsub.add_parser("list", help="inventaire")
    lst.add_argument("--state", default=None)
    lst.add_argument("--kind", default=None)
    rsub.add_parser("sync", help="aligne la base sur resources.toml")
    chk = rsub.add_parser("check", help="passe les sondes")
    chk.add_argument("key", nargs="?", default=None)
    shw = rsub.add_parser("show", help="detail d'une ressource")
    shw.add_argument("key")
    req = rsub.add_parser("request", help="demande a l'humain de creer/connecter/autoriser")
    req.add_argument("key")
    req.add_argument("need")
    req.add_argument("question")
    grt = rsub.add_parser("grant", help="accorde un acces ou fixe un etat constate")
    grt.add_argument("key")
    grt.add_argument("--access", default=None)
    grt.add_argument("--state", default=None)
    grt.add_argument("--notes", default=None)
    rsub.add_parser("blocked", help="ce qui manque pour avancer")
    rsub.add_parser("overview", help="resume chiffre")
    p.set_defaults(state=None, kind=None, key=None)

    p = sub.add_parser("businesses", help="tableau de bord par activite")
    p.add_argument("--days", type=int, default=7)
    from .media import cli as media_cli
    media_cli.add_parser(sub)
    from . import strategy_cli
    strategy_cli.add_parser(sub)
    args = parser.parse_args(argv)
    commands = {"report": cmd_report, "bench": cmd_bench, "models": cmd_models, "doctor": cmd_doctor,
                "worker": cmd_worker, "night-shift": cmd_night_shift, "night-stop": cmd_night_stop,
                "night-resume": cmd_night_resume, "promotion": cmd_promotion, "enqueue": cmd_enqueue,
                "tasks": cmd_tasks, "cancel": cmd_cancel,
                "ask": cmd_ask, "answer": cmd_answer, "schedule": cmd_schedule, "events": cmd_events,
                "video": media_cli.run, "businesses": cmd_businesses, "strategy": strategy_cli.run,
                "economy": strategy_cli.run_economy, "resources": cmd_resources}
    return commands[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
