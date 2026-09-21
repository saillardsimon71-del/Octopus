"""Trusted runtime probes used by the acceptance evidence harness.

The process prints exactly one marker line containing JSON so the supervising gate
can ignore incidental stdout emitted while importing the artifact under test.
"""
from __future__ import annotations

import argparse
import importlib
import json
from typing import Any


MARKER = "OCTOPUS_EVIDENCE_JSON="


def tk_navigation(module_name: str, class_name: str, attribute: str) -> dict[str, Any]:
    app = None
    try:
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        app = cls()
        mapping = getattr(app, attribute)
        if isinstance(mapping, dict):
            labels = list(mapping.keys())
        else:
            labels = list(mapping)
        try:
            app.update_idletasks()
        except Exception:
            pass
        return {
            "runtime": {"launched": True, "exception": None},
            "ui": {"primary_nav": labels},
        }
    except Exception as exc:
        return {
            "runtime": {
                "launched": False,
                "exception": f"{type(exc).__name__}: {exc}",
            },
            "ui": {"primary_nav": None},
        }
    finally:
        if app is not None:
            try:
                app.destroy()
            except Exception:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m octopus.acceptance_probe")
    sub = parser.add_subparsers(dest="command", required=True)
    tk = sub.add_parser("tk-navigation")
    tk.add_argument("--module", required=True)
    tk.add_argument("--class", dest="class_name", required=True)
    tk.add_argument("--attribute", default="nav_buttons")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "tk-navigation":
        result = tk_navigation(args.module, args.class_name, args.attribute)
    else:
        raise SystemExit(f"unsupported probe: {args.command}")
    print(MARKER + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
