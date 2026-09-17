"""Sous-commande `python -m octopus strategy` : boucle stratégique persistée sans la GUI.

  strategy add objective atelier "Premier client" --by human --set statement="Signer un atelier"
  strategy add hypothesis atelier "Essai gratuit" --by human --parent 1 --set statement="..."
  strategy list experiment atelier [--status running]
  strategy show experiment 3 atelier
  strategy move experiment 3 atelier completed --by human --outcome refutes --result "0 réponse"
  strategy link atelier evidence 4 experiment 3 informs
  strategy mission atelier "Préparer la liste" --experiment 3     (tâche orbit.mission : worker requis)
  strategy review atelier [--in 604800]                            (tâche strategy.review différée)
  strategy snapshot atelier
"""
from __future__ import annotations

import json

from . import strategy


def add_parser(sub) -> None:
    p = sub.add_parser("strategy", help="objectifs, hypothèses, expériences, preuves, décisions, revues")
    s = p.add_subparsers(dest="strategy_cmd", required=True)
    kinds = sorted(strategy.KINDS)
    a = s.add_parser("add", help="crée un objet stratégique")
    a.add_argument("kind", choices=kinds)
    a.add_argument("business")
    a.add_argument("summary")
    a.add_argument("--by", required=True, help="human, orbit, agent:ROLE, schedule")
    a.add_argument("--parent", type=int, default=None)
    a.add_argument("--task", type=int, default=None, help="tâche d'origine")
    a.add_argument("--set", action="append", default=[], metavar="CHAMP=VALEUR")
    l = s.add_parser("list", help="liste les objets d'un business")
    l.add_argument("kind", choices=kinds)
    l.add_argument("business")
    l.add_argument("--status", default=None)
    l.add_argument("--limit", type=int, default=50)
    g = s.add_parser("show", help="détail et liens")
    g.add_argument("kind", choices=kinds)
    g.add_argument("id", type=int)
    g.add_argument("business")
    m = s.add_parser("move", help="change le statut")
    m.add_argument("kind", choices=kinds)
    m.add_argument("id", type=int)
    m.add_argument("business")
    m.add_argument("status")
    m.add_argument("--by", required=True)
    m.add_argument("--outcome", default=None, choices=strategy.OUTCOMES)
    m.add_argument("--result", default=None)
    m.add_argument("--note", default=None)
    k = s.add_parser("link", help="relie deux objets du même business")
    k.add_argument("business")
    k.add_argument("from_kind", choices=kinds)
    k.add_argument("from_id", type=int)
    k.add_argument("to_kind", choices=kinds + sorted(strategy.EXTERNAL))
    k.add_argument("to_id", type=int)
    k.add_argument("relation")
    n = s.add_parser("mission", help="met en file une mission ORBIT rattachée à la stratégie")
    n.add_argument("business")
    n.add_argument("goal")
    for ref in ("objective", "hypothesis", "experiment"):
        n.add_argument(f"--{ref}", type=int, default=None)
    n.add_argument("--max-steps", type=int, default=8)
    r = s.add_parser("review", help="planifie une revue (tâche strategy.review)")
    r.add_argument("business")
    r.add_argument("--in", dest="due_in", type=float, default=0, help="délai en secondes")
    r.add_argument("--by", default="human")
    t = s.add_parser("snapshot", help="état persistant calculé (sans LLM)")
    t.add_argument("business")

    e = sub.add_parser("economy", help="cash observé, canaux, enveloppes de dépense, verdicts, réinvestissement")
    es = e.add_subparsers(dest="economy_cmd", required=True)
    x = es.add_parser("status", help="état économique (JSON)")
    x.add_argument("business")
    x = es.add_parser("cash", help="enregistre un mouvement d'argent réel")
    x.add_argument("business")
    x.add_argument("direction", choices=["in", "out"])
    x.add_argument("amount", type=float)
    x.add_argument("currency")
    x.add_argument("category")
    x.add_argument("--source", default=None, help="relevé, facture, export : sans source = non vérifié")
    x.add_argument("--experiment", type=int, default=None)
    x.add_argument("--channel", type=int, default=None)
    x.add_argument("--spend-request", type=int, default=None)
    x.add_argument("--by", default="human")
    x = es.add_parser("import", help="importe un export CSV réel (banque, paiement, marketplace)")
    x.add_argument("business")
    x.add_argument("path")
    x.add_argument("--currency", default=None)
    x.add_argument("--category", default="import")
    x.add_argument("--experiment", type=int, default=None)
    x.add_argument("--channel", type=int, default=None)
    x = es.add_parser("channel", help="enregistre un canal économique")
    x.add_argument("business")
    x.add_argument("kind")
    x.add_argument("name")
    x.add_argument("--locator", default=None)
    x.add_argument("--capabilities", default="", help="liste séparée par des virgules")
    x.add_argument("--by", default="human")
    x = es.add_parser("access", help="qualifie un canal : statut et droit d'agir (humain)")
    x.add_argument("business")
    x.add_argument("channel", type=int)
    x.add_argument("--status", default=None, choices=["active", "suspended", "abandoned"])
    x.add_argument("--access", default=None, choices=["none", "observe", "act"])
    x = es.add_parser("actions", help="actions proposées, bloquées ou exécutées")
    x.add_argument("business")
    x.add_argument("--status", default=None)
    x = es.add_parser("allow", help="accorde une enveloppe de dépense (humain)")
    x.add_argument("business")
    x.add_argument("amount", type=float)
    x.add_argument("currency")
    x.add_argument("rationale")
    x.add_argument("--experiment", type=int, default=None)
    x.add_argument("--days", type=float, default=None, help="validité")
    x = es.add_parser("policy", help="politique de réinvestissement (humain)")
    x.add_argument("business")
    x.add_argument("--share", type=float, required=True)
    x.add_argument("--max", dest="max_amount", type=float, required=True)
    x.add_argument("--currency", required=True)
    x.add_argument("--period-days", type=float, default=30)
    x = es.add_parser("cycle", help="évalue les expériences et réinvestit (sans LLM)")
    x.add_argument("business")


def _value(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _fields(pairs: list[str]) -> dict:
    fields = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise strategy.StrategyError(f"--set attend CHAMP=VALEUR : {pair!r}")
        # Les champs texte restent du texte même s'ils ressemblent à du JSON.
        numeric = key.strip() in strategy.NUMERIC
        fields[key.strip()] = _value(value) if numeric else value
    return fields


def run_economy(args) -> int:
    import time

    from . import economy
    cmd, b = args.economy_cmd, args.business
    try:
        if cmd == "status":
            print(json.dumps(economy.status(b), ensure_ascii=False, indent=1, default=str))
        elif cmd == "cash":
            entry = economy.record_cash(b, args.direction, args.amount, args.currency, args.category, created_by=args.by,
                                        nature="observed" if args.source else "unverified", source_ref=args.source,
                                        experiment_id=args.experiment, channel_id=args.channel,
                                        spend_request_id=args.spend_request)
            print(f"écriture #{entry} ({'observée' if args.source else 'non vérifiée'})")
        elif cmd == "import":
            print(json.dumps(economy.import_cash_csv(b, args.path, created_by="human", currency=args.currency,
                                                     category=args.category, experiment_id=args.experiment,
                                                     channel_id=args.channel), ensure_ascii=False))
        elif cmd == "channel":
            caps = [c for c in args.capabilities.split(",") if c.strip()]
            print(f"canal #{economy.add_channel(b, args.kind, args.name, created_by=args.by, locator=args.locator, capabilities=caps)}")
        elif cmd == "access":
            economy.update_channel(b, args.channel, actor="human", status=args.status, access=args.access)
            print(f"canal #{args.channel} mis à jour")
        elif cmd == "actions":
            from . import actions
            for a in actions.list_actions(b, status=args.status):
                print(f"#{a['id']:<5} {a['status']:9} canal #{a['channel_id']} {a['action']}  {a['reason'] or ''}")
        elif cmd == "allow":
            expires = time.time() + args.days * 86400 if args.days else None
            allowance = economy.grant_allowance(b, args.amount, args.currency, granted_by="human", rationale=args.rationale,
                                                experiment_id=args.experiment, expires_at=expires)
            print(f"enveloppe #{allowance}")
        elif cmd == "policy":
            economy.set_reinvest_policy(b, share=args.share, max_amount=args.max_amount, currency=args.currency,
                                        period_days=args.period_days, set_by="human")
            print("politique enregistrée")
        elif cmd == "cycle":
            print(json.dumps(economy.cycle(b), ensure_ascii=False, indent=1, default=str))
    except (strategy.StrategyError, OSError) as exc:
        print(f"refusé : {exc}")
        return 2
    return 0


def run(args) -> int:
    cmd = args.strategy_cmd
    try:
        if cmd == "add":
            item_id = strategy.create(args.kind, args.business, args.summary, created_by=args.by,
                                      parent_id=args.parent, origin_task_id=args.task, **_fields(args.set))
            print(f"{args.kind} #{item_id} créé ({args.business})")
        elif cmd == "list":
            for row in strategy.list_items(args.kind, args.business, status=args.status, limit=args.limit):
                print(f"#{row['id']:<5} {row['status']:12} {row['summary']}")
        elif cmd == "show":
            row = strategy.get(args.kind, args.id, args.business)
            if row is None:
                print(f"{args.kind} #{args.id} introuvable pour {args.business}")
                return 1
            print(json.dumps({**row, "links": strategy.links(args.business, args.kind, args.id)},
                             ensure_ascii=False, indent=1))
        elif cmd == "move":
            strategy.transition(args.kind, args.id, args.business, args.status, actor=args.by, outcome=args.outcome,
                                actual_result=args.result, note=args.note)
            print(f"{args.kind} #{args.id} -> {args.status}")
        elif cmd == "link":
            link_id = strategy.link(args.business, args.from_kind, args.from_id, args.to_kind, args.to_id,
                                    args.relation)
            print(f"lien #{link_id}")
        elif cmd == "mission":
            from . import worker
            worker.load_handlers()
            refs = {f"{ref}_id": getattr(args, ref) for ref in ("objective", "hypothesis", "experiment")}
            context = strategy.mission_context(args.business, **refs)  # erreur immédiate plutôt qu'en file
            task_id = worker.enqueue(args.business, "orbit.mission",
                                     {"goal": args.goal, "max_steps": args.max_steps,
                                      **{k: context[k] for k in refs if context[k] is not None}})
            print(f"tâche orbit.mission #{task_id} en file (python -m octopus worker pour l'exécuter)")
        elif cmd == "review":
            review_id, task_id = strategy.schedule_review(args.business, due_in_s=args.due_in, created_by=args.by)
            print(f"revue #{review_id} planifiée (tâche strategy.review #{task_id})")
        elif cmd == "snapshot":
            print(strategy.review_snapshot(args.business)["text"])
    except strategy.StrategyError as exc:
        print(f"refusé : {exc}")
        return 2
    return 0
