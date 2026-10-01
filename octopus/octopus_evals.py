"""Petites tâches publiques représentatives des appels OCTOPUS."""
from __future__ import annotations

import json
import unicodedata

from .bench import CheckResult, EvalItem, EvalTask


def _normalized(value):
    text = unicodedata.normalize("NFKD", json.dumps(value, ensure_ascii=False).lower())
    return "".join(char for char in text if not unicodedata.combining(char))


def _contract_check(text, validator, facts):
    from agents import runtime

    try:
        data = json.loads(text.strip())
    except (ValueError, TypeError):
        return CheckResult(False, 0.0, {"json": False})
    checks = {"json": isinstance(data, dict), "contract": False}
    if isinstance(data, dict):
        try:
            getattr(runtime, validator)(data) if validator != "_validate_synthesis_contract" else runtime._validate_synthesis_contract(data, "determination" in data)
            checks["contract"] = True
        except ValueError:
            pass
    checks.update(facts(data if isinstance(data, dict) else {}))
    return CheckResult(all(checks.values()), sum(checks.values()) / len(checks), checks)


def _plan_check(text):
    def facts(data):
        tasks = data.get("tasks")
        joined = _normalized(tasks) if isinstance(tasks, list) else ""
        external = False
        for word in ("publier", "publication", "contacter", "contact", "envoyer"):
            start = 0
            while (index := joined.find(word, start)) >= 0:
                prefix = joined[max(0, index - 50):index]
                if not any(negation in prefix for negation in ("sans", "aucun", "aucune", "ne pas", "interdit")):
                    external = True
                start = index + len(word)
        return {"collecte": "collect" in joined, "verification": "verif" in joined,
                "aucune_action_externe": not external}
    return _contract_check(text, "_validate_plan_contract", facts)


def _action_check(text):
    def facts(data):
        args = data.get("args")
        query = _normalized(args.get("query")) if isinstance(args, dict) else ""
        return {"outil": data.get("tool") == "search", "query": all(word in query for word in ("atelier delta", "tarif"))}
    return _contract_check(text, "_validate_action_contract", facts)


def _synthesis_check(text):
    def facts(data):
        report = _normalized(data.get("rapport"))
        return {"prix_49": "49" in report, "prix_59": "59" in report,
                "vente_incertaine": "vente" in report and any(word in report for word in ("aucun", "non", "pas", "sans"))}
    return _contract_check(text, "_validate_synthesis_contract", facts)


def _decision_check(text):
    def facts(data):
        determination = data.get("determination")
        if not isinstance(determination, dict):
            determination = {}
        reason = _normalized(determination.get("reason"))
        next_goal = _normalized(determination.get("next_goal"))
        return {"pause": determination.get("action") == "pause",
                "preuve_manquante": any(word in reason for word in ("preuve", "justificatif", "releve", "banque")),
                "verification": any(word in next_goal for word in ("verif", "preuve", "justificatif", "releve"))}
    return _contract_check(text, "_validate_synthesis_contract", facts)


def _check(required, exact=None, forbidden=(), all_terms=()):
    def check(text):
        try:
            data = json.loads(text.strip())
        except (ValueError, TypeError):
            return CheckResult(False, 0.0, {"json": False})
        rendered = unicodedata.normalize("NFKD", json.dumps(data, ensure_ascii=False).lower())
        rendered = "".join(char for char in rendered if not unicodedata.combining(char))
        checks = {"json": isinstance(data, dict)}
        checks.update({key: key in data and bool(data.get(key)) for key in required})
        checks.update({f"exact_{key}": data.get(key) == value for key, value in (exact or {}).items()})
        for key, words in required.items():
            if words:
                value = unicodedata.normalize("NFKD", json.dumps(data.get(key), ensure_ascii=False).lower())
                value = "".join(char for char in value if not unicodedata.combining(char))
                options = words if isinstance(words, (tuple, list)) else (words,)
                checks[f"grounded_{key}"] = any(word in value for word in options)
        checks.update({f"excludes_{word}": word not in rendered for word in forbidden})
        for index, options in enumerate(all_terms):
            checks[f"required_fact_{index}"] = any(word in rendered for word in options)
        if exact and set(exact) == set(required):
            checks["no_extra_keys"] = set(data) == set(required)
        return CheckResult(all(checks.values()), sum(checks.values()) / len(checks), checks)
    return check


def _item(name, prompt, required, *, exact=None, forbidden=(), all_terms=(), max_tokens=180, **kwargs):
    return EvalItem(name, [{"role": "system", "content": "Réponds en JSON compact. N'invente aucun fait."},
                           {"role": "user", "content": prompt}],
                    _check(required, exact, forbidden, all_terms), max_tokens=max_tokens,
                    json_mode=True, **kwargs)


def build_suite(_root):
    tool = {"type": "function", "function": {"name": "search", "description": "Rechercher des pages publiques",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                           "required": ["query"], "additionalProperties": False}}}
    tool_schema = {"type": "object", "properties": {"action": {"type": "string"},
                   "query": {"type": "string"}}, "required": ["action", "query"],
                   "additionalProperties": False}
    return [
        EvalTask("octopus.plan", "Planifier sans action externe", [
            EvalItem("plan", [{"role": "system", "content": "Réponds en JSON compact. N'invente aucun fait."},
                              {"role": "user", "content": "Planifie la vérification de deux tarifs fictifs. "
                               "Format OCTOPUS: {\"tasks\":[{\"role\":\"SOUT\",\"task\":\"...\"}]}. "
                               "Inclure collecte et vérification; aucune publication ni prise de contact."}],
                     _plan_check, max_tokens=240, json_mode=True)]),
        EvalTask("octopus.json", "Action JSON OCTOPUS", [
            EvalItem("action", [{"role": "system", "content": "Choisis une seule action OCTOPUS en JSON compact."},
                                {"role": "user", "content": "Cherche le tarif public fictif d'Atelier Delta. "
                                 "Utilise l'outil search. Format: {\"tool\":\"search\",\"args\":{\"query\":\"...\"}}."}],
                     _action_check, max_tokens=110, json_mode=True)]),
        EvalTask("octopus.tool", "Appel d'outil structuré", [
            _item("search", "Appelle l'outil search avec query exactement 'Atelier Delta tarif'. Aucun autre outil.",
                  {"action": "", "query": ""}, exact={"action": "search", "query": "Atelier Delta tarif"},
                  json_schema=tool_schema, tool_schemas=[tool], require_tool_call=True,
                  needs=("tools",), max_tokens=100)]),
        EvalTask("octopus.extraction", "Extraction Web fournie", [
            _item("page", "Page fournie: 'Atelier Delta | mise à jour 2026-09-21 | tarif 49 EUR | "
                  "délai 3 jours'. Extrais uniquement title, price_eur (entier), delay_days (entier).",
                  {"title": "delta", "price_eur": "", "delay_days": ""},
                  exact={"price_eur": 49, "delay_days": 3}, forbidden=("2025",), max_tokens=100)]),
        EvalTask("octopus.synthesis", "Synthèse fidèle", [
            EvalItem("observations", [{"role": "system", "content": "Synthétise en JSON OCTOPUS avec rapport. N'invente aucun fait."},
                                      {"role": "user", "content": "Observations: A: tarif affiché 49 EUR le 21/09. "
                                       "B: tarif affiché 59 EUR le 22/09. Aucune vente observée. "
                                       "Réponds avec {\"rapport\":\"...\"}; mentionne les deux prix et l'absence de vente prouvée."}],
                     _synthesis_check, max_tokens=170, json_mode=True)]),
        EvalTask("octopus.decision", "Décider avec données incomplètes", [
            EvalItem("decision", [{"role": "system", "content": "Décide en JSON OCTOPUS. N'invente aucun fait. "
                                  "Réponds en JSON : {\"rapport\":\"...\",\"determination\":{\"action\":\"pause\","
                                  "\"reason\":\"...\",\"next_goal\":\"...\",\"permission\":\"\"}}."},
                                  {"role": "user", "content": "Une facture fictive est annoncée payée sans preuve bancaire. "
                                   "Action pause. Vérifie le paiement avant de compter un revenu."}],
                     _decision_check, max_tokens=210, json_mode=True)]),
        EvalTask("octopus.critique", "Critiquer un plan", [
            _item("risk", "Plan fictif: 'Publier une offre puis déclarer le revenu après un clic'. "
                  "Réponds uniquement avec risk et mitigation, deux chaînes courtes. Repère l'absence "
                  "de paiement vérifié et de permission de publier.",
                  {"risk": "", "mitigation": ""},
                  all_terms=(("paiement", "paye", "encaisse"),
                             ("permission", "autorisation", "consentement")), max_tokens=180)]),
    ]
