"""OCTOPUS Workbench V2: mission-first desktop interface."""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from pathlib import Path

import customtkinter as ctk

from . import agnes_missions
from .intelligence import EntrepreneurialWorkbench
from .workbench import COLORS, DEFAULT_BUSINESS_ID, _open_path
from .workbench_v2_data import mission_state, read_snapshot
from .workspaces import Business

PRIMARY = ("Vue d'ensemble", "Missions", "Livrables", "Activité", "Paramètres")
ADVANCED = ("Business", "Agents", "Intelligence", "Navigateur", "Production", "Humain", "Système")
SYSTEM_BUSINESS = "octopus"
META = {
    "Vue d'ensemble": ("Vue d'ensemble", "OCTOPUS suit les objectifs autorisés jusqu'aux livrables vérifiés."),
    "Missions": ("Missions", "Créez un objectif et suivez sa preuve jusqu'à la clôture."),
    "Livrables": ("Livrables", "Vidéos présentes, décodables et reliées à une preuve active."),
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
        ctk.CTkLabel(self.sidebar, text="Atelier supervisé", anchor="w", text_color=COLORS["muted"],
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

    def _eligible_businesses(self) -> list[Business]:
        channels = (self._snapshot or {}).get("channels", [])
        allowed = {channel["business"] for channel in channels
                   if channel["business"] != SYSTEM_BUSINESS and channel["kind"] == "agnes_video"
                   and channel["status"] == "active" and channel["access"] == "act"
                   and channel["locator"] and "agnes_submit" in json.loads(channel["capabilities"])}
        return [item for item in self.registry.all() if item.id in allowed]

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
         "Paramètres": self._settings}[page](body)
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
        objectives = [item for item in state["objectives"] if item["business"] != SYSTEM_BUSINESS]
        verified = [g for g in state["generations"] if g["verified"] and
                    g["business"] != SYSTEM_BUSINESS and g["objective_id"]]
        technical = [g for g in state["generations"] if g["verified"] and g not in verified]
        pending = [r for r in state["requests"] if r["status"] == "pending"]
        active = [o for o in objectives if o["status"] == "active"]
        hero = self._card(body)
        hero.pack(fill="x", pady=(0, 12))
        if pending:
            headline = f"{len(pending)} réponse(s) attendue(s)"
            detail = pending[0]["question"]
        elif active:
            headline = f"{len(active)} objectif(s) en cours"
            detail = mission_state(active[0])[1]
        else:
            headline = "Aucune mission économique en cours"
            detail = ("Choisissez une activité réelle et autorisez son canal Agnes avant de créer une mission."
                      if not self._eligible_businesses() else
                      "Créez un objectif vidéo autorisé pour lancer un travail supervisé.")
        self._line(hero, headline, size=22, bold=True, padx=20, pady=(19, 3))
        self._line(hero, detail, COLORS["muted"], padx=20, pady=(0, 10))
        ctk.CTkButton(hero, text="Nouvelle mission", command=lambda: self._show_page("Missions"),
                      height=38, width=170).pack(anchor="w", padx=20, pady=(0, 18))

        stats = ctk.CTkFrame(body, fg_color="transparent")
        stats.pack(fill="x")
        for col in range(3):
            stats.grid_columnconfigure(col, weight=1)
        for col, (title, value, detail) in enumerate((
            ("Objectifs actifs", str(len(active)), "Clôture après preuve"),
            ("Livrables MP4 vérifiés", str(len(verified)), "Fichier, décodage et SHA"),
            ("Worker", self._worker_label(), "Agnes : " + state["agnes_health"]))):
            card = self._card(stats)
            card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 5, 0))
            self._line(card, title, COLORS["muted"], padx=16, pady=(13, 1))
            self._line(card, value, size=19, bold=True, padx=16)
            self._line(card, detail, COLORS["muted"], padx=16, pady=(0, 14))

        self._section(body, "Coûts et résultats")
        economics = self._card(body)
        economics.pack(fill="x")
        ledger = [r for r in state["ledger"] if r["nature"] == "observed" and r["direction"] == "out"]
        amounts = {}
        for row in ledger:
            amounts[row["currency"]] = amounts.get(row["currency"], 0) + row["amount"]
        observed = ", ".join(f"{amount:g} {currency}" for currency, amount in amounts.items()) or "Aucune"
        for index, (label, value, color) in enumerate((
            ("Dépenses observées", observed, COLORS["text"]),
            ("Coût tokens calculé", f"{state['token_cost_usd']:.6f} USD", COLORS["text"]),
            ("Coût API Agnes", "Inconnu" if state["agnes_api_cost"] is None else
             f"{state['agnes_api_cost']} USD", COLORS["warn"]))):
            economics.grid_columnconfigure(index, weight=1)
            cell = ctk.CTkFrame(economics, fg_color="transparent")
            cell.grid(row=0, column=index, sticky="ew", padx=16, pady=(10, 12))
            self._line(cell, label, COLORS["muted"])
            self._line(cell, value, color, size=14, bold=True)
        if verified:
            latest = verified[0]
            self._section(body, "Dernier livrable")
            card = self._card(body)
            card.pack(fill="x")
            self._line(card, f"Vidéo Agnes #{latest['id']} - {latest['business']}",
                       size=14, bold=True, padx=18, pady=(14, 2))
            self._line(card, f"{_date(latest['created_at'])} - MP4 vérifié", COLORS["muted"], padx=18)
            self._secondary(card, "Ouvrir le MP4", lambda p=latest["output_path"]: _open_path(p))
        else:
            self._line(body, "Aucun livrable économique vérifié pour ce contexte.", COLORS["muted"], pady=(14, 0))
        if technical:
            self._line(body, f"{len(technical)} validation(s) technique(s) MP4 dans Livrables.",
                       COLORS["muted"], pady=(10, 0))

    def _missions(self, body) -> None:
        compose = self._card(body, "Nouvelle mission")
        compose.pack(fill="x", pady=(0, 8))
        businesses = self._eligible_businesses()
        values = [b.label() for b in businesses]
        self._line(compose, "Business", COLORS["muted"], padx=18)
        self.mission_business = ctk.CTkOptionMenu(compose, values=values or ["Aucune activité autorisée"],
                                                  dynamic_resizing=False,
                                                  command=lambda _value: self._update_create_state())
        self.mission_business.pack(fill="x", padx=18, pady=(2, 9))
        if not businesses:
            self._line(compose, "Le canal OCTOPUS sert aux tests du système. Autorisez un canal pour une activité réelle.",
                       COLORS["warn"], padx=18, pady=(0, 8))
        selected = self.registry.get(self.selected_business_id)
        if selected and selected in businesses:
            self.mission_business.set(selected.label())
        self._line(compose, "Type : Vidéo avec Agnes", padx=18, pady=(0, 5))
        self.mission_prompt = ctk.CTkTextbox(compose, height=95, wrap="word", fg_color=COLORS["surface2"],
                                             border_color=COLORS["border"], border_width=1)
        self.mission_prompt.pack(fill="x", padx=18, pady=(0, 8))
        self.mission_consent = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(compose, text="J'autorise cette génération. Le coût API Agnes peut être facturé et reste inconnu.",
                        variable=self.mission_consent).pack(anchor="w", padx=18, pady=(0, 10))
        self.mission_button = ctk.CTkButton(compose, text="Créer la mission", command=self._create_mission, height=38)
        self.mission_button.pack(anchor="w", padx=18, pady=(0, 4))
        self.mission_info = self._line(compose, "", COLORS["muted"], padx=18, pady=(0, 12))
        self._update_create_state()

        self._section(body, "Suivi des objectifs")
        objectives = [item for item in self._snapshot["objectives"] if item["business"] != SYSTEM_BUSINESS]
        if not objectives:
            self._line(body, "Aucun objectif dans ce contexte. Créez une mission ci-dessus.", COLORS["muted"])
        for objective in objectives:
            stage, detail = mission_state(objective)
            card = self._card(body)
            card.pack(fill="x", pady=(0, 8))
            self._line(card, f"#{objective['id']}  {objective['summary']}", size=14, bold=True,
                       padx=18, pady=(13, 1))
            self._line(card, f"{objective['business']}  |  {stage}  |  {_date(objective['created_at'])}",
                       COLORS["warn"] if "attend" in stage.lower() or stage == "À examiner" else COLORS["muted"],
                       padx=18)
            self._line(card, detail, COLORS["muted"], padx=18, pady=(4, 6))
            if objective["authorized"] or objective["success_criteria"] == "kept_video_files>=1":
                steps = (("Demande", True), ("Autorisation", objective["authorized"]),
                         ("Planification", bool(objective["work_tasks"])),
                         ("Worker", any(t["status"] in ("running", "done") for t in objective["work_tasks"])),
                         ("MP4", any(g["verified"] for g in objective["generations"])),
                         ("Objectif", objective["status"] == "achieved"))
                done = [label.lower() for label, complete in steps if complete]
                next_step = next((label.lower() for label, complete in steps if not complete), None)
                self._line(card, "Étapes faites : " + ", ".join(done), COLORS["good"], padx=18, pady=(2, 0))
                if next_step:
                    self._line(card, "Étape suivante : " + next_step, COLORS["muted"], padx=18)
            if objective["work_tasks"]:
                task = objective["work_tasks"][-1]
                self._line(card, f"Tâche #{task['id']} : {task['status']} - {task['kind']}",
                           COLORS["muted"], padx=18)
            if objective["generations"]:
                generation = objective["generations"][0]
                self._line(card, f"Vidéo #{generation['id']} : " +
                           ("MP4 vérifié" if generation["verified"] else generation["status"]),
                           COLORS["good"] if generation["verified"] else COLORS["muted"],
                           padx=18, pady=(2, 0))
            self._secondary(card, "Détails de la mission",
                            lambda item=objective, parent=card: self._toggle_mission_details(item, parent))

    def _toggle_mission_details(self, objective: dict, card) -> None:
        existing = getattr(card, "_details", None)
        if existing and existing.winfo_exists():
            existing.destroy()
            self.after(80, lambda: self._fit_scrollbar(self._body))
            return
        details = ctk.CTkFrame(card, fg_color=COLORS["surface2"], corner_radius=8)
        details.pack(fill="x", padx=18, pady=(0, 15))
        card._details = details
        self._line(details, "Tâches liées", size=12, bold=True, padx=12, pady=(11, 2))
        if not objective["work_tasks"]:
            self._line(details, "Aucune tâche planifiée.", COLORS["muted"], padx=12)
        for task in objective["work_tasks"]:
            self._line(details, f"#{task['id']}  {task['kind']}  |  {task['status']}", padx=12)
            if task.get("error"):
                self._line(details, task["error"], COLORS["warn"], padx=12)
        self._line(details, "Preuves vidéo", size=12, bold=True, padx=12, pady=(10, 2))
        if not objective["generations"]:
            self._line(details, "Aucune génération reliée à cet objectif.", COLORS["muted"], padx=12)
        for generation in objective["generations"]:
            self._line(details, f"Vidéo #{generation['id']}  |  " +
                       ("vérifiée" if generation["verified"] else "non vérifiée") +
                       f"  |  preuve #{generation['evidence_id'] or 'absente'}", padx=12)
            if generation.get("sha256"):
                self._line(details, "SHA-256 : " + generation["sha256"], COLORS["muted"], padx=12)
        self._secondary(details, "Voir l'activité", lambda: self._show_page("Activité"))
        self.after(80, lambda: self._fit_scrollbar(self._body))

    def _update_create_state(self) -> None:
        if self._readonly:
            self.mission_button.configure(state="disabled")
            self.mission_info.configure(text="Mode consultation : création désactivée.", text_color=COLORS["warn"])
            return
        selected = next((b for b in self._eligible_businesses() if b.label() == self.mission_business.get()), None)
        self.mission_button.configure(state="normal" if selected else "disabled")
        self.mission_info.configure(text="Canal Agnes autorisé." if selected else
                                    "Canal Agnes actif avec accès act requis pour ce business.",
                                    text_color=COLORS["muted"] if selected else COLORS["warn"])

    def _create_mission(self) -> None:
        if self._readonly or self._creating:
            return
        selected = next((b for b in self._eligible_businesses() if b.label() == self.mission_business.get()), None)
        prompt = self.mission_prompt.get("1.0", "end").strip()
        if not selected:
            self._set_status("Choisissez un business concret.", COLORS["bad"])
            return
        if not prompt:
            self._set_status("Décrivez la vidéo à produire.", COLORS["bad"])
            return
        if not self.mission_consent.get():
            self._set_status("Autorisez explicitement le coût potentiel avant de créer.", COLORS["bad"])
            return
        self._creating = True
        self._set_status("Création de la mission...", COLORS["muted"])

        def run():
            try:
                objective_id = agnes_missions.create(selected.id, prompt, authorized=True)
                self._background_results.put(("v2_created", objective_id))
            except Exception as exc:
                self._background_results.put(("v2_action_error", str(exc)))

        threading.Thread(target=run, daemon=True).start()

    def _deliverables(self, body) -> None:
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
        pending = [r for r in self._snapshot["requests"] if r["status"] == "pending"]
        if pending:
            card = self._card(body, "Votre intervention")
            card.pack(fill="x", pady=(0, 12))
            for request in pending:
                self._line(card, request["question"], padx=18, pady=(0, 8))
            self._secondary(card, "Ouvrir les réponses", lambda: self._show_page("Humain"))
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
        self._line(card, f"Worker : {self._worker_label()}", padx=18, pady=(13, 1))
        self._line(card, "Agnes : " + state["agnes_health"] + ". La création exige un canal actif avec accès act.",
                   COLORS["muted"], padx=18, pady=(0, 8))
        owned_worker = self.worker_proc is not None and self.worker_proc.poll() is None
        external_worker = self._worker_label() == "Activité détectée" and not owned_worker
        button = self._secondary(card, "Arrêter le Worker" if owned_worker else "Démarrer le Worker", self._toggle_worker)
        if self._readonly or external_worker:
            button.configure(state="disabled")
        if external_worker:
            self._line(card, "Un Worker extérieur à cette fenêtre est actif. Gérez-le depuis sa session.",
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

        def run():
            try:
                snapshot = read_snapshot(business, check_health=True)
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
                                   (self.mission_prompt.get("1.0", "end").strip() or self.mission_consent.get()))
                        if not editing:
                            self._show_page(self.current_page)
                if self._reload_after_create:
                    self._reload_after_create = False
                    self._load_snapshot()
            elif kind == "v2_created":
                self._creating = False
                self._set_status(f"Mission #{result[1]} créée. Worker à démarrer si nécessaire.", COLORS["good"])
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
        self.side_worker.configure(text="Worker : " + self._worker_label())
        if not self._snapshot_busy and time.monotonic() - self._snapshot_at > 30:
            self._load_snapshot()
        self.after(1500, self._refresh)


def main() -> None:
    WorkbenchV2().mainloop()
