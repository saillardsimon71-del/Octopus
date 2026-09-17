"""Point d'entrée CLI du groupe d'agents Podalux.

Usage :
  python -m agents.run cycle [--offer <offer_id>]
  python -m agents.run batch
  python -m agents.run publish <offer_id> [--real]
  python -m agents.run status
  python -m agents.run report
  python -m agents.run browser <url>
"""
from __future__ import annotations

import argparse
import json
import sqlite3

from . import config, db
from .cycle import CycleBusy, run_cycle


def cmd_cycle(offer_id=None):
    db.init_db()
    try:
        res = run_cycle(offer_id)
    except CycleBusy:
        print(f"refusé : un autre cycle tourne déjà ({db.run_lock_holder()})")
        raise SystemExit(3)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_batch():
    from .cycle import already_produced
    from .agents import CATALOG
    db.init_db()
    todo = [k for k in CATALOG if k not in already_produced()]
    if not todo:
        print("toutes les offres du catalogue sont déjà produites.")
        return
    print(f"BATCH : {len(todo)} offres à produire : {todo}")
    for oid in todo:
        print(f"\n=== {oid} ===")
        res = run_cycle(offer_id=oid)
        led = res.get("ledger", {})
        print(f"  -> score {led.get('score')}/35 · warm {led.get('warm_pass')} · "
              f"{res.get('orbit', {}).get('decision')} (itérations {len(res.get('iterations', []))})")


def cmd_publish(offer_id, real=False):
    from .publish import publish
    db.init_db()
    print(json.dumps(publish(offer_id, dry_run=not real), ensure_ascii=False, indent=2))


def cmd_status():
    db.init_db()
    print(f"coût total : ${db.total_cost():.4f}  (budget cycle : ${config.CYCLE_BUDGET_USD})")
    print(f"verrou de production : {db.run_lock_holder() or 'libre'}")
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    for row in conn.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 10"):
        print(f"[{row['agent']}] {row['decision']}")
    conn.close()


def cmd_report():
    db.init_db()
    print(f"coût total : ${db.total_cost():.4f}")
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    print("\n--- métriques QC (rubric /35) ---")
    for row in conn.execute("SELECT offer_id, score, humanite, verdict FROM metrics ORDER BY id"):
        print(f"  {row['offer_id']}: {row['score']}/35 · humanité {row['humanite']}/5 · {row['verdict']}")
    print("\n--- métriques J+1 (post-publication) ---")
    j1 = db.j1_summary()
    if not j1:
        print("  (aucune métrique J+1 enregistrée)")
    for r in j1:
        print(f"  {r['offer_id']}: {r['v']} vues · {r['l']} likes · {r['c']} conversions · {r['r']:.2f} €")
    print("\n--- décisions récentes ---")
    for row in conn.execute("SELECT agent, decision FROM decisions ORDER BY id DESC LIMIT 8"):
        print(f"  [{row['agent']}] {row['decision']}")
    conn.close()


def cmd_browser(url):
    from .browser import new_browser
    b = new_browser(headless=True)
    try:
        b.goto(url)
        print("URL:", b.url())
        print("SNAPSHOT:", b.snapshot()[:1000])
        print("LIENS:", b.links()[:8])
        r = b.see("Décris cette page en 2 phrases, et dis ce qu'on peut y faire.")
        print("VISION:", r)
    finally:
        b.stop()


def cmd_gui():
    from .gui.app import main as gui_main
    gui_main()


def cmd_handoff(action, hid=None, text=None):
    db.init_db()
    if action == "list":
        for h in db.pending_handoffs():
            print(f"#{h['id']} [{h['agent']}] {h['question']}")
        if not db.pending_handoffs():
            print("(aucune demande en attente)")
    elif action == "answer":
        if not hid or not text:
            print("usage : handoff answer <id> <texte>")
            return
        db.answer(int(hid), text)
        print(f"réponse #{hid} envoyée")


def cmd_goal(goal_text):
    from .runtime import run_agent
    db.init_db()
    r = run_agent("ORBIT", goal_text, max_steps=8)
    print(json.dumps(r, ensure_ascii=False, indent=2))


def cmd_mission(goal_text):
    from .runtime import run_mission
    db.init_db()
    r = run_mission(goal_text, max_steps_per_agent=8)
    print(json.dumps(r, ensure_ascii=False, indent=2))


def cmd_msg(role, text):
    from .runtime import run_agent
    db.init_db()
    r = run_agent(role.upper(), text, max_steps=8, conversational=True)
    print(json.dumps(r, ensure_ascii=False, indent=2))


def cmd_browse_open():
    """Ouvre le navigateur Chromium (visible, persistant) et le laisse ouvert."""
    import time
    from .browser import get_shared_browser, _cleanup_shared
    db.init_db()
    b = get_shared_browser()
    b.goto("https://www.google.com")
    db.post("HUMAN", "navigateur ouvert — connecte-toi si besoin, puis ferme la fenêtre")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        _cleanup_shared()


def main():
    parser = argparse.ArgumentParser(prog="podalux", description="Groupe d'agents Podalux")
    sub = parser.add_subparsers(dest="cmd")
    p_cycle = sub.add_parser("cycle", help="lance un cycle complet (produit une vidéo)")
    p_cycle.add_argument("--offer", default=None, help="forcer un offer_id")
    sub.add_parser("batch", help="cycle pour toutes les offres non produites")
    p_pub = sub.add_parser("publish", help="plan de publication (dry-run par défaut)")
    p_pub.add_argument("offer_id")
    p_pub.add_argument("--real", action="store_true", help="upload réel (nécessite validation)")
    sub.add_parser("status", help="coûts et dernières décisions")
    sub.add_parser("doctor", help="diagnostic avant un cycle réel (rien de payant ni de lourd)")
    sub.add_parser("report", help="tableau de bord (coûts + QC + J+1)")
    sub.add_parser("gui", help="ouvre l'interface graphique")
    p_browser = sub.add_parser("browser", help="teste le navigateur (navigation + vision)")
    p_browser.add_argument("url", help="URL à ouvrir")
    p_h = sub.add_parser("handoff", help="gérer les demandes humaines (terminal)")
    p_h.add_argument("action", choices=["list", "answer"])
    p_h.add_argument("hid", nargs="?", default=None)
    p_h.add_argument("text", nargs="?", default=None)
    p_goal = sub.add_parser("goal", help="donne un objectif en langage naturel au groupe")
    p_goal.add_argument("text", nargs="+", help="l'objectif")
    p_mission = sub.add_parser("mission", help="objectif multi-agents (ORBIT planifie + délègue)")
    p_mission.add_argument("text", nargs="+", help="l'objectif")
    p_msg = sub.add_parser("msg", help="message à un agent (@ROLE) → réponse directe")
    p_msg.add_argument("role", help="rôle cible (ORBIT, SOUT, …)")
    p_msg.add_argument("text", nargs="+", help="le message")
    sub.add_parser("browse-open", help="ouvre le navigateur Chromium (visible) et le laisse ouvert")
    args = parser.parse_args()

    if args.cmd == "cycle":
        cmd_cycle(args.offer)
    elif args.cmd == "batch":
        cmd_batch()
    elif args.cmd == "publish":
        cmd_publish(args.offer_id, real=args.real)
    elif args.cmd == "status":
        cmd_status()
    elif args.cmd == "doctor":
        from .doctor import render, run_checks
        text, code = render(run_checks())
        print(text)
        raise SystemExit(code)
    elif args.cmd == "report":
        cmd_report()
    elif args.cmd == "gui":
        cmd_gui()
    elif args.cmd == "browser":
        cmd_browser(args.url)
    elif args.cmd == "handoff":
        cmd_handoff(args.action, args.hid, args.text)
    elif args.cmd == "goal":
        cmd_goal(" ".join(args.text))
    elif args.cmd == "mission":
        cmd_mission(" ".join(args.text))
    elif args.cmd == "msg":
        cmd_msg(args.role, " ".join(args.text))
    elif args.cmd == "browse-open":
        cmd_browse_open()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
