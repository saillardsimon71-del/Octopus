"""Banc d'evaluation : compare des modeles, et le code quand il existe, sur des taches reelles.

Chaque sortie est notee par des verifications deterministes (format, contraintes metier,
resistance a l'injection). Les resultats alimentent le journal ; les profils de cout
n'utilisent un modele alternatif que s'il a fait ses preuves ici (voir journal.evidence).
"""
from __future__ import annotations

import csv
import importlib
import json
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import catalog, enabled, journal, llm, paths, pricing

CODE = "code"
_CLASS_RANK = {CODE: -1, "local": 0, "free_quota": 1, "paid": 2}


@dataclass
class CheckResult:
    passed: bool
    score: float
    checks: dict = field(default_factory=dict)
    notes: str = ""
    value: float | None = None  # mesure numerique suivie sur les repetitions (ex. total /35)


@dataclass
class EvalItem:
    id: str
    messages: list
    check: Callable[[str], CheckResult]
    max_tokens: int = 1200
    json_mode: bool = False
    needs: tuple = ()
    repeats: int = 1
    baseline: Callable[[], str] | None = None  # sortie produite par du code, si elle existe


@dataclass
class EvalTask:
    name: str
    description: str
    items: list
    prompt_version: str = "v1"
    min_pass_rate: float = 0.9


def load_suite(module: str) -> list[EvalTask]:
    return importlib.import_module(module).build_suite(paths.home())


def estimate_cost(tasks: list[EvalTask], models: list[str], repeats: int | None, cat: catalog.Catalog) -> float:
    total = 0.0
    for model_id in models:
        model = cat.model(model_id) if model_id != CODE else None
        if not model or model["cost_class"] != "paid":
            continue
        for task in tasks:
            for item in task.items:
                chars, images = llm._prompt_size(item.messages)
                chars += images * pricing.IMAGE_CHARS_ESTIMATE
                # estimation en heures pleines (borne haute) ; la passerelle applique le tarif reel
                total += (repeats or item.repeats) * pricing.estimate_max_cost(model["price"], chars, item.max_tokens, True)
    return total


def run_bench(suite: str, models: list[str], *, tasks: list[str] | None = None, repeats: int | None = None,
              allow_paid: bool = False, max_cost_usd: float = 0.05, out_dir: Path | None = None,
              log: Callable[[str], None] = print) -> dict:
    if not enabled():
        raise RuntimeError("le banc a besoin du journal OCTOPUS : retirer OCTOPUS=off")
    cat = catalog.load()
    selected = [t for t in load_suite(suite) if not tasks or t.name in tasks]
    if not selected:
        raise ValueError("aucune tache selectionnee dans la suite")
    skipped: dict[str, str] = {}
    for model_id in models:
        if model_id == CODE:
            continue
        model = cat.model(model_id)
        if model is None:
            skipped[model_id] = "absent du catalogue"
        elif model["cost_class"] == "paid" and not allow_paid:
            skipped[model_id] = "modele payant : relancer avec --allow-paid"
    runnable = [m for m in models if m not in skipped]
    estimate = estimate_cost(selected, runnable, repeats, cat)
    log(f"Banc {suite} : {len(selected)} taches, modeles {', '.join(runnable) or '(aucun)'} ; "
        f"cout maximal estime {estimate:.4f} $, plafond {max_cost_usd:.4f} $")
    for model_id, reason in skipped.items():
        log(f"  ignore : {model_id} ({reason})")

    rows: list[dict] = []
    with journal.run("octopus", "bench", label=suite, budget_usd=max_cost_usd, profile="bench") as ctx:
        budget_hit = False
        for task in selected:
            for item in task.items:
                for model_id in runnable:
                    if model_id == CODE and item.baseline is None:
                        continue
                    is_paid = model_id != CODE and cat.model(model_id)["cost_class"] == "paid"
                    if budget_hit and is_paid:
                        continue
                    for rep in range(repeats or item.repeats):
                        row = _run_one(suite, task, item, model_id, rep, ctx.id, log)
                        rows.append(row)
                        if row["error"] and row["error"].startswith("budget"):
                            budget_hit = True
                            log("  plafond du banc atteint : modeles payants suivants ignores")
                            break
        bench_run_id = ctx.id
    matrix = summarize(rows, selected, cat)
    files = write_outputs(matrix, rows, out_dir or paths.data_dir() / "bench" / str(bench_run_id))
    return {"bench_run_id": bench_run_id, "rows": rows, "matrix": matrix, "skipped": skipped, "files": files}


def _run_one(suite: str, task: EvalTask, item: EvalItem, model_id: str, rep: int, bench_run_id: int,
             log: Callable[[str], None]) -> dict:
    started = time.perf_counter()
    text, cost, call_id, error = "", 0.0, None, None
    if model_id == CODE:
        text = item.baseline()
    else:
        try:
            completion = llm.complete(task.name, item.messages, agent="BENCH", business="octopus",
                                      max_tokens=item.max_tokens, json_mode=item.json_mode,
                                      needs=tuple(item.needs), pin_model=model_id, profile="bench")
            text, cost, call_id = completion.text, completion.cost_usd, completion.call_id
        except llm.BudgetExceeded as exc:
            error = f"budget : {exc}"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:300]
    latency_ms = int((time.perf_counter() - started) * 1000)
    if error:
        result = CheckResult(False, 0.0, {"appel": False}, error)
    else:
        try:
            result = item.check(text)
        except Exception as exc:  # un verificateur ne doit jamais faire tomber le banc
            result = CheckResult(False, 0.0, {"verificateur": False}, f"erreur du verificateur : {exc}")
    row = {
        "ts": time.time(), "bench_run_id": bench_run_id, "suite": suite, "task": task.name, "item": item.id,
        "model": model_id, "repeat": rep, "prompt_version": task.prompt_version, "passed": int(result.passed),
        "score": round(result.score, 4), "value": result.value, "checks": json.dumps(result.checks, ensure_ascii=False),
        "latency_ms": latency_ms, "cost_usd": cost, "llm_call_id": call_id, "error": error,
        "output_preview": text[:300],
    }
    journal.record_bench_result(row)
    log(f"  {task.name:22} {item.id:30} {model_id:22} {'OK' if result.passed else 'KO'} "
        f"score {result.score:.2f} {latency_ms} ms {cost:.5f} $ {result.notes[:70]}")
    return row


def summarize(rows: list[dict], tasks: list[EvalTask], cat: catalog.Catalog) -> list[dict]:
    by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        by_pair[(row["task"], row["model"])].append(row)
    matrix: list[dict] = []
    for task in tasks:
        task_rows = []
        for (task_name, model_id), group in by_pair.items():
            if task_name != task.name:
                continue
            values_by_item: dict[str, list[float]] = defaultdict(list)
            for r in group:
                if r["value"] is not None:
                    values_by_item[r["item"]].append(r["value"])
            spreads = [max(v) - min(v) for v in values_by_item.values() if len(v) > 1]
            model = cat.model(model_id)
            task_rows.append({
                "task": task.name, "model": model_id,
                "cost_class": CODE if model_id == CODE else model["cost_class"],
                "n": len(group), "pass_rate": sum(r["passed"] for r in group) / len(group),
                "mean_score": statistics.mean(r["score"] for r in group),
                "p50_latency_ms": int(statistics.median(r["latency_ms"] for r in group)),
                "mean_cost_usd": statistics.mean(r["cost_usd"] or 0.0 for r in group),
                "errors": sum(1 for r in group if r["error"]),
                "max_spread": max(spreads) if spreads else None,
            })
        decision = _decide(task, task_rows)
        for row in sorted(task_rows, key=lambda r: (_CLASS_RANK[r["cost_class"]], r["mean_cost_usd"])):
            row["decision"] = decision
            matrix.append(row)
    return matrix


def _decide(task: EvalTask, rows: list[dict]) -> str:
    code = next((r for r in rows if r["model"] == CODE), None)
    if code and code["pass_rate"] == 1.0:
        return "REMPLACER PAR DU CODE (100 % de conformite, 0 $)"
    qualified = [r for r in rows if r["model"] != CODE and r["pass_rate"] >= task.min_pass_rate and r["n"] >= 3]
    if not qualified:
        return f"AUCUN MODELE AU NIVEAU ({task.min_pass_rate:.0%} requis) : garder l'existant et ameliorer le prompt"
    best = min(qualified, key=lambda r: (_CLASS_RANK[r["cost_class"]], r["mean_cost_usd"], r["p50_latency_ms"]))
    caveat = " (preuve limitee : moins de 10 essais)" if best["n"] < 10 else ""
    return f"ROUTER VERS {best['model']}{caveat}"


def write_outputs(matrix: list[dict], rows: list[dict], out_dir: Path) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "resultats.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["vide"])
        writer.writeheader()
        writer.writerows(rows)
    md_path = out_dir / "matrice.md"
    md_path.write_text(format_matrix(matrix), encoding="utf-8")
    return [str(csv_path), str(md_path)]


def format_matrix(matrix: list[dict]) -> str:
    lines = ["| Tache | Modele | Classe | N | Reussite | Score | Latence p50 | Cout/appel | Ecart max | Decision |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in matrix:
        spread = "-" if r["max_spread"] is None else f"{r['max_spread']:g}"
        lines.append(f"| {r['task']} | {r['model']} | {r['cost_class']} | {r['n']} | {r['pass_rate']:.0%} | "
                     f"{r['mean_score']:.2f} | {r['p50_latency_ms']} ms | {r['mean_cost_usd']:.5f} $ | {spread} | "
                     f"{r['decision']} |")
    return "\n".join(lines) + "\n"
