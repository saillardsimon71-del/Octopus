"""OCTOPUS Workbench V2: mission-first desktop interface."""
from __future__ import annotations

import os
import queue
import threading
import time
from pathlib import Path

import customtkinter as ctk

from .intelligence import EntrepreneurialWorkbench
from .workbench import COLORS, DEFAULT_BUSINESS_ID, _open_path
from .workbench_v2_data import mission_state, read_snapshot
from .workspaces import Business

PRIMARY = ("Vue d'ensemble", "Missions", "Livrables", "Navigateur", "Activité", "Paramètres")
ADVANCED = ("Business", "Agents", "Intelligence", "Agnes", "Production", "Humain", "Système")
SYSTEM_BUSINESS = "octopus"
META = {
    "Vue d'ensemble": ("Vue d'ensemble", "Intelligence économique autonome. Performance réelle, limites humaines explicites."),
    "Missions": ("Missions", "Objectifs confiés par l'humain et recherches déterminées par OCTOPUS."),
    "Livrables": ("Livrables", "Rapports, observations, décisions et fichiers. Leur portée reste explicite."),
    "Navigateur": ("Navigateur Hermes", "Observations de la session utilisée par OCTOPUS."),
    "Activité": ("Activité", "Les événements récents du travail supervisé."),
    "Paramètres": ("Paramètres", "Services, permissions et outils avancés."),
}


def _date(value) -> str:
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(float(value))) if value else "Date inconnue"


def _event_label(event: dict) -> str:
    names = {
        "task.queued": "Tâche planifiée", "task.started": "Tâche démarrée",
        "task.done": "Tâche terminée", "task.failed": "Tâche en erreur",
        "agnes.generation.updated": "Vidéo Agnes mise à jour",
        "agnes.generation.created": "Génération Agnes enregistrée",
        "agnes.video.done": "Vidéo Agnes produite",
        "strategy.objective.created": "Objectif créé",
        "strategy.objective.transition": "Objectif mis à jour",
        "strategy.evidence.created": "Preuve enregistrée",
        "action.executed": "Action autorisée exécutée",
    }
    name = names.get(event["type"])
    if name:
        return name
    if event["type"].startswith("economy."):
        return "Économie : " + event["type"].split(".")[-1].replace("_", " ")
    return event["type"].replace(".", " / ").replace("_", " ")


def _event_detail(event: dict) -> str:
    data = event["data"]
    if event["type"] == "agnes.generation.updated":
        details = [f"génération #{data['id']}"] if data.get("id") else []
        details += [str(data[key]) for key in ("status", "phase") if data.get(key)]
        if data.get("progress") is not None:
            details.append(f"{data['progress']} %")
        return " | ".join(details)
    if event["type"] == "agnes.video.done":
        return "MP4 vérifié" if data.get("verified") else "Vérification à confirmer"
    if event["type"] == "strategy.evidence.created":
        return str(data.get("summary") or "")
    if event["type"] in ("task.queued", "task.started", "task.done", "task.failed"):
        return str(data.get("kind") or "")
    return ""


class WorkbenchV2(EntrepreneurialWorkbench):
    def __init__(self) -> None:
        self._snapshot = None
        self._visible_business_ids = set()
        self._snapshot_busy = False
        self._snapshot_at = 0.0
        self._snapshot_error = None
        self._creating = False
        self._pursuit_processes = []
        self._reload_after_create = False
        self._readonly = os.environ.get("OCTOPUS_WORKBENCH_READONLY") == "1"
        super().__init__()
        self.title("OCTOPUS - Workbench")
        self.geometry("1360x840")
        self.minsize(1180, 700)
        self._load_snapshot()

    def _build_shell(self) -> None:
        self.grid_columnconfigure(0, minsize=206)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.sidebar = ctk.CTkFrame(self, width=206, corner_radius=0, fg_color=COLORS["surface"])
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        ctk.CTkLabel(self.sidebar, text="OCTOPUS", anchor="w", text_color=COLORS["text"],
                     font=("Segoe UI", 25, "bold")).pack(fill="x", padx=22, pady=(24, 2))
        ctk.CTkLabel(self.sidebar, text="Intelligence économique autonome", anchor="w", text_color=COLORS["muted"],
                     font=("Segoe UI", 11)).pack(fill="x", padx=22, pady=(0, 26))
        ctk.CTkLabel(self.sidebar, text="Consulter", anchor="w", text_color=COLORS["muted"],
                     font=("Segoe UI", 10)).pack(fill="x", padx=20, pady=(0, 6))
        self.business_menu = ctk.CTkOptionMenu(self.sidebar, values=["Tous les business"],
                                                command=self._on_business_menu, dynamic_resizing=False,
                                                fg_color=COLORS["surface2"], button_color=COLORS["surface3"])
        self.business_menu.pack(fill="x", padx=14, pady=(0, 23))
        self.nav_buttons = {}
        for page in PRIMARY:
            button = ctk.CTkButton(self.sidebar, text=page, anchor="w", height=43,
                                   fg_color="transparent", hover_color=COLORS["surface3"],
                                   text_color=COLORS["muted"], font=("Segoe UI", 12),
                                   command=lambda p=page: self._show_page(p))
            button.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[page] = button
        footer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=16, pady=18)
        self.side_worker = ctk.CTkLabel(footer, text="Worker : état inconnu", anchor="w",
                                        text_color=COLORS["muted"], font=("Segoe UI", 10))
        self.side_worker.pack(fill="x")
        if self._readonly:
            ctk.CTkLabel(footer, text="Mode consultation", anchor="w", text_color=COLORS["warn"],
                         font=("Segoe UI", 10)).pack(fill="x", pady=(5, 0))

        main = ctk.CTkFrame(self, fg_color=COLORS["bg"], corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 12))
        header.grid_columnconfigure(0, weight=1)
        self.page_title = ctk.CTkLabel(header, text="", anchor="w", text_color=COLORS["text"],
                                       font=("Segoe UI", 27, "bold"))
        self.page_title.grid(row=0, column=0, sticky="w")
        self.page_subtitle = ctk.CTkLabel(header, text="", anchor="w", text_color=COLORS["muted"],
                                          font=("Segoe UI", 11))
        self.page_subtitle.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.context_chip = ctk.CTkLabel(header, text="", text_color=COLORS["muted"],
                                         font=("Segoe UI", 10))
        self.context_chip.grid(row=0, column=1, sticky="e", padx=16)
        ctk.CTkButton(header, text="Actualiser", width=90, height=27,
                      fg_color=COLORS["surface3"], command=self._load_snapshot).grid(
                          row=0, column=2, sticky="e")
        self.header_status = ctk.CTkLabel(header, text="", text_color=COLORS["muted"],
                                          font=("Segoe UI", 10))
        self.header_status.grid(row=1, column=1, columnspan=2, sticky="e", padx=16)
        self.page_host = ctk.CTkFrame(main, fg_color="transparent")
        self.page_host.grid(row=1, column=0, sticky="nsew", padx=28, pady=(0, 16))
        self.page_host.grid_columnconfigure(0, weight=1)
        self.page_host.grid_rowconfigure(0, weight=1)

    def _bind_shortcuts(self) -> None:
        for index, page in enumerate(PRIMARY, start=1):
            self.bind_all(f"<Control-Key-{index}>", lambda _event, name=page: self._show_page(name))

    def _on_business_menu(self, label: str) -> None:
        selected = next((item.id for item in self._visible_businesses() if item.label() == label), DEFAULT_BUSINESS_ID)
        self.selected_business_id = selected
        self._sync_business_menu()
        self._show_page(self.current_page)
        self._load_snapshot()

    def _visible_businesses(self) -> list[Business]:
        known = self._visible_business_ids | set((self._snapshot or {}).get("businesses", []))
        known.discard(SYSTEM_BUSINESS)
        return [item for item in self.registry.all() if item.id in known]

    def _business_label(self) -> str:
        if self.selected_business_id == DEFAULT_BUSINESS_ID:
            return "Toutes les activités"
        return super()._business_label()

    def _sync_business_menu(self) -> None:
        self.business_menu.configure(values=["Toutes les activités"] +
                                     [item.label() for item in self._visible_businesses()])
        self.business_menu.set(self._business_label())
        self.context_chip.configure(text=self._business_label())

    def _select_business(self, business_id: str) -> None:
        self.selected_business_id = business_id
        self._sync_business_menu()
        self._show_page("Vue d'ensemble")
        self._load_snapshot()

    def _select_business_then(self, page: str, business_id: str) -> None:
        self.selected_business_id = business_id
        self._sync_business_menu()
        self._show_page(page)
        self._load_snapshot()

    def _show_page(self, page: str) -> None:
        if page == "Cockpit":
            page = "Vue d'ensemble"
        if page not in PRIMARY:
            super()._show_page(page)
            if self._readonly:
                self._disable_advanced_actions(self.page_host)
            return
        self.current_page = page
        for name, button in self.nav_buttons.items():
            active = name == page
            button.configure(fg_color=COLORS["surface3"] if active else "transparent",
                             text_color=COLORS["text"] if active else COLORS["muted"])
        title, subtitle = META[page]
        self.page_title.configure(text=title)
        self.page_subtitle.configure(text=subtitle)
        self.context_chip.configure(text=self._business_label())
        for child in self.page_host.winfo_children():
            child.destroy()
        body = ctk.CTkScrollableFrame(self.page_host, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew")
        self._body = body
        if self._snapshot_error:
            self._line(body, f"Lecture impossible : {self._snapshot_error}", COLORS["bad"])
            self._secondary(body, "Réessayer", self._load_snapshot)
            return
        if self._snapshot is None:
            self._line(body, "Chargement des données locales...", COLORS["muted"])
            return
        {"Vue d'ensemble": self._overview, "Missions": self._missions,
         "Livrables": self._deliverables, "Activité": self._activity,
         "Paramètres": self._settings, "Navigateur": self._browser}[page](body)
        body._parent_canvas.bind("<Configure>", lambda _event, frame=body: self._fit_scrollbar(frame), add="+")
        self.after(80, lambda frame=body: self._fit_scrollbar(frame))

    def _fit_scrollbar(self, body) -> None:
        if not body.winfo_exists():
            return
        bounds = body._parent_canvas.bbox("all")
        overflow = bool(bounds and bounds[3] - bounds[1] > body._parent_canvas.winfo_height() + 2)
        if overflow:
            body._scrollbar.grid()
        else:
            body._scrollbar.grid_remove()

    def _disable_advanced_actions(self, parent) -> None:
        for child in parent.winfo_children():
            if isinstance(child, ctk.CTkButton):
                child.configure(state="disabled")
            else:
                self._disable_advanced_actions(child)

    def _card(self, parent, title: str | None = None):
        card = ctk.CTkFrame(parent, fg_color=COLORS["surface"], border_width=1,
                            border_color=COLORS["border"], corner_radius=12)
        if title:
            self._line(card, title, COLORS["text"], size=14, bold=True, padx=18, pady=(16, 7))
        return card

    def _line(self, parent, text: str, color: str = COLORS["text"], *, size: int = 11,
              bold: bool = False, padx: int = 0, pady=0):
        label = ctk.CTkLabel(parent, text=str(text), text_color=color, anchor="w", justify="left",
                             font=("Segoe UI", size, "bold" if bold else "normal"), wraplength=800)
        label.pack(fill="x", padx=padx, pady=pady)
        return label

    def _secondary(self, parent, text: str, command):
        button = ctk.CTkButton(parent, text=text, command=command, height=32, width=150,
                               fg_color=COLORS["surface3"], hover_color=COLORS["border"])
        button.pack(anchor="w", padx=18, pady=(8, 15))
        return button

    def _section(self, parent, title: str):
        self._line(parent, title, size=16, bold=True, pady=(20, 9))

    def _overview(self, body) -> None:
        state = self._snapshot
        pending = [r for r in state["requests"] if r["status"] == "pending"]
        running = [t for t in state["tasks"] if t["status"] == "running" and
                   (t.get("lease_until") or 0) > time.time()]
        hero = self._card(body)
        hero.pack(fill="x", pady=(0, 12))
        label = "Votre intervention est attendue" if pending else "En activité" if running else "Prêt à démarrer ou reprendre"
        self._line(hero, label, size=22, bold=True, padx=20, pady=(19, 6))
        self._line(hero, "Finalité : obtenir, maintenir et améliorer une performance économique réelle.",
                   padx=20, pady=(0, 8))
        self._line(hero, "Premier démarrage : 0 EUR. Consultation et analyse. Trois cycles bornés, délai cible de deux minutes chacun.",
                   COLORS["muted"], padx=20, pady=(0, 8))
        button = self._secondary(hero, "Démarrer / reprendre OCTOPUS", self._start_pursuit)
        if self._readonly or self._creating:
            button.configure(state="disabled")
        self._secondary(hero, "Confier une mission", lambda: self._show_page("Missions"))
        if pending:
            self._line(hero, pending[0]["question"], COLORS["warn"], padx=20, pady=8)
            self._secondary(hero, "Répondre", lambda: self._show_page("Humain"))
        self._section(body, "Travail actuel")
        objectives = [o for o in state["objectives"] if o.get("success_criteria") == "bounded_determination"]
        if not objectives:
            self._line(body, "Aucune trajectoire encore déterminée. Aucun marché ni outil spécialisé imposé.", COLORS["muted"])
        for objective in objectives[:3]:
            card = self._card(body, objective["summary"])
            card.pack(fill="x", pady=(0, 8))
            self._line(card, mission_state(objective)[0], padx=18)
            latest = objective["work_tasks"][-1] if objective["work_tasks"] else {}
            result = latest.get("result") or {}
            self._line(card, result.get("reason") or mission_state(objective)[1], COLORS["muted"], padx=18, pady=8)
            if result.get("next_goal"):
                self._line(card, "Prochaine recherche proposée : " + result["next_goal"], padx=18, pady=8)
        self._section(body, "Résultats et moyens")
        self._line(body, f"Coût LLM calculé : {state['token_cost_usd']:g} USD. Le résultat économique se consulte dans les comptes, pas dans le nombre de tâches.",
                   COLORS["muted"])
        if state.get("pursuit_llm"):
            llm_budget = state["pursuit_llm"]
            self._line(body, f"Plafond LLM du travail : {llm_budget['spent_usd']:g} / {llm_budget['budget_usd']:g} USD "
                       f"({llm_budget['remaining_usd']:g} USD restants). Budget économique externe : 0 EUR.",
                       COLORS["muted"])
        self._line(body, "Exécution : " + self._worker_label() + ". Aucun appel Agnes automatique.", COLORS["muted"], pady=8)
        if state.get("browser"):
            self._secondary(body, "Voir le navigateur Hermes", lambda: self._show_page("Navigateur"))
        for task in state["tasks"]:
            result = task.get("result") or {}
            if result.get("rapport"):
                self._line(body, "Dernier rapport (analyse) : " + str(result["rapport"])[:650], pady=8)
                break

    def _missions(self, body) -> None:
        compose = self._card(body, "Confier un objectif libre")
        compose.pack(fill="x", pady=(0, 12))
        self._line(compose, "Décrivez le résultat recherché. OCTOPUS choisit les moyens dans les limites de consultation à 0 EUR.",
                   COLORS["muted"], padx=18, pady=(0, 8))
        self.mission_prompt = ctk.CTkTextbox(compose, height=85, wrap="word", fg_color=COLORS["surface2"])
        self.mission_prompt.pack(fill="x", padx=18, pady=(0, 8))
        self.mission_button = self._secondary(compose, "Confier la mission", self._create_mission)
        if self._readonly:
            self.mission_button.configure(state="disabled")
        self._section(body, "Objectifs et décisions")
        for objective in self._snapshot["objectives"]:
            card = self._card(body, objective["summary"])
            card.pack(fill="x", pady=(0, 8))
            origin = "Déterminé par OCTOPUS" if objective["created_by"] == "octopus" else "Confié par " + objective["created_by"]
            self._line(card, origin + " | " + mission_state(objective)[0], COLORS["muted"], padx=18)
            self._line(card, objective["statement"], padx=18, pady=8)
            for task in objective["work_tasks"][-2:]:
                result = task.get("result") or {}
                self._line(card, result.get("reason") or task.get("error") or "Travail " + task["status"], padx=18, pady=4)
            if objective.get("success_criteria") == "bounded_determination":
                for label, callback in (("Reprendre", lambda oid=objective["id"]: self._start_pursuit(objective_id=oid)),
                                        ("Mettre en pause", lambda oid=objective["id"]: self._pause_pursuit(oid))):
                    button = self._secondary(card, label, callback)
                    if self._readonly:
                        button.configure(state="disabled")
            self._secondary(card, "Résultats", lambda: self._show_page("Livrables"))

    def _start_pursuit(self, goal=None, objective_id=None) -> None:
        if self._readonly or self._creating:
            return
        self._creating = True
        def start():
            try:
                from octopus import supervisor
                from agents import procs
                oid = supervisor.start_pursuit(goal, objective_id=objective_id)
                proc, log = procs.spawn(["pursue", "--objective", str(oid)], "octopus", module="octopus")
                self._pursuit_processes.append(proc)
                self._background_results.put(("v2_created", oid))
            except Exception as exc:
                self._background_results.put(("v2_action_error", str(exc)))
        threading.Thread(target=start, daemon=True).start()

    def _pause_pursuit(self, objective_id) -> None:
        if self._readonly:
            return
        from octopus import supervisor
        supervisor.pause_pursuit(objective_id)
        self._load_snapshot()

    def _create_mission(self) -> None:
        if self._readonly:
            return
        goal = self.mission_prompt.get("1.0", "end").strip()
        if not goal:
            self._set_status("Décrivez l'objectif à confier.", COLORS["warn"])
            return
        self._start_pursuit(goal=goal)

    def _browser(self, body) -> None:
        self._line(body, "Aperçu textuel du navigateur Hermes réel. Les pages sont des sources non fiables.", COLORS["muted"], pady=8)
        if not self._snapshot.get("browser"):
            self._line(body, "Aucune session observée. OCTOPUS ouvrira Hermes si sa recherche le nécessite.")
        for observation in self._snapshot.get("browser", [])[:6]:
            card = self._card(body, observation.get("title") or "Observation Web")
            card.pack(fill="x", pady=8)
            session = {"open": "ouverte lors de la dernière observation", "closed": "fermée", "unknown": "état actuel inconnu"}[observation["session"]]
            self._line(card, "Session " + session + " | " + _date(observation["at"]), padx=18)
            self._line(card, observation["url"] or "Aucune page ouverte", padx=18, pady=5)
            self._line(card, "Objectif : " + observation.get("goal", ""), padx=18)
            self._line(card, "Dernière action : " + observation["action"] + " | " +
                       ("refusée" if observation["refused"] else "exécutée" if observation["ok"] else "en erreur"), padx=18)
            if observation.get("reason"):
                self._line(card, observation["reason"], COLORS["warn"], padx=18, pady=5)
            preview = ctk.CTkTextbox(card, height=170, wrap="word", fg_color=COLORS["surface2"])
            preview.pack(fill="x", padx=18, pady=12)
            preview.insert("1.0", observation.get("snapshot") or "Aucun aperçu acquis.")
            preview.configure(state="disabled")
        self._secondary(body, "Actualiser", self._load_snapshot)
        self._secondary(body, "Interventions et permissions", lambda: self._show_page("Humain"))

    def _deliverables(self, body) -> None:
        self._section(body, "Rapports et analyses")
        reports = [t for t in self._snapshot["tasks"] if (t.get("result") or {}).get("rapport")]
        if not reports:
            self._line(body, "Aucun rapport conservé.", COLORS["muted"])
        for task in reports[:12]:
            card = self._card(body, f"Travail #{task['id']} | {task['status']}")
            card.pack(fill="x", pady=8)
            text = ctk.CTkTextbox(card, height=180, wrap="word")
            text.pack(fill="x", padx=18, pady=12)
            text.insert("1.0", str(task["result"]["rapport"]))
            text.configure(state="disabled")
            self._line(card, "Analyse du modèle. Les affirmations demandent leurs sources ; aucun revenu déduit.",
                       COLORS["muted"], padx=18, pady=8)
        self._section(body, "Décisions conservées")
        for decision in self._snapshot.get("decisions", [])[:12]:
            self._line(body, decision["decision"] + " : " + decision["rationale"], pady=5)
        self._section(body, "Preuves enregistrées")
        for evidence in self._snapshot.get("evidence", [])[:12]:
            self._line(body, f"{evidence['nature']} | {evidence['status']} | {evidence['summary']} | {evidence['source_ref']}", pady=5)
        self._section(body, "Vidéos et fichiers vérifiés")
        generations = self._snapshot["generations"]
        if not generations:
            self._line(body, "Aucune génération Agnes enregistrée pour ce contexte.", COLORS["muted"])
            return
        self._line(body, f"{sum(g['verified'] for g in generations)} MP4 vérifié(s) sur {len(generations)} génération(s)",
                   COLORS["muted"], pady=(0, 12))
        economic = [item for item in generations if item["business"] != SYSTEM_BUSINESS and item["objective_id"]]
        technical = [item for item in generations if item not in economic]
        for section, items in (("Livrables économiques", economic), ("Validations techniques", technical)):
            if items:
                self._section(body, section)
            for item in items:
                card = self._card(body)
                card.pack(fill="x", pady=(0, 8))
                title = (f"Validation technique Agnes #{item['id']}" if section == "Validations techniques"
                         else f"Vidéo Agnes #{item['id']}")
                self._line(card, title, size=15, bold=True, padx=18, pady=(13, 1))
                context = "Système OCTOPUS" if item["business"] == SYSTEM_BUSINESS else item["business"]
                self._line(card, f"{context}  |  {_date(item['created_at'])}  |  "
                           f"{(item['file_size'] or 0) / 1048576:.2f} Mo", COLORS["muted"], padx=18)
                status = "Vérifié" if item["verified"] else f"Non vérifié : {item['verification_reason']}"
                self._line(card, status, COLORS["good"] if item["verified"] else COLORS["warn"],
                           padx=18, pady=(5, 1))
                self._line(card, f"Objectif #{item['objective_id']}" if item["objective_id"] else
                           "Aucun objectif économique : génération de test", COLORS["muted"], padx=18)
                self._line(card, f"Preuve #{item['evidence_id'] or 'absente'}  |  SHA-256 : {item['sha256'] or 'inconnu'}",
                           COLORS["muted"], padx=18, pady=(3, 1))
                if item["verified"]:
                    buttons = ctk.CTkFrame(card, fg_color="transparent")
                    buttons.pack(fill="x", padx=18, pady=(5, 12))
                    for label, path in (("Ouvrir le MP4", item["output_path"]),
                                        ("Ouvrir le dossier", str(Path(item["output_path"]).parent))):
                        ctk.CTkButton(buttons, text=label, width=150, height=31,
                                      fg_color=COLORS["surface3"], command=lambda p=path: _open_path(p)).pack(side="left", padx=(0, 8))
                else:
                    self._line(card, item["status"], COLORS["muted"], padx=18, pady=(0, 12))

    def _activity(self, body) -> None:
        events = self._snapshot["events"]
        llm_calls = self._snapshot.get("llm_calls", [])
        pending = [r for r in self._snapshot["requests"] if r["status"] == "pending"]
        if pending:
            card = self._card(body, "Votre intervention")
            card.pack(fill="x", pady=(0, 12))
            for request in pending:
                self._line(card, request["question"], padx=18, pady=(0, 8))
            self._secondary(card, "Ouvrir les réponses", lambda: self._show_page("Humain"))
        if llm_calls:
            self._section(body, "Routage LLM récent")
            fallbacks = sum(call["fallback"] for call in llm_calls)
            self._line(body, f"Replis observés : {fallbacks}", COLORS["muted"], pady=(0, 8))
            for call in llm_calls[:10]:
                card = self._card(body)
                card.pack(fill="x", pady=(0, 6))
                route = call["resolved_model"] or call["model"]
                provider = call["resolved_provider"] or call["provider"]
                cost_kind = "observé" if call["provider_cost_usd"] is not None else "calculé"
                free_or_paid = "payant" if call["cost_class"] == "paid" else "gratuit"
                self._line(card, f"{provider} / {route}  |  {free_or_paid}  |  "
                           f"{call['cost_usd']:g} USD {cost_kind}  |  {call['status']}",
                           size=13, bold=True, padx=16, pady=(9, 0))
                if call.get("route_reason"):
                    self._line(card, str(call["route_reason"])[:250], COLORS["muted"], padx=16, pady=(0, 9))
        if not events:
            self._line(body, "Aucun événement récent pour ce contexte.", COLORS["muted"])
            return
        self._line(body, "Les événements reflètent des actions techniques. Une tâche terminée ne clôt pas son objectif.",
                   COLORS["muted"], pady=(0, 10))
        for event in events:
            card = self._card(body)
            card.pack(fill="x", pady=(0, 6))
            self._line(card, _event_label(event), size=13, bold=True, padx=16, pady=(9, 0))
            context = "Système OCTOPUS" if event["business"] == SYSTEM_BUSINESS else event["business"] or "global"
            self._line(card, f"{_date(event['ts'])}  |  {context}" +
                       (f"  |  tâche #{event['task_id']}" if event["task_id"] else ""),
                       COLORS["muted"], padx=16, pady=(0, 2))
            if _event_detail(event):
                self._line(card, _event_detail(event), COLORS["muted"], padx=16, pady=(0, 9))

    def _settings(self, body) -> None:
        state = self._snapshot
        self._section(body, "Services et permissions")
        card = self._card(body)
        card.pack(fill="x")
        self._line(card, f"Exécution : {self._worker_label()}", padx=18, pady=(13, 1))
        self._line(card, "Agnes : " + state["agnes_health"] + ". La création exige un canal actif avec accès act.",
                   COLORS["muted"], padx=18, pady=(0, 8))
        self._line(card, "Démarrez depuis la vue d'ensemble. Mettez chaque objectif en pause depuis Missions.",
                   COLORS["muted"], padx=18, pady=(0, 12))
        channels = state["channels"]
        if not channels:
            self._line(body, "Aucun canal économique déclaré.", COLORS["muted"], pady=(11, 0))
        for channel in channels:
            scope = "Système OCTOPUS - validation technique" if channel["business"] == SYSTEM_BUSINESS else channel["business"]
            self._line(body, f"{channel['name']} ({channel['kind']}) - {scope} : "
                       f"{channel['status']}  |  accès {channel['access']}",
                       COLORS["muted"], pady=(8, 0))
        active_allowances = [a for a in state["allowances"] if a["status"] == "active"]
        self._line(body, f"Enveloppes actives : {len(active_allowances)}", COLORS["muted"], pady=(10, 0))
        for allowance in active_allowances:
            self._line(body, f"#{allowance['id']}  {allowance['amount']:g} {allowance['currency']}  "
                       f"accordés par {allowance['granted_by']}", COLORS["muted"], pady=(4, 0))
        self._section(body, "Outils avancés")
        self._line(body, "Les vues historiques restent accessibles ici.", COLORS["muted"], pady=(0, 8))
        grid = ctk.CTkFrame(body, fg_color="transparent")
        grid.pack(fill="x")
        for index, page in enumerate(ADVANCED):
            ctk.CTkButton(grid, text=page + (" (legacy)" if page == "Production" else ""),
                          fg_color=COLORS["surface3"], height=36,
                          command=lambda name=page: self._show_page(name)).grid(
                              row=index // 4, column=index % 4, sticky="ew", padx=3, pady=3)
        for index in range(4):
            grid.grid_columnconfigure(index, weight=1)

    def _worker_label(self) -> str:
        if any(p.poll() is None for p in self._pursuit_processes):
            return "OCTOPUS en cours"
        if self.worker_proc and self.worker_proc.poll() is None:
            return "Actif dans cette fenêtre"
        tasks = (self._snapshot or {}).get("tasks", [])
        if any(t["status"] == "running" and (t.get("lease_until") or 0) > time.time() for t in tasks):
            return "Activité détectée"
        return "Non démarré ici"

    def _toggle_worker(self) -> None:
        if self._readonly:
            self._set_status("Mode consultation : Worker désactivé.", COLORS["warn"])
            return
        if self._worker_label() == "Activité détectée" and not self.worker_proc:
            self._set_status("Un autre Worker est actif ; contrôlez-le depuis sa session.", COLORS["warn"])
            return
        super()._toggle_worker()
        if self.current_page == "Paramètres":
            self._show_page("Paramètres")

    def _load_snapshot(self) -> None:
        if self._snapshot_busy:
            return
        self._snapshot_busy = True
        business = self.selected_business_id
        check_health = not self._readonly and self.current_page == "Paramètres"

        def run():
            try:
                snapshot = read_snapshot(business, check_health=check_health)
                self._background_results.put(("v2_snapshot", business, snapshot))
            except Exception as exc:
                self._background_results.put(("v2_error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=run, daemon=True).start()

    def _refresh(self) -> None:
        while True:
            try:
                result = self._background_results.get_nowait()
            except queue.Empty:
                break
            kind = result[0]
            if kind == "v2_snapshot":
                self._snapshot_busy = False
                if result[1] == self.selected_business_id:
                    self._snapshot = result[2]
                    self._visible_business_ids.update(self._snapshot["businesses"])
                    for business_id in self._snapshot["businesses"]:
                        if business_id != SYSTEM_BUSINESS:
                            self.registry._businesses.setdefault(business_id, Business(business_id, business_id.title()))
                    self._sync_business_menu()
                    self._snapshot_error = None
                    self._snapshot_at = time.monotonic()
                    if self.current_page in PRIMARY:
                        editing = (self.current_page == "Missions" and hasattr(self, "mission_prompt") and
                                   self.mission_prompt.winfo_exists() and
                                   self.mission_prompt.get("1.0", "end").strip())
                        if not editing:
                            self._show_page(self.current_page)
                if self._reload_after_create:
                    self._reload_after_create = False
                    self._load_snapshot()
            elif kind == "v2_created":
                self._creating = False
                self._set_status(f"Mission #{result[1]} enregistrée. Consultez son état et ses résultats.", COLORS["good"])
                if self._snapshot_busy:
                    self._reload_after_create = True
                else:
                    self._load_snapshot()
            elif kind == "v2_action_error":
                self._creating = False
                self._set_status(result[1][:110], COLORS["bad"])
            elif kind == "v2_error":
                self._snapshot_busy = False
                self._snapshot_error = result[1]
                self._snapshot_at = time.monotonic()
                self._set_status(result[1][:110], COLORS["bad"])
                if self.current_page in PRIMARY:
                    self._show_page(self.current_page)
            else:
                self._background_results.put(result)
                super()._drain_background_results()
                break
        self.side_worker.configure(text="Exécution : " + self._worker_label())
        if not self._snapshot_busy and time.monotonic() - self._snapshot_at > 5:
            self._load_snapshot()
        self.after(1500, self._refresh)


def main() -> None:
    WorkbenchV2().mainloop()
