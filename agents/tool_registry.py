"""Explicit synchronous tool registry; policies remain in OCTOPUS handlers.

Pattern adapted from NousResearch/hermes-agent tools/registry.py at
59004a62356f3a4697ab0fe8ad5086d2b405e2a6 (MIT). No discovery or imports
of upstream runtime code. The existing params dialect is the sole schema.
"""
from __future__ import annotations

import ast
import json
import re
from urllib.parse import urlsplit

from . import cancel
from octopus.browser_workspace import HumanBrowserRequired


def technical_refusal(reason) -> bool:
    """Known invalid inputs/sources are observations, not missing authority.

    Deliberately anchored: a policy refusal containing a DNS error remains a policy
    refusal. Unknown refusals remain human boundaries in the supervisor.
    """
    if isinstance(reason, dict):
        if reason.get('error_code') in ('format', 'ambiguous', 'invalid_tool_arguments'):
            return True
        reason = str(reason.get('reason') or '')
    if reason == "L'opérateur doit fournir un accès manuel à cette page":
        return True
    return reason.startswith((
        "invalid_tool_arguments:",
        "argument obligatoire manquant :", "args doit être un objet", "outil inconnu :",
        "hôte local refusé :", "adresse locale ou privée refusée :", "adresse privée refusée :",
        "hôte non résolvable :", "URL sans hôte", "schéma refusé :",
        "aucune page ouverte :", "ref attendue de la forme", "ref @",
        "direction : up, down, left ou right", "text requis :", "touche invalide",
        "cible clavier indisponible :",
        "suppression irréversible de l'identité :",
        "Un effet précédent n'est pas encore confirmé",
        "Cette ressource n'appartient pas au contexte", "Cette page ne correspond à aucune ressource",
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

    def parse_action(self, value):
        def reject(kind, message):
            return {'tool': None, 'args': {}, '_protocol': kind, '_protocol_error': message}

        normalized = False
        if isinstance(value, str):
            text = value.strip()
            if not text or len(text) > 20000:
                return reject('format', 'Réponse vide ou trop longue. Choisis une action courte ou conclus.')
            objects, start, depth, quote, escaped = [], None, 0, None, False
            for i, char in enumerate(text):
                if depth and quote:
                    if escaped:
                        escaped = False
                    elif char == '\\':
                        escaped = True
                    elif char == quote:
                        quote = None
                elif depth and char in ('"', "'"):
                    quote = char
                elif char == '{':
                    if depth == 0:
                        start = i
                    depth += 1
                elif char == '}' and depth:
                    depth -= 1
                    if depth == 0:
                        objects.append(text[start:i + 1])
            if depth:
                return reject('format', 'Action incomplète. Donne le nom de l\'outil et ses paramètres.')
            if len(objects) > 1:
                actions = [self.parse_action(item) for item in objects]
                if all(action.get('tool') and not action.get('_protocol_error') for action in actions):
                    identities = {json.dumps([action['tool'], action['args']], sort_keys=True, ensure_ascii=False)
                                  for action in actions}
                    if len(identities) == 1:
                        return {**actions[0], '_protocol': 'normalized'}
                return reject('ambiguous', 'Plusieurs actions sont proposées. Choisis une seule action.')
            if not objects:
                return {'final': text, '_protocol': 'immediate'}
            normalized = objects[0] != text
            duplicate = []
            def pairs(items):
                result = {}
                for key, item in items:
                    if key in result:
                        duplicate.append(key)
                    result[key] = item
                return result
            try:
                value = json.loads(objects[0], object_pairs_hook=pairs)
            except (ValueError, RecursionError):
                try:
                    tree = ast.parse(objects[0], mode='eval')
                    literals = {'true': True, 'false': False, 'null': None}
                    for node in ast.walk(tree):
                        for field, child in ast.iter_fields(node):
                            if isinstance(child, ast.Name) and child.id in literals:
                                setattr(node, field, ast.Constant(literals[child.id]))
                            elif isinstance(child, list):
                                child[:] = [ast.Constant(literals[v.id]) if isinstance(v, ast.Name) and v.id in literals else v for v in child]
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Dict):
                            keys = [ast.literal_eval(key) for key in node.keys]
                            if len(keys) != len(set(keys)):
                                duplicate.append('key')
                    value = ast.literal_eval(tree)
                    normalized = True
                except (ValueError, TypeError, SyntaxError, RecursionError):
                    return reject('format', 'Action illisible. Donne le nom de l\'outil et ses paramètres.')
            if duplicate:
                return reject('ambiguous', 'Un paramètre est répété. Donne une seule valeur par paramètre.')
        if not isinstance(value, dict):
            return reject('format', 'Une action ou une conclusion est attendue.')
        while len(value) == 1 and isinstance(value.get('action', value.get('response')), dict):
            value = value.get('action', value.get('response'))
            normalized = True
        tool = value.get('tool', value.get('name', value.get('action')))
        if 'final' in value:
            if tool:
                return reject('ambiguous', 'Une conclusion et une action sont proposées ensemble. Choisis une seule.')
            if not isinstance(value['final'], str) or not value['final'].strip():
                return reject('format', 'La conclusion doit contenir ton rapport.')
            return {**value, '_protocol': 'normalized' if normalized else 'immediate'}
        if not isinstance(tool, str):
            return reject('format', 'Indique le nom de l\'outil et ses paramètres, ou conclus avec ton rapport.')
        name = tool.strip().lower().replace('-', '_')
        if name not in self and 'browser_' + name in self:
            name = 'browser_' + name
        params = self.get(name, {}).get('params', {})
        names = [value[k].strip().lower().replace('-', '_') for k in ('tool', 'name', 'action')
                 if isinstance(value.get(k), str) and k not in params]
        if len(set(names)) > 1:
            return reject('ambiguous', 'Plusieurs outils différents sont proposés. Choisis un seul outil.')
        normalized |= name != tool
        args = value.get('args', value.get('arguments', {}))
        if 'args' in value and 'arguments' in value and value['args'] != value['arguments']:
            return reject('ambiguous', 'Deux jeux de paramètres différents sont proposés. Choisis un seul.')
        if not isinstance(args, dict):
            return reject('ambiguous', 'Les paramètres ne désignent pas une action unique.')
        args = dict(args)
        for key, item in value.items():
            if key in ('tool', 'args', 'arguments', '_protocol', '_protocol_error') or key in ('name', 'action') and key not in self.get(name, {}).get('params', {}):
                continue
            if key in args and args[key] != item:
                return reject('ambiguous', f'Deux valeurs différentes pour {key}. Choisis une seule valeur.')
            args[key] = item
        if name == 'final' and isinstance(args.get('report', args.get('answer')), str):
            return {'final': args.get('report', args.get('answer')), '_protocol': 'normalized'}
        if 'ref' in args:
            ref = args['ref']
            if isinstance(ref, list):
                return reject('ambiguous', 'Plusieurs cibles sont proposées. Choisis une cible de la page.')
            match = re.fullmatch(r'@?[eE]?(\d+)', str(int(ref) if isinstance(ref, float) and ref.is_integer() else ref))
            if match:
                args['ref'] = '@e' + match.group(1)
                normalized |= args['ref'] != ref
        for key, declared in self.get(name, {}).get('params', {}).items():
            item = args.get(key)
            tokens = str(declared).replace('?', '').split('|')
            if isinstance(item, str) and 'int' in tokens and re.fullmatch(r'-?\d+', item):
                args[key] = int(item)
                normalized = True
            elif isinstance(item, float) and 'int' in tokens and item.is_integer():
                args[key] = int(item)
                normalized = True
            elif isinstance(item, str) and 'bool' in tokens and item.lower() in ('true', 'false'):
                args[key] = item.lower() == 'true'
                normalized = True
            elif key == 'direction' and isinstance(item, str):
                args[key] = item.lower()
                normalized |= args[key] != item
        from agents import agent_browser
        if agent_browser.contains_secret(json.dumps(args, ensure_ascii=False)):
            return reject('format', 'Un secret ne peut pas être transmis à un outil ni conservé dans la conversation.')
        return {'tool': name, 'args': args, '_protocol': 'normalized' if normalized else 'immediate'}

    def describe(self, allowed_tools: set[str] | None = None, *, legacy_search: bool = False, browser_details: bool = False) -> str:
        items = self.items() if allowed_tools is None else (
            (name, spec) for name, spec in self.items() if name in allowed_tools
        )
        lines = []
        for name, spec in items:
            if legacy_search and name == "search":
                lines.append("- search(query) : recherche web (liens)")
            else:
                params = spec['params']
                if name.startswith('browser_'):
                    # Workspace resolves the active account/business channel deterministically.
                    params = {k: v for k, v in params.items() if k not in ('channel_id', 'effect')}
                    labels = [f"{k}: {v}" for k, v in params.items()]
                    lines.append(f"- {name}({', '.join(labels)}) : {spec['desc']}")
                else:
                    lines.append(f"- {name}({', '.join(params)}) : {spec['desc']}")
        return "\n".join(lines)

    def validate(self, tool: str, args) -> str | None:
        """Validation structurelle minimale des paramètres déclarés dans le registre."""
        if not isinstance(args, dict):
            return f"args doit être un objet, reçu {type(args).__name__}"
        if tool.startswith('browser_'):
            unexpected = set(args) - set(self[tool]['params'])
            if unexpected:
                return "invalid_tool_arguments: unknown parameters; use only declared schema fields"
            for key, values in {'direction': ('up', 'down', 'left', 'right'),
                    'state': ('authenticated', 'unauthenticated', 'challenge', 'uncertain')}.items():
                if key in args and args[key] is not None and args[key] not in values:
                    return f"invalid_tool_arguments: {key} must be one of {'|'.join(values)} or omitted"
            if 'ref' in args and (not isinstance(args['ref'], str) or not re.fullmatch(r'@?e[0-9]+', args['ref'])):
                return "invalid_tool_arguments: ref must match @eN from latest observation"
            if 'url' in args:
                try:
                    parts = urlsplit(args['url'])
                    valid = parts.scheme in ('http', 'https') and bool(parts.hostname) and not parts.username and not parts.password
                    parts.port  # validates malformed/out-of-range ports
                except (TypeError, ValueError):
                    valid = False
                if not valid:
                    return "invalid_tool_arguments: url must be an absolute HTTP(S) URL without credentials"
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
                return ("invalid_tool_arguments: " if tool.startswith('browser_') else "") + f"argument {name} : type attendu {expected}, reçu {type(value).__name__}"
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
