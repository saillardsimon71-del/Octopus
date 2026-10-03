"""OCTOPUS Workbench V2: mission-first desktop interface."""
from __future__ import annotations

import os
import json
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
PAGE_DATA_KEYS = {
    "Vue d'ensemble": ("activities", "requests", "tasks", "objectives", "token_cost_usd", "pursuit_llm", "browser"),
    "Missions": ("objectives",),
    "Livrables": ("tasks", "decisions", "evidence", "generations"),
    "Navigateur": ("browser",),
    "Activité": ("requests", "llm_calls", "events"),
    "Paramètres": ("channels", "allowances", "agnes_health", "tasks", "accounts", "mandates", "requests"),
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
        selected = next((item.id for item in self._visible_businesses() if self._activity_label(item) == label), DEFAULT_BUSINESS_ID)
        self._select_business_then(self.current_page, selected)

    @staticmethod
    def _activity_label(item) -> str:
        return "Discovery autonome (historique)" if item.id == SYSTEM_BUSINESS else f"{item.label()} [{item.id}]"

    def _visible_businesses(self) -> list[Business]:
        known = self._visible_business_ids | set((self._snapshot or {}).get("businesses", []))
        from octopus import businesses
        known.update(businesses.discover())
        return [item for item in self.registry.all() if item.id in known]

    def _business_label(self) -> str:
        if self.selected_business_id == DEFAULT_BUSINESS_ID:
            return "Toutes les activités"
        item = self.registry.get(self.selected_business_id)
        return self._activity_label(item) if item else self.selected_business_id

    def _sync_business_menu(self) -> None:
        self.business_menu.configure(values=["Toutes les activités"] +
                                     [self._activity_label(item) for item in self._visible_businesses()])
        self.business_menu.set(self._business_label())
        self.context_chip.configure(text=self._business_label())

    def _select_business(self, business_id: str) -> None:
        self._select_business_then("Vue d'ensemble", business_id)

    def _select_business_then(self, page: str, business_id: str) -> None:
        if business_id != self.selected_business_id:
            self._snapshot = None
            self._snapshot_error = None
            self._snapshot_at = 0.0
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
        previous_page = self.current_page
        previous_children = self.page_host.winfo_children()
        old_body = (self._body if previous_page == page and hasattr(self, "_body") and
                    self._body.winfo_exists() else None)
        scroll = old_body._parent_canvas.yview()[0] if old_body else 0.0
        draft = None
        restore_focus = False
        if page == "Missions" and old_body and hasattr(self, "mission_prompt") and self.mission_prompt.winfo_exists():
            draft = self.mission_prompt.get("1.0", "end-1c")
            cursor = self.mission_prompt.index("insert")
            restore_focus = self.focus_lastfor() == self.mission_prompt._textbox
        self.current_page = page
        for name, button in self.nav_buttons.items():
            active = name == page
            button.configure(fg_color=COLORS["surface3"] if active else "transparent",
                             text_color=COLORS["text"] if active else COLORS["muted"])
        title, subtitle = META[page]
        self.page_title.configure(text=title)
        self.page_subtitle.configure(text=subtitle)
        self.context_chip.configure(text=self._business_label())
        body = ctk.CTkScrollableFrame(self.page_host, fg_color="transparent")
        self._body = body
        if self._snapshot_error:
            self._line(body, f"Lecture impossible : {self._snapshot_error}", COLORS["bad"])
            self._secondary(body, "Réessayer", self._load_snapshot)
        elif self._snapshot is None:
            self._line(body, "Chargement des données locales...", COLORS["muted"])
        else:
            {"Vue d'ensemble": self._overview, "Missions": self._missions,
             "Livrables": self._deliverables, "Activité": self._activity,
             "Paramètres": self._settings, "Navigateur": self._browser}[page](body)
        if draft is not None and self._snapshot is not None and not self._snapshot_error:
            self.mission_prompt.insert("1.0", draft)
            self.mission_prompt.mark_set("insert", cursor)
        body.grid(row=0, column=0, sticky="nsew")
        if previous_children:
            body._parent_frame.lower()
            self.update_idletasks()
            body._parent_canvas.yview_moveto(scroll)
            body._parent_frame.lift()
        if old_body:
            old_body.destroy()
        for child in previous_children:
            if child.winfo_exists():
                child.destroy()
        if restore_focus:
            self.mission_prompt._textbox.focus_set()
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

    def _show_activity_form(self) -> None:
        if self._readonly:
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("Ajouter une activité")
        dialog.geometry("560x430")
        self._line(dialog, "Nom", padx=20, pady=(18, 4))
        name = ctk.CTkEntry(dialog)
        name.pack(fill="x", padx=20)
        self._line(dialog, "Description de l'activité", padx=20, pady=(15, 4))
        description = ctk.CTkTextbox(dialog, height=150, wrap="word")
        description.pack(fill="x", padx=20)
        self._line(dialog, "Définissez le terrain. OCTOPUS choisira les moyens. Créer ne démarre aucun travail.",
                   COLORS["muted"], padx=20, pady=10)
        error = self._line(dialog, "", COLORS["bad"], padx=20)
        def submit():
            try:
                if self._create_activity(name.get(), description.get("1.0", "end")):
                    dialog.destroy()
            except Exception as exc:
                error.configure(text=str(exc))
        self._secondary(dialog, "Créer l'activité", submit).configure(width=210)
        dialog.transient(self)
        dialog.grab_set()
        name.focus_set()

    def _create_activity(self, name: str, description: str):
        if self._readonly:
            return None
        from octopus import businesses
        item = businesses.create_activity(name, description)
        self.registry.reload()
        self._visible_business_ids.add(item.id)
        self._select_business(item.id)
        self._set_status("Activité créée, prête et non démarrée.", COLORS["good"])
        return item

    def _cash_lines(self, body, activity) -> None:
        if not activity["cash"]:
            self._line(body, "Aucun encaissement enregistré. Les coûts non enregistrés restent inconnus.", COLORS["muted"])
        for currency, amounts in activity["cash"].items():
            self._line(body, f"Clients encaissés (observés) : {amounts['customer_receipts']:g} {currency} ; "
                       f"sorties observées : {amounts['out_observed']:g} {currency} ; "
                       f"solde ledger calculé : {amounts['net_observed']:g} {currency}.", COLORS["muted"])
            self._line(body, f"Entrées non vérifiées : {amounts['in_unverified']:g} {currency} ; "
                       f"sorties non vérifiées : {amounts['out_unverified']:g} {currency}. "
                       "Le solde inclut les apports ; il ne prouve pas une marge complète.", COLORS["muted"])

    def _portfolio(self, body) -> None:
        hero = self._card(body, "Portefeuille d'activités")
        hero.pack(fill="x", pady=(0, 12))
        self._line(hero, "Déclarez un terrain économique, puis démarrez explicitement son travail.", padx=18, pady=8)
        button = self._secondary(hero, "+ Ajouter une activité", self._show_activity_form)
        button.configure(width=220)
        if self._readonly:
            button.configure(state="disabled")
        self._line(hero, f"Coûts LLM enregistrés, portefeuille : {self._snapshot['token_cost_usd']:g} USD.",
                   COLORS["muted"], padx=18, pady=(0, 12))
        totals = {}
        for item in self._snapshot.get("activities", []):
            for currency, amounts in item["cash"].items():
                cur = totals.setdefault(currency, {key: 0. for key in amounts})
                for key, value in amounts.items():
                    cur[key] += value
        self._cash_lines(hero, {"cash": totals})
        for item in sorted(self._snapshot.get("activities", []), key=lambda row: row["id"] == SYSTEM_BUSINESS):
            card = self._card(body, item["name"])
            card.pack(fill="x", pady=(0, 10))
            self._line(card, item["state"] + f" | Dernière activité : {_date(item['last_activity']) if item['last_activity'] else 'aucune'}",
                       padx=18, pady=4)
            if item["objective"]:
                self._line(card, "Objectif : " + item["objective"]["summary"], padx=18, pady=4)
            self._line(card, f"LLM : {item['llm_cost_usd']:g} USD | Demandes humaines : {item['pending_human']}",
                       COLORS["muted"], padx=18, pady=4)
            self._cash_lines(card, item)
            self._secondary(card, "Sélectionner", lambda bid=item["id"]: self._select_business(bid))
            start = self._secondary(card, "Démarrer / reprendre", lambda bid=item["id"]: self._start_pursuit(business=bid))
            if self._readonly or self._creating:
                start.configure(state="disabled")

    def _overview(self, body) -> None:
        state = self._snapshot
        if self.selected_business_id == DEFAULT_BUSINESS_ID:
            self._portfolio(body)
            return
        pending = [r for r in state["requests"] if r["status"] == "pending" and not r.get("technical_obsolete")]
        obsolete = [r for r in state["requests"] if r.get("technical_obsolete")]
        running = [t for t in state["tasks"] if t["status"] == "running" and
                   (t.get("lease_until") or 0) > time.time()]
        hero = self._card(body)
        hero.pack(fill="x", pady=(0, 12))
        label = "Votre intervention est attendue" if pending else "En activité" if running else "Reprise technique disponible" if obsolete else "Prêt à démarrer ou reprendre"
        self._line(hero, label, size=22, bold=True, padx=20, pady=(19, 6))
        self._line(hero, "Finalité : obtenir, maintenir et améliorer une performance économique réelle.",
                   padx=20, pady=(0, 8))
        self._line(hero, "Premier démarrage : 0 EUR. Consultation et analyse. Trois cycles bornés, délai cible de deux minutes chacun.",
                   COLORS["muted"], padx=20, pady=(0, 8))
        if obsolete:
            self._line(hero, "Une ancienne interruption technique sera réconciliée au clic sur Reprendre. Aucune permission supplémentaire requise.",
                       COLORS["muted"], padx=20, pady=(0, 8))
        activity = next((item for item in state.get("activities", []) if item["id"] == self.selected_business_id), None)
        if activity and activity["description"]:
            self._line(hero, activity["description"], COLORS["muted"], padx=20, pady=(0, 8))
        button = self._secondary(hero, "Démarrer / reprendre l'activité" if self.selected_business_id != SYSTEM_BUSINESS
                                 else "Démarrer / reprendre discovery", self._start_pursuit)
        button.configure(width=280)
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
        if activity:
            self._cash_lines(body, activity)
        self._line(body, f"Coût LLM calculé : {state['token_cost_usd']:g} USD. Le résultat économique se consulte dans les comptes, pas dans le nombre de tâches.",
                   COLORS["muted"])
        if state.get("pursuit_llm"):
            self._line(body, f"Coût LLM enregistré pour cet objectif : {state['pursuit_llm']['spent_usd']:g} USD. "
                       "Budget économique externe : 0 EUR.",
                       COLORS["muted"])
        self.overview_worker = self._line(body, "Exécution : " + self._worker_label() +
                                          ". Aucun appel Agnes automatique.", COLORS["muted"], pady=8)
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
        if self._readonly or self.selected_business_id == DEFAULT_BUSINESS_ID:
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
                for label, callback in (("Reprendre", lambda oid=objective["id"], bid=objective["business"]: self._start_pursuit(objective_id=oid, business=bid)),
                                        ("Mettre en pause", lambda oid=objective["id"], bid=objective["business"]: self._pause_pursuit(oid, business=bid))):
                    button = self._secondary(card, label, callback)
                    if self._readonly:
                        button.configure(state="disabled")
            self._secondary(card, "Résultats", lambda: self._show_page("Livrables"))

    def _start_pursuit(self, goal=None, objective_id=None, business=None) -> None:
        if self._readonly or self._creating:
            return
        business = business or self.selected_business_id
        if business == DEFAULT_BUSINESS_ID:
            self._set_status("Sélectionnez une activité avant de démarrer.", COLORS["warn"])
            return
        self._creating = True
        def start():
            try:
                from octopus import supervisor
                from agents import procs
                oid = supervisor.start_pursuit(goal, objective_id=objective_id, business=business)
                if any(t["status"] == "waiting_human" for t in supervisor.work_tasks(business, oid)):
                    self._background_results.put(("v2_action_error",
                        "Reprise suspendue : une réponse est attendue dans Humain. Aucune permission supplémentaire accordée."))
                    return
                proc, log = procs.spawn(["pursue", "--business", business, "--objective", str(oid)], "octopus", module="octopus")
                self._pursuit_processes.append((business, proc))
                self._background_results.put(("v2_created", oid))
            except Exception as exc:
                self._background_results.put(("v2_action_error", str(exc)))
        threading.Thread(target=start, daemon=True).start()

    def _pause_pursuit(self, objective_id, business=None) -> None:
        if self._readonly:
            return
        from octopus import supervisor
        supervisor.pause_pursuit(objective_id, business=business or self.selected_business_id)
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
        pending = [r for r in self._snapshot["requests"] if r["status"] == "pending" and not r.get("technical_obsolete")]
        obsolete = [r for r in self._snapshot["requests"] if r.get("technical_obsolete")]
        if obsolete:
            card = self._card(body, "Ancienne erreur technique — reprise disponible")
            card.pack(fill="x", pady=(0, 12))
            for request in obsolete:
                self._line(card, request["question"].split(" Une réponse seule")[0], padx=18, pady=(0, 8))
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
        self._line(body, "Moteur, outils installés et configuration LLM : partagés. Canaux et enveloppes ci-dessous : contexte sélectionné.",
                   COLORS["muted"], pady=(0, 8))
        self._resource_hub(body)
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

    def _resource_hub(self, body):
        self._section(body, "Comptes et ressources connectés")
        self._line(body, "Une connexion rend la ressource disponible. Les mandats définissent les actions autorisées pour chaque activité.", COLORS['muted'])
        if not self._readonly:
            self._secondary(body, "Ajouter un compte", self._account_form)
        labels = {'absent': 'Absent', 'connection_required': 'Connexion humaine requise',
                  'connected': 'Connecté', 'expired': 'Session expirée', 'unavailable': 'Indisponible'}
        for row in self._snapshot.get('accounts', []):
            account = row.get('web_account') or {}
            card = self._card(body)
            card.pack(fill='x', pady=4)
            self._line(card, row['label'] + ' · ' + account.get('provider', row['kind']), padx=18, pady=(10, 2), bold=True)
            self._line(card, labels.get(account.get('session_status', 'absent'), 'Absent') +
                       (' · Désactivé' if account and not account.get('enabled') else '') +
                       ' · ' + account.get('ownership', 'À définir') +
                       (' · Dédié à OCTOPUS' if account.get('dedicated') else ''), padx=18, pady=2)
            self._line(card, 'Activités autorisées : ' + (', '.join(account.get('businesses', [])) or 'Aucune') +
                       '\nCapacités constatées : ' + (', '.join(row.get('capabilities', [])) or 'Non constatées') +
                       '\nDernière vérification : ' + _date(row.get('last_check_at')), COLORS['muted'], padx=18, pady=2)
            related = [m for m in self._snapshot.get('mandates', []) if m['status'] == 'active'
                       and m['target'] == 'owned_account' and m['business'] in account.get('businesses', [])
                       and (row['key'] in json.loads(m['resource_keys']) or '*' in json.loads(m['resource_keys']))]
            self._line(card, 'Mandats : ' + (' ; '.join(m['label'] for m in related) or 'Aucun dans ce contexte'),
                       COLORS['muted'], padx=18, pady=2)
            if not self._readonly:
                self._secondary(card, 'Configurer / modifier', lambda key=row['key']: self._account_form(key))
                if account and account.get('enabled'):
                    self._secondary(card, 'Ouvrir la connexion', lambda key=row['key']: self._open_account(key))
                    self._secondary(card, 'J’ai terminé — vérifier', lambda key=row['key']: self._verify_account(key))
                    self._secondary(card, 'Désactiver', lambda key=row['key']: self._disable_account(key))
        self._section(body, 'Demandes de ressources')
        found = False
        for request in self._snapshot.get('requests', []):
            context = json.loads(request.get('context') or '{}')
            info = context.get('web_request')
            if request['status'] != 'pending' or not info:
                continue
            found = True
            card = self._card(body)
            card.pack(fill='x', pady=4)
            self._line(card, info['platform'] + ' · ' + request['business'], padx=18, pady=(10, 2), bold=True)
            self._line(card, info['reason'] + '\nCapacités souhaitées : ' + ', '.join(info.get('capabilities', [])) +
                       '\nActions envisagées : ' + ', '.join(info.get('desired_actions', [])), padx=18, pady=2)
            if not self._readonly:
                self._secondary(card, 'Accepter / connecter', lambda c=context, b=request['business']: self._account_form(c['key'], c, b))
                self._secondary(card, 'Refuser', lambda rid=request['id']: self._decline_resource(rid))
                self._secondary(card, 'Reporter', lambda: self._set_status('Demande conservée en attente.'))
        if not found:
            self._line(body, 'Aucune demande de compte en attente.', COLORS['muted'])
        self._section(body, 'Mandats accordés à cette activité')
        for mandate in self._snapshot.get('mandates', []):
            self._line(body, f"#{mandate['id']} {mandate['label']} · {mandate['business']} · {mandate['status']} · " +
                       ', '.join(json.loads(mandate['effects'])), pady=(6, 1))
            if mandate['status'] == 'active' and not self._readonly:
                self._secondary(body, 'Révoquer ce mandat', lambda m=mandate: self._revoke_mandate(m))
                self._secondary(body, 'Modifier ce mandat', lambda m=mandate: self._mandate_form(m))
        if not self._readonly and self.selected_business_id != DEFAULT_BUSINESS_ID:
            self._secondary(body, 'Accorder un mandat', self._mandate_form)

    def _hub_error(self, error):
        self._set_status(str(error)[:200], COLORS['warn'])

    def _account_form(self, key=None, request=None, business=None):
        if self._readonly:
            return
        from octopus import resources
        row = resources.get(key) if key else None
        a = (row or {}).get('web_account') or {}
        request = (request or {}).get('web_request') or {}
        url = a.get('verify_url') or request.get('url') or (row or {}).get('locator') or ''
        from urllib.parse import urlsplit
        current_business = business or self.selected_business_id
        window = ctk.CTkToplevel(self)
        window.title('Configurer une ressource — aucun mot de passe')
        window.geometry('680x700')
        panel = ctk.CTkScrollableFrame(window)
        panel.pack(fill='both', expand=True, padx=16, pady=16)
        self._line(panel, 'Les domaines définissent le périmètre des tâches OCTOPUS après connexion. La connexion humaine peut utiliser le Web public, ses CDN et OAuth sans les ajouter ici. Choisissez un texte visible uniquement après connexion, par exemple « Déconnexion » sur votre tableau de bord.', COLORS['muted'])
        values = [
            ('key', 'Identifiant unique', key or ''),
            ('provider', 'Plateforme', a.get('provider') or request.get('platform') or ''),
            ('label', 'Nom lisible', (row or {}).get('label') or ''),
            ('url', 'URL de connexion HTTPS', (row or {}).get('locator') or request.get('url') or ''),
            ('domains', 'Domaines autorisés aux tâches OCTOPUS, séparés par virgules', ','.join(a.get('domains', []) or [urlsplit(url).hostname or ''])),
            ('businesses', 'Identifiants des activités autorisées, séparés par virgules', ','.join(a.get('businesses', []) or ([current_business] if current_business != DEFAULT_BUSINESS_ID else []))),
            ('verify_url', 'URL de la page après connexion', url),
            ('authenticated_text', 'Texte visible uniquement après connexion', a.get('authenticated_text', ''))]
        fields = {}
        for name, label, value in values:
            self._line(panel, label, pady=(7, 2))
            field = ctk.CTkEntry(panel, height=30)
            field.insert(0, value)
            field.pack(fill='x')
            fields[name] = field
        self._line(panel, 'Propriétaire', pady=(7, 2))
        owner = ctk.CTkOptionMenu(panel, values=['operator', 'business', 'other'])
        owner.set(a.get('ownership', 'operator'))
        owner.pack(anchor='w')
        dedicated = ctk.BooleanVar(value=a.get('dedicated', False))
        ctk.CTkCheckBox(panel, text='Compte dédié aux activités OCTOPUS', variable=dedicated).pack(anchor='w', pady=8)
        def save():
            try:
                data = {name: f.get().strip() for name, f in fields.items()}
                resource_key = data.pop('key')
                if key and resource_key != key:
                    raise ValueError('La clé d’une ressource existante ne peut pas changer')
                data['domains'] = [d.strip() for d in data['domains'].split(',') if d.strip()]
                data['businesses'] = [b.strip() for b in data['businesses'].split(',') if b.strip()]
                resources.configure_account(resource_key, actor='human', ownership=owner.get(), dedicated=dedicated.get(), **data)
                window.destroy()
                self._load_snapshot()
                self._open_account(resource_key)
            except Exception as exc:
                self._hub_error(exc)
        self._secondary(panel, 'Enregistrer et ouvrir la connexion', save)
        window.transient(self)
        window.grab_set()
        window.lift()
        fields['key'].focus_set()

    def _mandate_form(self, previous=None):
        if self._readonly or self.selected_business_id == DEFAULT_BUSINESS_ID:
            return
        from octopus import mandates
        business = previous['business'] if previous else self.selected_business_id
        window = ctk.CTkToplevel(self)
        window.title('Mandat · ' + business)
        window.geometry('630x530')
        panel = ctk.CTkFrame(window)
        panel.pack(fill='both', expand=True, padx=16, pady=16)
        self._line(panel, 'Les dépenses, secrets, sécurité, suppressions et engagements sensibles exigent une autorisation distincte.', COLORS['muted'])
        self._line(panel, 'Nom du mandat', pady=(10, 2))
        label = ctk.CTkEntry(panel)
        label.pack(fill='x')
        label.insert(0, previous['label'] if previous else 'Opérations commerciales')
        self._line(panel, 'Périmètre', pady=(10, 2))
        target = ctk.CTkOptionMenu(panel, values=['public_business', 'owned_account'])
        target.set(previous['target'] if previous else 'public_business')
        target.pack(anchor='w')
        self._line(panel, 'public_business : contact professionnel public sans dépense.\nowned_account : comptes connectés explicitement ouverts à cette activité.', COLORS['muted'])
        selected = set(json.loads(previous['effects'])) if previous else {'contact'}
        checks = {}
        for effect, title in [('read', 'Lire les comptes'), ('contact', 'Contacter / répondre'), ('publish', 'Publier'), ('edit', 'Modifier profils / contenus')]:
            checks[effect] = ctk.BooleanVar(value=effect in selected)
            ctk.CTkCheckBox(panel, text=title, variable=checks[effect]).pack(anchor='w', pady=4)
        self._line(panel, 'Comptes : identifiants séparés par virgules ; * = comptes ouverts à cette activité', COLORS['muted'])
        keys = ctk.CTkEntry(panel)
        keys.insert(0, ','.join(json.loads(previous['resource_keys'])) if previous else '*')
        keys.pack(fill='x')
        def save():
            try:
                mandates.replace(business, previous['id'] if previous else None, label.get(), target.get(),
                                 [e for e,v in checks.items() if v.get()], actor='human',
                                 resource_keys=[k.strip() for k in keys.get().split(',') if k.strip()])
                window.destroy()
                self._load_snapshot()
            except Exception as exc:
                self._hub_error(exc)
        self._secondary(panel, 'Accorder jusqu’à révocation', save)
        window.transient(self)
        window.grab_set()
        window.lift()
        label.focus_set()

    def _open_account(self, key):
        if self._readonly:
            return
        from octopus import resources
        if not hasattr(self, '_human_connections'):
            self._human_connections = {}
        def work():
            previous = self._human_connections.pop(key, None)
            if previous:
                previous.close()
            self._human_connections[key] = resources.HumanConnection(key, actor='human')
            return 'Chrome stable ouvert, contrôlé par vous seul. Terminez puis fermez ses fenêtres avant « J’ai terminé — vérifier ».'
        self._hub_background(work)

    def _verify_account(self, key):
        if self._readonly:
            return
        def work():
            from octopus import resources
            connection = getattr(self, '_human_connections', {}).get(key)
            ok = connection.verify() if connection else resources.verify_account_connection(key, actor='human')
            if connection:
                self._human_connections.pop(key, None)
                connection.close()
            return 'Compte connecté. Accordez un mandat puis reprenez l’activité.' if ok else 'Session non réutilisable ou vérification impossible. Voir l’état du compte ; reconnectez ou utilisez un handoff humain.'
        self._hub_background(work)

    def _hub_background(self, work):
        def run():
            try:
                message = work()
                self._background_results.put(('v2_hub_done', message))
            except Exception as exc:
                self._background_results.put(('v2_hub_error', str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def _revoke_mandate(self, mandate):
        if self._readonly:
            return
        from octopus import mandates
        mandates.revoke(mandate['business'], mandate['id'], actor='human')
        self._load_snapshot()

    def _disable_account(self, key):
        if self._readonly:
            return
        from octopus import resources
        resources.disable_account(key, actor='human')
        connection = getattr(self, '_human_connections', {}).pop(key, None)
        if connection:
            self._hub_background(lambda: (connection.close(), 'Compte désactivé.')[1])
        self._load_snapshot()

    def _decline_resource(self, request_id):
        if self._readonly:
            return
        from octopus import tasks
        tasks.answer(request_id, 'declined')
        self._load_snapshot()

    def destroy(self):
        for connection in list(getattr(self, '_human_connections', {}).values()):
            try:
                connection.close()
            except Exception:
                pass
        super().destroy()

    def _worker_label(self) -> str:
        if any(p.poll() is None and self.selected_business_id in (DEFAULT_BUSINESS_ID, business)
               for business, p in self._pursuit_processes):
            return "Activité en cours"
        if self.worker_proc and self.worker_proc.poll() is None:
            return "Worker moteur actif (global)"
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
                self._background_results.put(("v2_error", f"{type(exc).__name__}: {exc}", business))

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
                    changed = (self._snapshot is None or self._snapshot_error is not None or
                               (self.current_page in PRIMARY and any(
                                   result[2].get(key) != self._snapshot.get(key)
                                   for key in PAGE_DATA_KEYS[self.current_page])))
                    self._snapshot = result[2]
                    self._visible_business_ids.update(self._snapshot["businesses"])
                    self.registry.reload()
                    for business_id in self._snapshot["businesses"]:
                        self.registry._businesses.setdefault(business_id, Business(business_id, business_id.title()))
                    self._sync_business_menu()
                    self._snapshot_error = None
                    self._snapshot_at = time.monotonic()
                    if changed and self.current_page in PRIMARY:
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
            elif kind == 'v2_hub_done':
                self._set_status(result[1], COLORS['good'])
                if self._snapshot_busy:
                    self._reload_after_create = True
                else:
                    self._load_snapshot()
            elif kind == 'v2_hub_error':
                self._hub_error(result[1])
            elif kind == "v2_action_error":
                self._creating = False
                self._set_status(result[1][:110], COLORS["bad"])
            elif kind == "v2_error":
                self._snapshot_busy = False
                if len(result) > 2 and result[2] != self.selected_business_id:
                    continue
                self._snapshot_error = result[1]
                self._snapshot_at = time.monotonic()
                self._set_status(result[1][:110], COLORS["bad"])
                if self._snapshot is None and self.current_page in PRIMARY:
                    self._show_page(self.current_page)
            else:
                self._background_results.put(result)
                super()._drain_background_results()
                break
        self.side_worker.configure(text="Exécution : " + self._worker_label())
        if (self.current_page == "Vue d'ensemble" and hasattr(self, "overview_worker") and
                self.overview_worker.winfo_exists()):
            self.overview_worker.configure(text="Exécution : " + self._worker_label() +
                                           ". Aucun appel Agnes automatique.")
        if not self._snapshot_busy and time.monotonic() - self._snapshot_at > 5:
            self._load_snapshot()
        self.after(1500, self._refresh)


def main() -> None:
    WorkbenchV2().mainloop()
