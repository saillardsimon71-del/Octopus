"""Explicit synchronous tool registry; policies remain in OCTOPUS handlers.

Pattern adapted from NousResearch/hermes-agent tools/registry.py at
59004a62356f3a4697ab0fe8ad5086d2b405e2a6 (MIT). No discovery or imports
of upstream runtime code. The existing params dialect is the sole schema.
"""
from __future__ import annotations

from . import cancel
from octopus.browser_workspace import HumanBrowserRequired


def technical_refusal(reason: str) -> bool:
    """Known invalid inputs/sources are observations, not missing authority.

    Deliberately anchored: a policy refusal containing a DNS error remains a policy
    refusal. Unknown refusals remain human boundaries in the supervisor.
    """
    return reason.startswith((
        "argument obligatoire manquant :", "args doit être un objet", "outil inconnu :",
        "hôte local refusé :", "adresse locale ou privée refusée :", "adresse privée refusée :",
        "hôte non résolvable :", "URL sans hôte", "schéma refusé :",
        "aucune page ouverte :", "ref attendue de la forme", "ref @",
        "direction : up, down, left ou right", "text requis :", "touche invalide",
        "HTTP 404", "HTTP 403", "timeout réseau", "source Web inaccessible",
        "JSON invalide", "JSONDecodeError:", "InvalidOutput:",
        "sortie structurée invalide", "HTTP 429", "429", "cooldown",
        "TimeoutError:", "ConnectionError:",
        "provider temporairement indisponible", "navigateur indisponible :",
        "capture indisponible", "capture PNG bornée requise", "backend_error",
        "effet déclaré :", "état sémantique invalide", "état de session et preuve d’action",
    )) or (reason.startswith("argument ") and " : type attendu " in reason)


def _matches_tool_type(value, token: str) -> bool:
    if token.endswith("_id"):
        token = "int"
    checks = {
        "str": lambda v: isinstance(v, str),
        "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "list": lambda v: isinstance(v, list),
        "dict": lambda v: isinstance(v, dict),
        "bool": lambda v: isinstance(v, bool),
    }
    check = checks.get(token)
    return False if check is None else check(value)


class ToolRegistry(dict):
    """One mapping of name -> desc/params/fn, also used by legacy consumers.

    Entries are explicitly composed by runtime after defining its handlers.
    Availability and permissions are resolved by those existing handlers;
    listing an entry grants no authority to execute an external action.
    """

    def describe(self, allowed_tools: set[str] | None = None, *, legacy_search: bool = False) -> str:
        items = self.items() if allowed_tools is None else (
            (name, spec) for name, spec in self.items() if name in allowed_tools
        )
        lines = []
        for name, spec in items:
            if legacy_search and name == "search":
                lines.append("- search(query) : recherche web (liens)")
            else:
                lines.append(f"- {name}({', '.join(spec['params'])}) : {spec['desc']}")
        return "\n".join(lines)

    def validate(self, tool: str, args) -> str | None:
        """Validation structurelle minimale des paramètres déclarés dans le registre."""
        if not isinstance(args, dict):
            return f"args doit être un objet, reçu {type(args).__name__}"
        for name, declared in self[tool]["params"].items():
            declared = str(declared)
            variants = declared.split("|")
            optional = all(v.endswith("?") for v in variants)
            clean = [v[:-1] if v.endswith("?") else v for v in variants]
            if name not in args or args[name] is None:
                if optional:
                    continue
                return f"argument obligatoire manquant : {name}"
            value = args[name]
            if not any(_matches_tool_type(value, token) for token in clean):
                expected = "|".join(clean)
                return f"argument {name} : type attendu {expected}, reçu {type(value).__name__}"
        return None

    def normalize_allowed(self, allowed_tools) -> set[str] | None:
        if allowed_tools is None:
            return None
        allowed = set(allowed_tools)
        unknown = sorted(allowed - set(self))
        if unknown:
            raise ValueError(f"outils inconnus dans allowed_tools : {', '.join(unknown)}")
        return allowed

    def dispatch(self, tool: str, args, allowed_tools: set[str] | None = None):
        """Return (refusal, result); never retry, and propagate human cancellation."""
        if tool not in self:
            return f"outil inconnu : {tool}", None
        if allowed_tools is not None and tool not in allowed_tools:
            return f"outil {tool} interdit par la politique de cette mission", None
        refusal = self.validate(tool, args)
        if refusal is not None:
            return refusal, None
        try:
            return None, self[tool]["fn"](args)
        except (cancel.Cancelled, HumanBrowserRequired):
            raise
        except Exception as exc:
            # Bound exception text before it can enter the model/history.
            raise RuntimeError(str(exc)[:2048]) from None
