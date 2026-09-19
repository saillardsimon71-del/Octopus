"""Watchdog indépendant des workers GPU OCTOPUS."""
from __future__ import annotations

import argparse
import json
import os
import time

from octopus.compute_finance import BudgetLimits, FinancialCircuitBreaker
from octopus.gpuai import GPUAIClient, GPUAIConfig
from octopus.salad import SaladClient, SaladConfig


def providers_from_env() -> dict[str, object]:
    providers: dict[str, object] = {}
    salad = SaladConfig.from_env()
    if salad.api_key and salad.organization and salad.project:
        providers["salad"] = SaladClient(salad)
    gpuai = GPUAIConfig.from_env()
    if gpuai.api_key:
        providers["gpuai"] = GPUAIClient(gpuai)
    return providers


def run_once(*, limits: BudgetLimits | None = None) -> list[dict]:
    breaker = FinancialCircuitBreaker(limits)
    return breaker.watchdog(providers_from_env())


def main() -> int:
    parser = argparse.ArgumentParser(description="OCTOPUS GPU financial watchdog")
    parser.add_argument("--once", action="store_true", help="un seul sweep puis sortie")
    parser.add_argument("--interval", type=float, default=None, help="intervalle entre sweeps en secondes")
    args = parser.parse_args()

    limits = BudgetLimits.from_env()
    interval = limits.watchdog_interval_s if args.interval is None else float(args.interval)
    if interval <= 0 or interval > limits.watchdog_interval_s:
        parser.error(
            f"--interval doit être > 0 et <= OCTOPUS_GPU_WATCHDOG_INTERVAL_S ({limits.watchdog_interval_s:g}s)"
        )

    while True:
        actions = run_once(limits=limits)
        for action in actions:
            print(json.dumps(action, ensure_ascii=False), flush=True)
        if args.once:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
