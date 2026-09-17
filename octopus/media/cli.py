"""python -m octopus video ... : diagnostic, génération, historique, réutilisation."""
from __future__ import annotations

import argparse
import json
import time

from .. import tasks, worker
from . import handlers, library, perf, presets, wangp


def add_parser(sub) -> None:
    p = sub.add_parser("video", help="génération vidéo locale (WanGP / MiniMax H3)")
    vsub = p.add_subparsers(dest="video_cmd", required=True)
    vsub.add_parser("doctor", help="installation WanGP, instance déjà lancée, dernier diagnostic (rapide)")
    q = vsub.add_parser("probe", help="démarre WanGP sans générer : matériel, modèles disponibles, réglages")
    q.add_argument("--families", default="minimax_h3,ltx2")
    g = vsub.add_parser("generate", help="met une génération en file")
    g.add_argument("prompt")
    g.add_argument("--preset", default=None, help="brouillon | standard | qualite | h3 (voir « video presets »)")
    g.add_argument("--model", default=None)
    g.add_argument("--seconds", type=float, default=None)
    g.add_argument("--resolution", default=None, help="LARGEURxHAUTEUR, ex. 480x832")
    g.add_argument("--steps", type=int, default=None)
    g.add_argument("--seed", type=int, default=None)
    g.add_argument("--variants", type=int, default=1)
    g.add_argument("--business", default="studio")
    g.add_argument("--setting", action="append", default=[], help="réglage WanGP clé=valeur JSON, ex. guidance_scale=5")
    g.add_argument("--allow-with-webui", action="store_true")
    g.add_argument("--force", action="store_true", help="essayer malgré un matériel jugé insuffisant")
    g.add_argument("--wait", action="store_true", help="exécuter tout de suite dans ce terminal")
    vsub.add_parser("presets", help="préréglages vitesse / qualité et durée estimée sur cette machine")
    m = vsub.add_parser("perf", help="performances mesurées des générations (s/étape, chargement, total)")
    m.add_argument("--limit", type=int, default=20)
    l = vsub.add_parser("list", help="historique")
    l.add_argument("--limit", type=int, default=20)
    l.add_argument("--search", default=None)
    s = vsub.add_parser("show", help="détail d'une génération")
    s.add_argument("id", type=int)
    r = vsub.add_parser("reuse", help="relance le prompt et les réglages d'une génération (nouvelles graines)")
    r.add_argument("id", type=int)
    r.add_argument("--variants", type=int, default=1)
    r.add_argument("--wait", action="store_true")


def _print_probe(data: dict) -> None:
    hw = data.get("hardware", {})
    gpu = hw.get("gpu") or {}
    print(f"WanGP {data.get('wangp_version')} ; Python {hw.get('python')} ; torch {hw.get('torch')} ; "
          f"CUDA {'oui' if hw.get('cuda_available') else 'non'}")
    print(f"GPU : {gpu.get('name', '-')} ({gpu.get('vram_total_gb', '?')} Go, capacité {gpu.get('capability', '?')}) ; "
          f"RAM : {hw.get('ram_total_gb')} Go ; CPU logiques : {hw.get('cpu_count')}")
    if data.get("error"):
        print(f"ERREUR : {data['error']}")
    for model in data.get("models", []):
        print(f"  {model.get('availability', '?'):9} {model['model_type']:36} {model.get('name')}")
    if data.get("probe_seconds"):
        print(f"Démarrage du runtime : {data['probe_seconds']} s")


def _enqueue(inp: dict) -> int:
    worker.load_handlers()
    return worker.enqueue(inp.get("business") or "studio", "media.video_generate", inp)


def _wait(task_id: int) -> int:
    worker.load_handlers()
    last = ""
    while True:
        task = tasks.get(task_id)
        if task["status"] == "queued":
            worker.run_one(kinds=["media.video_generate"])
            continue
        if task["status"] in ("done", "failed", "cancelled", "waiting_human"):
            print(json.dumps({"status": task["status"], "output": task["output"], "error": (task["error"] or "")[:500]},
                             ensure_ascii=False, indent=1))
            return 0 if task["status"] == "done" else 1
        line = " | ".join(f"#{g['id']} {g['phase']} {g['progress']}%" for g in library.by_task(task_id))
        if line != last:
            print(line)
            last = line
        time.sleep(2)


def run(args) -> int:
    cmd = args.video_cmd
    if cmd == "doctor":
        install = wangp.discover()
        print(json.dumps(install.as_dict(), ensure_ascii=False, indent=1))
        busy = wangp.running_instances()
        print("Instances WanGP lancées : " + (", ".join(f"PID {b['pid']}" for b in busy) if busy else "aucune"))
        cached = wangp.cached_probe()
        if cached:
            print(f"Dernier diagnostic : {time.strftime('%Y-%m-%d %H:%M', time.localtime(cached.get('probed_at', 0)))}")
            _print_probe(cached)
        else:
            print("Aucun diagnostic : lancer « python -m octopus video probe » (WanGP fermé dans Pinokio).")
        print(f"Modèle par défaut : {handlers.default_model()}")
        return 0 if install.ok else 1
    if cmd == "probe":
        data = wangp.probe(families=args.families)
        _print_probe(data)
        return 0 if data.get("ok") else 1
    if cmd == "generate":
        settings = {}
        for item in args.setting:
            key, _, value = item.partition("=")
            try:
                settings[key] = json.loads(value)
            except ValueError:
                settings[key] = value
        if args.steps:
            settings["num_inference_steps"] = args.steps
        inp = {"prompt": args.prompt, "preset": args.preset, "model_type": args.model, "settings": settings,
               "duration_s": args.seconds, "resolution": args.resolution, "seed": args.seed, "variants": args.variants,
               "business": args.business, "allow_with_webui": args.allow_with_webui, "force": args.force}
        inp = {k: v for k, v in inp.items() if v not in (None, {}, False)}
        try:
            perf.backfill()
            estimate = presets.estimate_for_input(inp, inp.get("model_type") or (None if args.preset else handlers.default_model()))
        except ValueError as exc:
            print(f"erreur : {exc}")
            return 2
        task_id = _enqueue(inp)
        print(f"génération en file : tâche #{task_id} — durée prévue {perf.describe(estimate)}")
        return _wait(task_id) if args.wait else 0
    if cmd == "presets":
        perf.backfill()
        for preset in presets.PRESETS.values():
            model = preset.model_type or handlers.default_model()
            estimate = presets.estimate_for_input({"preset": preset.key}, model)
            print(f"{preset.key:10} {preset.label:26} {model:22} {preset.resolution or 'défaut':9} "
                  f"{preset.duration_s or '?'}s  {perf.describe(estimate)}")
            print(f"{'':10} {preset.note}")
        return 0
    if cmd == "perf":
        added = perf.backfill()
        if added:
            print(f"{added} lancement(s) passé(s) mesuré(s)")
        for r in perf.runs(args.limit):
            state = {0: "ok", 2: "échec", 3: "annulé"}.get(r["exit_code"], f"code {r['exit_code']}")
            print(f"tâche #{r['task_id'] or '?':<4} {state:7} {r['model_type']:22} {r['width']}x{r['height']} "
                  f"{r['frames'] or '?'} img  {r['steps'] or '?'} ét. {'CFG' if (r['passes'] or 2) == 2 else 'sans CFG'}  "
                  f"{r['sec_per_step'] if r['sec_per_step'] is not None else '?'} s/étape  "
                  f"chargement {r['prepare_s'] if r['prepare_s'] is not None else '?'} s  "
                  f"téléchargé {r['download_gb'] or 0:g} Go  total {r['total_s']:.0f} s  [{r['gpu'] or '?'}]")
        return 0
    if cmd == "list":
        for g in library.recent(args.limit, search=args.search):
            print(f"#{g['id']:<5} {g['status']:9} {g['progress']:>3}% {g['model_type']:28} "
                  f"{(g['duration_s'] or 0):5.1f}s {g['width'] or '?'}x{g['height'] or '?'}  {g['prompt'][:60]}")
        return 0
    if cmd == "show":
        print(json.dumps(library.get(args.id), ensure_ascii=False, indent=1, default=str))
        return 0
    if cmd == "reuse":
        source = library.get(args.id)
        if not source:
            print(f"génération #{args.id} inconnue")
            return 2
        settings = {k: v for k, v in source["settings"].items() if k not in ("prompt", "model_type", "seed")}
        task_id = _enqueue({"prompt": source["prompt"], "model_type": source["model_type"], "settings": settings,
                            "variants": args.variants, "business": source["business"], "parent_id": source["id"]})
        print(f"variante(s) en file : tâche #{task_id}")
        return _wait(task_id) if args.wait else 0
    raise SystemExit(f"sous-commande inconnue : {cmd}")
