"""CLI OCTOPUS.

  python -m octopus report [--days 7] [--legacy-db agents/data/podalux.db]
  python -m octopus bench --models code,deepseek/flash [--tasks podalux.write_job] [--allow-paid] [--max-cost 0.05]
  python -m octopus models
  python -m octopus doctor
  python -m octopus worker [--once] [--max-tasks N]
  python -m octopus runtime [--business octopus] [--tick-every 300] [--once]   # démarrage durable unique
  python -m octopus status [--business X] [--json]                             # état observable du runtime
  python -m octopus night-shift [--repo .] [--hours 8] [--max-tasks 4] [--dry-run]
  python -m octopus night-stop | night-resume
  python -m octopus promotion --report data/night-shift-reports/<run>.json
  python -m octopus enqueue octopus octopus.cost_report
  python -m octopus tasks [--status queued] | cancel ID | ask | answer REQUEST_ID "texte"
  python -m octopus schedule octopus octopus.cost_report --every 86400 [--disable]
  python -m octopus events [--since ID]
  python -m octopus strategy add|list|show|move|link|mission|review|snapshot ...
  python -m octopus capability state|gaps|validate|acquire ...   # écarts de capacité et acquisition
"""
from __future__ import annotations

import argparse
import json
import math
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
    if args.task is not None:
        if args.max_tasks is not None:
            print("--task et --max-tasks sont incompatibles")
            return 2
        worker.load_handlers()
        try:
            result = worker.run_one(task_id=args.task)
        except ValueError as exc:
            print(f"task invalide : {exc}")
            return 2
        print(_dump(result) if result else "aucune tâche prête")
        return 0
    worker.load_handlers()
    if args.once:
        result = worker.run_one()
        print(_dump(result) if result else "aucune tâche prête")
        return 0
    worker.loop(poll_s=args.poll, max_tasks=args.max_tasks)
    return 0


def cmd_status(args) -> int:
    """État observable du runtime : objectifs, tâches, runs, attentes, échecs, suite, routage, coûts."""
    from . import status
    state = status.snapshot(args.business, limit=args.limit)
    if args.json:
        print(json.dumps(state, ensure_ascii=False, indent=1, default=str))
    else:
        print(status.render(state))
    return 0


def cmd_pursue(args) -> int:
    from . import supervisor
    if args.pause:
        if args.objective is None:
            raise ValueError("--pause exige --objective")
        supervisor.pause_pursuit(args.objective, business=args.business)
        return 0
    objective_id = supervisor.start_pursuit(args.goal, objective_id=args.objective, business=args.business)
    print(f"Activité {args.business}, objectif #{objective_id} : 0 EUR externe, economical borné, consultation, trois cycles maximum.")
    supervisor.run_pursuit(objective_id, business=args.business)
    return 0


def cmd_runtime(args) -> int:
    """Démarrage durable unique : superviseur autonome + worker, sans chorégraphie manuelle.

    Un seul `python -m octopus runtime` suffit ensuite à la boucle normale :
    objectif persistant -> mission -> preuve -> évaluation -> tâche suivante.
    """
    from . import supervisor, worker
    worker.load_handlers()
    if supervisor.TICK_KIND not in worker.HANDLERS or supervisor.WORK_KIND not in worker.HANDLERS:
        print(f"handlers manquants : {supervisor.TICK_KIND}, {supervisor.WORK_KIND}")
        return 2
    for missing in ("tick_every", "poll"):
        value = getattr(args, missing)
        if not math.isfinite(value) or value <= 0:
            print(f"runtime --{missing.replace('_', '-')} doit être fini et strictement positif")
            return 2
    if args.max_tasks is not None and args.max_tasks <= 0:
        print("runtime --max-tasks doit être strictement positif")
        return 2
    task_id = supervisor.bootstrap(args.business, tick_every_s=args.tick_every)
    print(f"[runtime] superviseur amorcé : tâche #{task_id} ({supervisor.TICK_KIND}) ; "
          f"tick toutes les {args.tick_every:g} s")
    print("[runtime] état observable : python -m octopus status")
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


def cmd_capability(args) -> int:
    """Écarts de capacité et acquisition bornée : ce qui manque, ce que cela exigerait, ce que cela vaut.

    Lecture seule par défaut : `state`, `gaps` et `validate` recalculent l'inventaire depuis les
    registres réels. `acquire` met en file une acquisition déjà décidée par une étude persistée ;
    il n'élargit aucune permission, ne dépense rien et n'exécute aucun effet externe.
    """
    from . import capability_acquisition as acquisition, supervisor
    action = args.capability_cmd or "state"
    if action == "acquire" and os.environ.get("OCTOPUS_WORKBENCH_READONLY") == "1":
        raise PermissionError("Mode consultation : acquisition désactivée")
    allowed = ({item.strip() for item in args.allowed.split(",") if item.strip()} if args.allowed is not None
               else set(supervisor.PURSUIT_TOOLS))
    if action == "state":
        names = args.capability or sorted(acquisition.CAPABILITY_TARGETS)
        snapshot = acquisition.snapshot(names, allowed_execution=allowed, business=args.business)
        if args.json:
            print(json.dumps(snapshot, ensure_ascii=False, indent=1, default=str))
            return 0
        for item in snapshot["capabilities"]:
            print(f"{item['capability']:22} {item['effective_state']:28} capable {str(item['capable']):5} "
                  f"autorisé {str(item['authorized']):5} acquisition {item['acquisition_state']}"
                  + (" ; acquisition périmée" if item["stale_acquisition"] else ""))
        print(f"autorité : {snapshot['authority']} ; inventaire recalculé, jamais mémorisé")
        return 0
    if action == "gaps":
        records = acquisition.recorded(args.business, args.objective)
        if not records:
            print("aucune étude d'acquisition persistée pour cet objectif")
            return 0
        for record in records:
            print(acquisition.render(record))
        return 0
    if action == "validate":
        result = acquisition.validate(args.capability, probe=args.probe)
        print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
        return 0 if result["capable"] else 2
    if action == "acquire":
        record = acquisition.latest_for_capability(args.business, args.capability,
                                                   objective_id=args.objective)
        if record is None:
            print(f"aucune étude persistée pour {args.capability} : aucune acquisition inventée")
            return 2
        started = acquisition.start_acquisition(args.business, args.capability, record, requested_by=args.by,
                                                objective_id=args.objective, hypothesis_id=args.hypothesis)
        print(json.dumps(started, ensure_ascii=False, indent=1))
        if started["status"] == "queued":
            print(f"tâche #{started['task_id']} en file : lancer un worker, puis « octopus capability validate »")
        return 0 if started["status"] == "queued" else 2
    return 2


def cmd_browser(args) -> int:
    """Espace de travail navigateur (backend Hermes agent-browser) : diagnostic et tranche humaine."""
    from . import browser_workspace
    if args.browser_cmd == "doctor":
        from agents import agent_browser
        info = agent_browser.availability()
        print(json.dumps(info, ensure_ascii=False, indent=1))
        if not info["ready"]:
            print("installer : python scripts/install_agent_browser.py ; python -m playwright install chromium")
            return 2
        if args.smoke:
            result = browser_workspace.smoke()
            print(json.dumps(result, ensure_ascii=False, indent=1))
            return 0 if result.get("ok") else 1
        return 0
    if args.browser_cmd == "actions":
        rows = browser_workspace.list_actions(args.business, status=args.status, task_id=args.task)
        for r in rows:
            print(f"#{r['id']:<5} {r['status']:9} {r['action']:14} tâche {r.get('task_id')} {r.get('page') or ''} "
                  f"{r.get('target') or ''} {r.get('reason') or ''}"[:200])
        if not rows:
            print("aucune action navigateur")
        return 0
    if args.browser_cmd == "resolve":
        result = browser_workspace.resolve(args.business, args.action_id, executed=args.outcome == "executed")
        print(f"action #{result['action_id']} : {result['status']}")
        return 0
    return 2


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
    p.add_argument("--task", type=int, default=None, help="executer une tache specifique par ID")
    p.add_argument("--max-tasks", type=int, default=None, help="nombre de taches strictement positif")
    p.add_argument("--poll", type=float, default=2.0, help="intervalle fini strictement positif, en secondes")
    p = sub.add_parser("night-shift", help="canary autonome borné, sans push ni merge vers main")
    p.add_argument("--repo", default=".", help="racine Git propre à utiliser comme base")
    p.add_argument("--plan", default=None, help="plan JSON; défaut: octopus/config/night_shift.json")
    p.add_argument("--hours", type=float, default=8.0)
    p.add_argument("--max-tasks", type=int, default=4)
    p.add_argument("--max-failures", type=int, default=2)
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("pursue", help="démarrer ou reprendre une activité dans les limites de pursuit")
    p.add_argument("--business", default="octopus", help="activité ; défaut : discovery autonome historique")
    entry = p.add_mutually_exclusive_group()
    entry.add_argument("--goal", help="mission libre facultative")
    entry.add_argument("--objective", type=int, help="objectif existant à reprendre")
    p.add_argument("--pause", action="store_true", help="suspendre cet objectif")
    p = sub.add_parser("runtime", help="démarrage durable unique : superviseur autonome + worker")
    p.add_argument("--business", default="octopus", help="business porteur du tick superviseur")
    p.add_argument("--tick-every", type=float, default=300.0,
                   help="intervalle du superviseur en secondes (défaut 300)")
    p.add_argument("--poll", type=float, default=2.0, help="intervalle de scrutation du worker, en secondes")
    p.add_argument("--max-tasks", type=int, default=None, help="nombre de tâches strictement positif")
    p.add_argument("--once", action="store_true", help="une seule tâche puis sortie (diagnostic)")
    p = sub.add_parser("status", help="état observable du runtime (objectifs, tâches, runs, attentes, coûts)")
    p.add_argument("--business", default=None, help="limiter aux objectifs de ce business")
    p.add_argument("--limit", type=int, default=8, help="nombre d'éléments par section (défaut 8)")
    p.add_argument("--json", action="store_true", help="sortie JSON plutôt que texte")
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
    p = sub.add_parser("capability", help="écarts de capacité, options d'acquisition et validation")
    csub = p.add_subparsers(dest="capability_cmd", required=True)
    st = csub.add_parser("state", help="inventaire recalculé : capable, autorisé, acquisition")
    st.add_argument("capability", nargs="*", default=None, help="identifiants (défaut : capacités connues)")
    st.add_argument("--business", default=None, help="relit aussi l'état d'acquisition persisté")
    st.add_argument("--allowed", default=None,
                    help="ensemble autorisé séparé par des virgules (défaut : outils de pursuit)")
    st.add_argument("--json", action="store_true")
    gp = csub.add_parser("gaps", help="études d'acquisition persistées d'un objectif")
    gp.add_argument("business")
    gp.add_argument("--objective", type=int, required=True)
    va = csub.add_parser("validate", help="validation déterministe d'une capacité (exit 2 si absente)")
    va.add_argument("capability")
    va.add_argument("--probe", action="store_true", help="passe aussi les sondes des ressources déclarées")
    ac = csub.add_parser("acquire", help="met en file une acquisition déjà décidée (idempotent)")
    ac.add_argument("business")
    ac.add_argument("capability")
    ac.add_argument("--objective", type=int, default=None)
    ac.add_argument("--hypothesis", type=int, default=None)
    ac.add_argument("--by", default="human")
    p.set_defaults(capability=None, business=None, allowed=None, json=False, probe=False, objective=None,
                   hypothesis=None, by="human")
    p = sub.add_parser("browser", help="espace de travail navigateur (backend Hermes agent-browser)")
    bsub = p.add_subparsers(dest="browser_cmd", required=True)
    x = bsub.add_parser("doctor", help="backend installé ? (--smoke : ouvre une vraie page locale)")
    x.add_argument("--smoke", action="store_true")
    x = bsub.add_parser("actions", help="actions navigateur à effet et leur état")
    x.add_argument("business")
    x.add_argument("--status", default=None)
    x.add_argument("--task", type=int, default=None)
    x = bsub.add_parser("resolve", help="l'humain tranche une action ambiguë (après vérification sur le site)")
    x.add_argument("business")
    x.add_argument("action_id", type=int)
    x.add_argument("outcome", choices=["executed", "not_executed"])
    from . import strategy_cli
    strategy_cli.add_parser(sub)
    args = parser.parse_args(argv)
    if args.cmd == "worker":
        if args.task is not None and args.max_tasks is not None:
            parser.error("--task et --max-tasks sont incompatibles")
        if args.task is not None and args.task <= 0:
            parser.error("worker --task doit etre strictement positif")
        if args.max_tasks is not None and args.max_tasks <= 0:
            parser.error("worker --max-tasks doit etre strictement positif")
        if not math.isfinite(args.poll) or args.poll <= 0:
            parser.error("worker --poll doit etre fini et strictement positif")
    commands = {"report": cmd_report, "bench": cmd_bench, "models": cmd_models, "doctor": cmd_doctor,
                "worker": cmd_worker, "runtime": cmd_runtime, "pursue": cmd_pursue, "status": cmd_status,
                "night-shift": cmd_night_shift, "night-stop": cmd_night_stop,
                "night-resume": cmd_night_resume, "promotion": cmd_promotion, "enqueue": cmd_enqueue,
                "tasks": cmd_tasks, "cancel": cmd_cancel,
                "ask": cmd_ask, "answer": cmd_answer, "schedule": cmd_schedule, "events": cmd_events,
                "businesses": cmd_businesses, "strategy": strategy_cli.run,
                "economy": strategy_cli.run_economy, "resources": cmd_resources,
                "capability": cmd_capability, "browser": cmd_browser}
    return commands[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
