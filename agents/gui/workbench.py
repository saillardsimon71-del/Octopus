"""OCTOPUS Workbench : poste de travail quotidien du control-plane.

Cette interface reste une couche d'orchestration/visualisation. Les moteurs métier restent dans
les modules existants et les opérations longues passent par des sous-processus/threads.
"""
from __future__ import annotations

import os
import platform
import queue
import subprocess
import threading
import time
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from .. import config, db, procs
from .workspaces import DEFAULT_BUSINESS_ID, Business, WorkspaceRegistry

COLORS = {
    "bg": "#0b1220", "surface": "#111827", "surface2": "#172033", "surface3": "#1f2a40",
    "border": "#26344d", "text": "#e6edf7", "muted": "#8ea0ba", "accent": "#5b8def",
    "accent_hover": "#4c78d6", "good": "#3ecf8e", "warn": "#f0b35b", "bad": "#ef6b73",
    "info": "#58b8ff", "purple": "#a98cf4",
}
AGENT_COLORS = {
    "ORBIT": "#f4b860", "GROWTH": "#63d39b", "LEDGER": "#64b5f6",
    "FORGE": "#ff8b6a", "CONVERT": "#b987f5", "SOUT": "#55c3bd",
}
AGENT_DESCRIPTIONS = {
    "ORBIT": "coordination, arbitrage, synthèse", "GROWTH": "QC visuel, distribution",
    "LEDGER": "coûts, métriques, go/no-go", "FORGE": "production vidéo",
    "CONVERT": "offres, scripts, CTA", "SOUT": "veille, sources, opportunités",
}
PAGE_META = {
    "Cockpit": ("Centre de contrôle", "Piloter le business actif sans quitter le cockpit"),
    "Business": ("Businesses", "Changer de contexte et piloter plusieurs activités"),
    "Missions": ("Missions", "Donner un objectif à ORBIT et suivre l'exécution"),
    "Agents": ("Agents", "État, activité et charge du collectif"),
    "Production": ("Production vidéo", "Offres, rendu cloud, Studio et QC"),
    "Humain": ("Interventions humaines", "Questions, validations et handoffs"),
    "Navigateur": ("Navigateur", "Session Chromium agentique et observations"),
    "Système": ("Système", "Prévol cloud-first, journaux et développement Orca"),
}


class PodaluxWorkbench(ctk.CTk):
    """Cockpit unique : contexte business + opérations + supervision."""

    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.title("OCTOPUS — Workbench")
        self.geometry("1500x940")
        self.minsize(1180, 760)
        self.configure(fg_color=COLORS["bg"])

        db.init_db()  # premier lancement : la table state doit exister avant de lire le business actif
        self.registry = WorkspaceRegistry()
        selected = db.get_state("active_business") or DEFAULT_BUSINESS_ID
        self.selected_business_id = selected if selected == DEFAULT_BUSINESS_ID or self.registry.get(selected) else DEFAULT_BUSINESS_ID
        self.proc: subprocess.Popen | None = None
        self.worker_proc: subprocess.Popen | None = None
        self.msg_procs: list[subprocess.Popen] = []
        self.current_page = "Cockpit"
        self._last_browser_shot: str | None = None
        self._browser_image = None
        self._system_busy = False
        self._orca_busy = False
        self._background_results: queue.SimpleQueue = queue.SimpleQueue()
        self._last_activity_signature: tuple | None = None
        self._build_shell()
        self._sync_business_menu()
        self._show_page("Cockpit")
        self._bind_shortcuts()
        self.after(700, self._refresh)

    # shell -------------------------------------------------------------------------
    def _build_shell(self) -> None:
        self.grid_columnconfigure(0, weight=0, minsize=246)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self, width=246, corner_radius=0, fg_color=COLORS["surface"])
        sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar = sidebar

        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=18, pady=(18, 10))
        ctk.CTkLabel(brand, text="OCTOPUS", text_color=COLORS["text"], font=("Segoe UI", 24, "bold")).pack(anchor="w")
        ctk.CTkLabel(brand, text="BUSINESS OPERATING DESK", text_color=COLORS["muted"], font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(2, 0))

        context = ctk.CTkFrame(sidebar, fg_color=COLORS["surface2"], corner_radius=12)
        context.pack(fill="x", padx=12, pady=(4, 14))
        ctk.CTkLabel(context, text="BUSINESS ACTIF", text_color=COLORS["muted"], font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(9, 2))
        self.business_menu = ctk.CTkOptionMenu(context, values=["Tous les business"], command=self._on_business_menu, dynamic_resizing=False)
        self.business_menu.pack(fill="x", padx=9, pady=(0, 8))
        ctk.CTkButton(context, text="＋ Gérer les businesses", height=30, fg_color=COLORS["surface3"], hover_color=COLORS["surface3"], command=lambda: self._show_page("Business")).pack(fill="x", padx=9, pady=(0, 9))

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        for page in PAGE_META:
            button = ctk.CTkButton(sidebar, text=self._nav_label(page), anchor="w", height=39, corner_radius=9,
                                   fg_color="transparent", hover_color=COLORS["surface3"], text_color=COLORS["muted"],
                                   command=lambda p=page: self._show_page(p))
            button.pack(fill="x", padx=12, pady=2)
            self.nav_buttons[page] = button

        side_footer = ctk.CTkFrame(sidebar, fg_color="transparent")
        side_footer.pack(side="bottom", fill="x", padx=14, pady=14)
        self.side_worker = ctk.CTkLabel(side_footer, text="● Worker arrêté", text_color=COLORS["muted"], anchor="w", font=("Segoe UI", 9, "bold"))
        self.side_worker.pack(fill="x", padx=5)
        ctk.CTkLabel(side_footer, text="cloud-first · vidéo distante · publication contrôlée", text_color=COLORS["muted"], justify="left", wraplength=205, font=("Segoe UI", 8)).pack(fill="x", padx=5, pady=(4, 0))

        main = ctk.CTkFrame(self, fg_color=COLORS["bg"], corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=26, pady=(18, 10))
        header.grid_columnconfigure(1, weight=1)
        self.page_title = ctk.CTkLabel(header, text="", anchor="w", text_color=COLORS["text"], font=("Segoe UI", 23, "bold"))
        self.page_title.grid(row=0, column=0, sticky="w")
        self.page_subtitle = ctk.CTkLabel(header, text="", anchor="w", text_color=COLORS["muted"], font=("Segoe UI", 10))
        self.page_subtitle.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.context_chip = ctk.CTkLabel(header, text="Tous les business", text_color=COLORS["info"], fg_color=COLORS["surface2"], corner_radius=8, font=("Segoe UI", 9, "bold"))
        self.context_chip.grid(row=0, column=1, sticky="e", padx=(8, 18))
        self.header_status = ctk.CTkLabel(header, text="● Prêt", text_color=COLORS["muted"], font=("Segoe UI", 10, "bold"))
        self.header_status.grid(row=0, column=2, rowspan=2, sticky="e")

        self.page_host = ctk.CTkFrame(main, fg_color="transparent")
        self.page_host.grid(row=1, column=0, sticky="nsew", padx=26, pady=(0, 8))
        self.page_host.grid_columnconfigure(0, weight=1)
        self.page_host.grid_rowconfigure(0, weight=1)

        self.command_bar = ctk.CTkFrame(main, fg_color=COLORS["surface"], border_width=1, border_color=COLORS["border"], corner_radius=0)
        self.command_bar.grid(row=2, column=0, sticky="ew")
        self.command_bar.grid_columnconfigure(0, weight=1)
        self.command_entry = ctk.CTkEntry(self.command_bar, height=38, border_width=0, fg_color="transparent", placeholder_text="Commande rapide : mission, @agent, /production, /business, /doctor…")
        self.command_entry.grid(row=0, column=0, sticky="ew", padx=(16, 8), pady=8)
        self.command_entry.bind("<Return>", lambda _e: self._handle_command(self.command_entry.get()))
        ctk.CTkButton(self.command_bar, text="Exécuter", width=105, height=34, command=lambda: self._handle_command(self.command_entry.get())).grid(row=0, column=1, padx=(4, 14), pady=10)

    @staticmethod
    def _nav_label(page: str) -> str:
        return {
            "Cockpit": "⌂  Cockpit", "Business": "▦  Business", "Missions": "◇  Missions", "Agents": "◉  Agents",
            "Production": "▣  Production", "Humain": "?  Humain", "Navigateur": "◌  Navigateur", "Système": "⚙  Système",
        }.get(page, page)

    def _bind_shortcuts(self) -> None:
        pages = list(PAGE_META)
        for idx, page in enumerate(pages, start=1):
            self.bind_all(f"<Control-Key-{idx}>", lambda _e, p=page: self._show_page(p))
        self.bind_all("<Control-Return>", lambda _e: self._handle_command(self.command_entry.get()))

    def _show_page(self, page: str) -> None:
        self.current_page = page
        for name, button in self.nav_buttons.items():
            button.configure(fg_color=COLORS["surface3"] if name == page else "transparent", text_color=COLORS["text"] if name == page else COLORS["muted"])
        title, subtitle = PAGE_META[page]
        self.page_title.configure(text=title)
        self.page_subtitle.configure(text=subtitle)
        context = self._business_label()
        self.context_chip.configure(text=context)
        for child in self.page_host.winfo_children(): child.destroy()
        getattr(self, f"_page_{self._slug(page)}")()

    @staticmethod
    def _slug(page: str) -> str:
        return page.lower().replace("é", "e").replace("è", "e").replace(" ", "_")

    def _business_label(self) -> str:
        business = self.registry.get(self.selected_business_id)
        return business.label() if business else "Tous les business"

    def _sync_business_menu(self) -> None:
        values = ["Tous les business"] + [b.label() for b in self.registry.all()]
        self.business_menu.configure(values=values)
        self.business_menu.set(self._business_label())
        self.context_chip.configure(text=self._business_label())

    def _on_business_menu(self, label: str) -> None:
        if label == "Tous les business":
            self.selected_business_id = DEFAULT_BUSINESS_ID
        else:
            found = next((b for b in self.registry.all() if b.label() == label), None)
            if not found:
                return
            self.selected_business_id = found.id
        self.registry.set_current(self.selected_business_id)
        self._sync_business_menu()
        self._show_page(self.current_page)
        self._set_status(f"Contexte : {self._business_label()}", COLORS["info"])

    def _card(self, parent, title: str | None = None):
        card = ctk.CTkFrame(parent, fg_color=COLORS["surface"], border_width=1, border_color=COLORS["border"], corner_radius=14)
        if title:
            ctk.CTkLabel(card, text=title.upper(), text_color=COLORS["muted"], anchor="w", font=("Segoe UI", 9, "bold")).pack(fill="x", padx=14, pady=(12, 6))
        return card

    def _metric(self, parent, column: int, title: str, value: str, detail: str, color: str = COLORS["text"]) -> None:
        card = self._card(parent)
        card.grid(row=0, column=column, sticky="nsew", padx=4)
        ctk.CTkLabel(card, text=title.upper(), text_color=COLORS["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=13, pady=(11, 1))
        ctk.CTkLabel(card, text=value, text_color=color, font=("Segoe UI", 21, "bold")).pack(anchor="w", padx=13)
        ctk.CTkLabel(card, text=detail, text_color=COLORS["muted"], font=("Segoe UI", 9), anchor="w", wraplength=250).pack(anchor="w", padx=13, pady=(0, 11))

    def _pill(self, parent, text: str, color: str):
        return ctk.CTkLabel(parent, text=f"  {text}  ", text_color=color, fg_color=COLORS["surface3"], corner_radius=8, font=("Segoe UI", 8, "bold"))

    # data ---------------------------------------------------------------------------
    def _business_matches_offer(self, offer_id: str | None) -> bool:
        if self.selected_business_id == DEFAULT_BUSINESS_ID:
            return True
        return (offer_id or "") in set(self.registry.offers_for(self.selected_business_id))

    def _task_rows(self) -> list[dict]:
        try:
            from octopus import tasks
            return tasks.list_tasks(limit=80)
        except Exception:
            return []

    def _pending_requests(self) -> list[dict]:
        items = [{"source": "handoff", "id": h["id"], "who": h["agent"], "question": h["question"]} for h in db.pending_handoffs()]
        try:
            from octopus import tasks
            items += [{"source": "task", "id": r["id"], "task_id": r["task_id"], "who": f"tâche #{r['task_id']}", "question": r["question"]} for r in tasks.pending_human_requests()]
        except Exception:
            pass
        return items

    def _agent_rows(self) -> list[dict]:
        messages = db.recent_messages(300)
        tasks = self._task_rows()
        rows = []
        for role in config.AGENTS:
            role_msgs = [m for m in messages if m["from_agent"] == role]
            role_tasks = [t for t in tasks if role in str(t.get("business", "")).upper() or role in str(t.get("kind", "")).upper()]
            running = any(t.get("status") == "running" for t in role_tasks)
            waiting = any(t.get("status") == "waiting_human" for t in role_tasks)
            status = "en cours" if running else ("attente humain" if waiting else "au repos")
            rows.append({"role": role, "latest": role_msgs[-1]["content"][:180] if role_msgs else "Aucune activité récente", "status": status, "status_color": COLORS["info"] if running else (COLORS["warn"] if waiting else COLORS["muted"]), "tasks": len(role_tasks), "messages": len(role_msgs)})
        return rows

    # pages --------------------------------------------------------------------------
    def _page_cockpit(self) -> None:
        root = self.page_host
        root.grid_columnconfigure(0, weight=2)
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(2, weight=1)
        metrics = ctk.CTkFrame(root, fg_color="transparent")
        metrics.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        for i in range(4): metrics.grid_columnconfigure(i, weight=1)
        run = db.current_run() or {}
        tasks = self._task_rows()
        pending = self._pending_requests()
        business_offers = self.registry.offers_for(self.selected_business_id)
        active_tasks = sum(1 for t in tasks if t.get("status") in {"queued", "running", "waiting_human"})
        self._metric(metrics, 0, "Run", self._run_status(run), run.get("step") or "aucun run actif", self._run_color(run))
        self._metric(metrics, 1, "Business", self._business_label(), f"{len(business_offers)} offre(s) dans ce contexte", COLORS["info"])
        self._metric(metrics, 2, "Tâches actives", str(active_tasks), f"{len(tasks)} dernières dans la file")
        self._metric(metrics, 3, "À traiter", str(len(pending)), "interventions humaines", COLORS["warn"] if pending else COLORS["good"])

        quick = self._card(root, "Actions rapides")
        quick.grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=5)
        # Le titre de _card est placé avec pack : les boutons en grille vont dans un cadre dédié.
        quick_row = ctk.CTkFrame(quick, fg_color="transparent")
        quick_row.pack(fill="x")
        for i in range(5): quick_row.grid_columnconfigure(i, weight=1)
        buttons = [
            ("▶  Cycle", self._start_cycle, COLORS["accent"]),
            ("◇  Mission", lambda: self._show_page("Missions"), COLORS["surface3"]),
            ("▣  Production", lambda: self._show_page("Production"), COLORS["surface3"]),
            ("◉  Agents", lambda: self._show_page("Agents"), COLORS["surface3"]),
            ("?  Humain", lambda: self._show_page("Humain"), COLORS["surface3"]),
        ]
        for i, (text, command, fg) in enumerate(buttons):
            ctk.CTkButton(quick_row, text=text, height=37, command=command, fg_color=fg, hover_color=COLORS["accent_hover"] if fg == COLORS["accent"] else COLORS["surface3"]).grid(row=1, column=i, sticky="ew", padx=5, pady=(0, 12))

        activity = self._card(root, "Flux d'activité")
        activity.grid(row=2, column=0, sticky="nsew", padx=(0, 6), pady=5)
        self.activity_feed = ctk.CTkScrollableFrame(activity, fg_color="transparent")
        self.activity_feed.pack(fill="both", expand=True, padx=7, pady=(0, 7))
        self._render_activity(self.activity_feed)

        side = ctk.CTkFrame(root, fg_color="transparent")
        side.grid(row=2, column=1, sticky="nsew", padx=(6, 0), pady=5)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(0, weight=1)
        agents = self._card(side, "Agents")
        agents.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        self.agent_overview = ctk.CTkScrollableFrame(agents, fg_color="transparent")
        self.agent_overview.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self._render_agents(self.agent_overview, compact=True)
        attention = self._card(side, "Attention")
        attention.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ctk.CTkLabel(attention, text=self._attention_text(), text_color=COLORS["text"], justify="left", anchor="w", wraplength=420).pack(fill="x", padx=13, pady=(0, 12))

    def _page_business(self) -> None:
        root = self.page_host
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(1, weight=1)
        head = self._card(root, "Portefeuille")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 9))
        row = ctk.CTkFrame(head, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkLabel(row, text="Chaque business est un contexte de travail local : offres, production, missions et indicateurs sont filtrés ensemble.", text_color=COLORS["muted"], anchor="w", wraplength=900).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="＋ Nouveau business", width=150, command=self._new_business_dialog).pack(side="right", padx=(10, 0))

        cards = ctk.CTkScrollableFrame(root, fg_color="transparent")
        cards.grid(row=1, column=0, sticky="nsew")
        cards.grid_columnconfigure((0, 1), weight=1)
        all_count = len(self.registry.offers_for(DEFAULT_BUSINESS_ID))
        global_card = self._card(cards)
        global_card.grid(row=0, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        ctk.CTkLabel(global_card, text="Tous les business", text_color=COLORS["info"], font=("Segoe UI", 16, "bold")).pack(side="left", padx=14, pady=12)
        ctk.CTkLabel(global_card, text=f"{all_count} offre(s) visibles", text_color=COLORS["muted"]).pack(side="left", padx=6)
        ctk.CTkButton(global_card, text="Sélectionner", width=95, command=lambda: self._select_business(DEFAULT_BUSINESS_ID)).pack(side="right", padx=14)

        for i, business in enumerate(self.registry.all(), start=1):
            offers = self.registry.offers_for(business.id)
            card = self._card(cards)
            card.grid(row=(i - 1) // 2 + 1, column=(i - 1) % 2, sticky="ew", padx=5, pady=5)
            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=14, pady=(12, 4))
            ctk.CTkLabel(top, text=business.label(), text_color=COLORS["text"], font=("Segoe UI", 16, "bold")).pack(side="left")
            self._pill(top, f"{len(offers)} offres", COLORS["info"]).pack(side="right")
            ctk.CTkLabel(card, text=business.description or "Aucune description.", text_color=COLORS["muted"], anchor="w", justify="left", wraplength=600).pack(fill="x", padx=14, pady=3)
            offers_text = " · ".join(offers[:6]) + (" …" if len(offers) > 6 else "") or "aucune offre"
            ctk.CTkLabel(card, text=offers_text, text_color=COLORS["text"], anchor="w", wraplength=600).pack(fill="x", padx=14, pady=(3, 8))
            actions = ctk.CTkFrame(card, fg_color="transparent")
            actions.pack(fill="x", padx=10, pady=(0, 10))
            ctk.CTkButton(actions, text="Ouvrir", width=82, command=lambda b=business.id: self._select_business(b)).pack(side="left", padx=3)
            ctk.CTkButton(actions, text="Mission", width=82, fg_color=COLORS["surface3"], command=lambda b=business.id: self._select_business_then("Missions", b)).pack(side="left", padx=3)
            ctk.CTkButton(actions, text="Production", width=92, fg_color=COLORS["surface3"], command=lambda b=business.id: self._select_business_then("Production", b)).pack(side="left", padx=3)

    def _page_missions(self) -> None:
        root = self.page_host
        root.grid_columnconfigure(0, weight=2)
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(1, weight=1)
        compose = self._card(root, "Nouvelle mission")
        compose.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 9))
        compose_row = ctk.CTkFrame(compose, fg_color="transparent")
        compose_row.pack(fill="x")
        compose_row.grid_columnconfigure(0, weight=1)
        self.mission_entry = ctk.CTkEntry(compose_row, height=41, placeholder_text=f"Ex. : analyser le marché et préparer un plan — contexte : {self._business_label()}")
        self.mission_entry.grid(row=1, column=0, sticky="ew", padx=(14, 7), pady=(3, 13))
        self.mission_entry.bind("<Return>", lambda _e: self._start_mission())
        ctk.CTkButton(compose_row, text="Lancer", width=130, height=41, command=self._start_mission).grid(row=1, column=1, padx=(7, 14), pady=(3, 13))

        task_card = self._card(root, "File de travail")
        task_card.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        task_card.grid_rowconfigure(1, weight=1)
        filter_bar = ctk.CTkFrame(task_card, fg_color="transparent")
        filter_bar.pack(fill="x", padx=10, pady=(0, 4))
        self.mission_filter = ctk.StringVar(value="Toutes")
        ctk.CTkSegmentedButton(filter_bar, values=["Toutes", "Actives", "Humain", "Terminées"], variable=self.mission_filter, command=lambda _v: self._refresh()).pack(anchor="w")
        self.mission_tasks = ctk.CTkScrollableFrame(task_card, fg_color="transparent")
        self.mission_tasks.pack(fill="both", expand=True, padx=6, pady=(0, 8))
        self._render_task_list(self.mission_tasks)

        side = self._card(root, "Contexte")
        side.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        business = self.registry.get(self.selected_business_id)
        ctk.CTkLabel(side, text=self._business_label(), text_color=COLORS["info"], font=("Segoe UI", 17, "bold"), anchor="w").pack(fill="x", padx=14, pady=(13, 4))
        ctk.CTkLabel(side, text=(business.description if business else "Vue globale : missions multi-business."), text_color=COLORS["muted"], anchor="w", justify="left", wraplength=350).pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkLabel(side, text="Worker", text_color=COLORS["muted"], anchor="w", font=("Segoe UI", 9, "bold")).pack(fill="x", padx=14, pady=(5, 0))
        self.worker_state = ctk.CTkLabel(side, text="Actif" if self.worker_proc else "Arrêté", text_color=COLORS["good"] if self.worker_proc else COLORS["muted"], anchor="w")
        self.worker_state.pack(fill="x", padx=14, pady=4)
        ctk.CTkButton(side, text="Démarrer / arrêter", command=self._toggle_worker).pack(fill="x", padx=14, pady=6)
        ctk.CTkButton(side, text="Voir les handoffs", fg_color=COLORS["surface3"], command=lambda: self._show_page("Humain")).pack(fill="x", padx=14, pady=3)

    def _page_agents(self) -> None:
        root = self.page_host
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(0, weight=1)
        frame = ctk.CTkScrollableFrame(root, fg_color="transparent")
        frame.grid(row=0, column=0, sticky="nsew")
        frame.grid_columnconfigure((0, 1), weight=1)
        self._render_agents(frame, compact=False)

    def _page_production(self) -> None:
        root = self.page_host
        root.grid_columnconfigure(0, weight=1)
        root.grid_columnconfigure(1, weight=2)
        root.grid_rowconfigure(1, weight=1)
        controls = self._card(root, "Production")
        controls.grid(row=0, column=0, sticky="ew", padx=(0, 7), pady=(0, 8))
        controls.grid_columnconfigure(0, weight=1)
        offers = self.registry.offers_for(self.selected_business_id)
        values = offers or ["Aucune offre"]
        self.production_offer = ctk.CTkOptionMenu(controls, values=values, dynamic_resizing=False)
        self.production_offer.pack(fill="x", padx=14, pady=(3, 8))
        run = db.current_run() or {}
        if run.get("offer_id") in offers:
            self.production_offer.set(run["offer_id"])
        row = ctk.CTkFrame(controls, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(0, 8))
        for text, command in (("▶ Cycle", self._start_cycle), ("🎬 Studio", self._open_studio), ("▶ Ouvrir", self._open_video)):
            ctk.CTkButton(row, text=text, command=command, fg_color=COLORS["surface3"], hover_color=COLORS["surface3"]).pack(side="left", fill="x", expand=True, padx=3)
        ctk.CTkButton(controls, text="Publication dry-run", fg_color="#28623f", hover_color="#32774d", command=self._publish).pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkLabel(controls, text="Rendu normal : cloud / RunPod\nMiniMax H3 : cloud-only", text_color=COLORS["muted"], justify="left", anchor="w").pack(fill="x", padx=14, pady=(0, 12))

        summary = self._card(root, "Run & QC")
        summary.grid(row=1, column=0, sticky="nsew", padx=(0, 7))
        self.production_summary = summary
        self._render_production_summary()
        metrics = self._card(root, "Derniers résultats")
        metrics.grid(row=1, column=1, sticky="nsew", padx=(7, 0))
        self.production_metrics = ctk.CTkScrollableFrame(metrics, fg_color="transparent")
        self.production_metrics.pack(fill="both", expand=True, padx=6, pady=(0, 7))
        self._render_metrics(self.production_metrics)

    def _page_humain(self) -> None:
        root = self.page_host
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(0, weight=1)
        frame = ctk.CTkScrollableFrame(root, fg_color="transparent")
        frame.grid(row=0, column=0, sticky="nsew")
        self._render_handoffs(frame)

    def _page_navigateur(self) -> None:
        root = self.page_host
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(1, weight=1)
        controls = self._card(root, "Navigation contrôlée")
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 9))
        controls_row = ctk.CTkFrame(controls, fg_color="transparent")
        controls_row.pack(fill="x")
        controls_row.grid_columnconfigure(0, weight=1)
        self.browser_entry = ctk.CTkEntry(controls_row, height=38, placeholder_text="https://…")
        self.browser_entry.grid(row=0, column=0, sticky="ew", padx=(14, 6), pady=10)
        self.browser_entry.bind("<Return>", lambda _e: self._browse_url())
        ctk.CTkButton(controls_row, text="Ouvrir visible", width=120, command=self._open_browser).grid(row=0, column=1, padx=4, pady=10)
        ctk.CTkButton(controls_row, text="Inspecter", width=100, fg_color=COLORS["surface3"], command=self._browse_url).grid(row=0, column=2, padx=(4, 14), pady=10)
        view = self._card(root, "Observation")
        view.grid(row=1, column=0, sticky="nsew")
        self.browser_state = ctk.CTkLabel(view, text="URL : (inactive)", text_color=COLORS["muted"], anchor="w")
        self.browser_state.pack(fill="x", padx=14, pady=(10, 5))
        self.browser_preview = ctk.CTkLabel(view, text="Aucune capture récente.")
        self.browser_preview.pack(expand=True, padx=14, pady=10)

    def _page_systeme(self) -> None:
        root = self.page_host
        root.grid_columnconfigure(0, weight=1)
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(0, weight=1)
        checks = self._card(root, "Doctor cloud-first")
        checks.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self.system_checks = ctk.CTkScrollableFrame(checks, fg_color="transparent")
        self.system_checks.pack(fill="both", expand=True, padx=7, pady=(0, 7))
        ctk.CTkButton(checks, text="Relancer le diagnostic", command=self._run_doctor).pack(fill="x", padx=14, pady=(0, 12))
        self._run_doctor()
        dev = self._card(root, "Développement")
        dev.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        ctk.CTkLabel(dev, text="Orca est optionnel et réservé au développement du dépôt.", text_color=COLORS["muted"], anchor="w", justify="left", wraplength=500).pack(fill="x", padx=14, pady=(0, 9))
        ctk.CTkButton(dev, text="Orca status", fg_color=COLORS["surface3"], command=self._orca_status).pack(fill="x", padx=14, pady=4)
        self.orca_result = ctk.CTkLabel(dev, text="", text_color=COLORS["muted"], anchor="w", justify="left", wraplength=500)
        self.orca_result.pack(fill="x", padx=14, pady=(4, 10))
        ctk.CTkLabel(dev, text="Journaux", text_color=COLORS["muted"], font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=14, pady=(7, 2))
        log_dir = config.DATA_DIR / "logs"
        ctk.CTkLabel(dev, text=str(log_dir), text_color=COLORS["text"], anchor="w", wraplength=500).pack(fill="x", padx=14, pady=(0, 8))
        ctk.CTkButton(dev, text="Ouvrir le dossier de logs", fg_color=COLORS["surface3"], command=lambda: _open_path(log_dir)).pack(fill="x", padx=14, pady=4)

    # render helpers -----------------------------------------------------------------
    def _render_activity(self, parent) -> None:
        for widget in list(parent.winfo_children()): widget.destroy()
        messages = db.recent_messages(100)[-35:][::-1]
        for message in messages:
            role = message["from_agent"]
            if role not in {"HUMAN"} and self.selected_business_id != DEFAULT_BUSINESS_ID:
                content = message["content"]
                if self.selected_business_id not in content and not any(offer in content for offer in self.registry.offers_for(self.selected_business_id)):
                    # Les messages sans identifiant business restent visibles dans le cockpit global mais pas dans un contexte ciblé.
                    continue
            color = AGENT_COLORS.get(role, COLORS["info"] if role == "HUMAN" else COLORS["muted"])
            row = ctk.CTkFrame(parent, fg_color=COLORS["surface2"], corner_radius=9)
            row.pack(fill="x", pady=2)
            top = ctk.CTkFrame(row, fg_color="transparent")
            top.pack(fill="x", padx=9, pady=(5, 1))
            ctk.CTkLabel(top, text=role, text_color=color, font=("Segoe UI", 8, "bold")).pack(side="left")
            ctk.CTkLabel(top, text=self._format_age(message["ts"]), text_color=COLORS["muted"], font=("Segoe UI", 8)).pack(side="right")
            ctk.CTkLabel(row, text=message["content"], text_color=COLORS["text"], anchor="w", justify="left", wraplength=900).pack(fill="x", padx=9, pady=(0, 7))

    def _render_agents(self, parent, compact: bool = False) -> None:
        for widget in list(parent.winfo_children()): widget.destroy()
        for index, row in enumerate(self._agent_rows()):
            role = row["role"]
            color = AGENT_COLORS.get(role, COLORS["text"])
            if compact:
                card = ctk.CTkFrame(parent, fg_color=COLORS["surface2"], corner_radius=9)
                card.pack(fill="x", pady=2)
                ctk.CTkLabel(card, text="●", text_color=color, font=("Segoe UI", 12)).pack(side="left", padx=(8, 4), pady=6)
                ctk.CTkLabel(card, text=role, text_color=COLORS["text"], font=("Segoe UI", 9, "bold")).pack(side="left")
                self._pill(card, row["status"], row["status_color"]).pack(side="right", padx=8)
            else:
                card = self._card(parent)
                card.grid(row=index // 2, column=index % 2, sticky="nsew", padx=5, pady=5)
                head = ctk.CTkFrame(card, fg_color="transparent")
                head.pack(fill="x", padx=14, pady=(12, 2))
                ctk.CTkLabel(head, text=role, text_color=color, font=("Segoe UI", 16, "bold")).pack(side="left")
                self._pill(head, row["status"], row["status_color"]).pack(side="right")
                ctk.CTkLabel(card, text=AGENT_DESCRIPTIONS.get(role, ""), text_color=COLORS["muted"], anchor="w").pack(fill="x", padx=14)
                ctk.CTkLabel(card, text=row["latest"], text_color=COLORS["text"], anchor="w", justify="left", wraplength=560).pack(fill="x", padx=14, pady=(8, 4))
                ctk.CTkLabel(card, text=f"{row['tasks']} tâches · {row['messages']} messages", text_color=COLORS["muted"], anchor="w").pack(fill="x", padx=14, pady=(0, 12))

    def _render_task_list(self, parent) -> None:
        for widget in list(parent.winfo_children()): widget.destroy()
        icons = {"queued": "…", "running": "▶", "waiting_human": "?", "done": "✓", "failed": "✗", "cancelled": "■"}
        wanted = getattr(self, "mission_filter", ctk.StringVar(value="Toutes")).get()
        for task in self._task_rows():
            status = task.get("status")
            if wanted == "Actives" and status not in {"queued", "running"}:
                continue
            if wanted == "Humain" and status != "waiting_human":
                continue
            if wanted == "Terminées" and status not in {"done", "failed", "cancelled"}:
                continue
            business = str(task.get("business") or "")
            if self.selected_business_id != DEFAULT_BUSINESS_ID and business and self.selected_business_id not in business.lower():
                continue
            card = ctk.CTkFrame(parent, fg_color=COLORS["surface2"], corner_radius=10)
            card.pack(fill="x", pady=3)
            line = ctk.CTkFrame(card, fg_color="transparent")
            line.pack(fill="x", padx=10, pady=(7, 2))
            ctk.CTkLabel(line, text=f"{icons.get(status, '·')}  #{task.get('id')} · {task.get('kind', 'tâche')}", text_color=COLORS["text"], font=("Segoe UI", 10, "bold")).pack(side="left")
            self._pill(line, status or "?", {"running": COLORS["info"], "waiting_human": COLORS["warn"], "done": COLORS["good"], "failed": COLORS["bad"]}.get(status, COLORS["muted"])).pack(side="right")
            detail = task.get("error") if status == "failed" else task.get("business") or task.get("result") or ""
            ctk.CTkLabel(card, text=str(detail)[:300], text_color=COLORS["muted"], anchor="w", wraplength=900, justify="left").pack(fill="x", padx=10, pady=(0, 7))

    def _render_handoffs(self, parent) -> None:
        for widget in list(parent.winfo_children()): widget.destroy()
        pending = self._pending_requests()
        if not pending:
            ctk.CTkLabel(parent, text="✓ Rien ne bloque actuellement. Aucune intervention humaine en attente.", text_color=COLORS["good"]).pack(anchor="w", padx=12, pady=20)
            return
        for item in pending:
            card = self._card(parent)
            card.pack(fill="x", pady=5, padx=2)
            ctk.CTkLabel(card, text=item["who"], text_color=COLORS["warn"], font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x", padx=14, pady=(10, 2))
            ctk.CTkLabel(card, text=item["question"], text_color=COLORS["text"], anchor="w", justify="left", wraplength=1050).pack(fill="x", padx=14)
            entry = ctk.CTkEntry(card, placeholder_text="Réponse ou validation…")
            entry.pack(fill="x", padx=14, pady=7)
            ctk.CTkButton(card, text="Envoyer", width=100, command=lambda i=item, e=entry: self._answer(i, e)).pack(anchor="e", padx=14, pady=(0, 10))

    def _render_metrics(self, parent) -> None:
        for widget in list(parent.winfo_children()): widget.destroy()
        metrics = [m for m in db.metrics_list() if self._business_matches_offer(m.get("offer_id"))]
        if not metrics:
            ctk.CTkLabel(parent, text="Aucune métrique QC dans ce contexte.", text_color=COLORS["muted"]).pack(anchor="w", padx=10, pady=18)
            return
        for metric in metrics[-30:][::-1]:
            row = ctk.CTkFrame(parent, fg_color=COLORS["surface2"], corner_radius=9)
            row.pack(fill="x", pady=2)
            score = metric.get("score") or 0
            color = COLORS["good"] if score >= config.QC_SHIP_SCORE else COLORS["warn"]
            ctk.CTkLabel(row, text=metric["offer_id"], text_color=COLORS["text"], font=("Segoe UI", 10, "bold")).pack(side="left", padx=10, pady=7)
            ctk.CTkLabel(row, text=f"{score}/35", text_color=color, font=("Segoe UI", 10, "bold")).pack(side="right", padx=10)
            ctk.CTkLabel(row, text=f"humanité {metric.get('humanite')}/5 · {metric.get('verdict')}", text_color=COLORS["muted"]).pack(side="right")

    def _render_production_summary(self) -> None:
        for widget in list(self.production_summary.winfo_children()): widget.destroy()
        run = db.current_run() or {}
        offer = run.get("offer_id")
        if offer and not self._business_matches_offer(offer):
            offer = None
        ctk.CTkLabel(self.production_summary, text=offer or "Aucun rendu dans ce contexte", text_color=COLORS["text"], font=("Segoe UI", 16, "bold"), anchor="w").pack(fill="x", padx=14, pady=(4, 2))
        ctk.CTkLabel(self.production_summary, text=run.get("step") or "Prêt", text_color=COLORS["muted"], anchor="w", wraplength=430).pack(fill="x", padx=14, pady=(0, 8))
        final = config.PROJECT_ROOT / "out" / offer / "final.mp4" if offer else None
        ctk.CTkLabel(self.production_summary, text=f"Final : {final if final and final.exists() else 'pas encore disponible'}", text_color=COLORS["muted"], anchor="w", wraplength=430).pack(fill="x", padx=14, pady=(0, 12))

    def _paint_system_checks(self, checks: list[dict]) -> None:
        for widget in list(self.system_checks.winfo_children()): widget.destroy()
        for check in checks:
            color = COLORS["good"] if check["ok"] else (COLORS["bad"] if check["blocking"] else COLORS["warn"])
            row = ctk.CTkFrame(self.system_checks, fg_color=COLORS["surface2"], corner_radius=8)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text="●", text_color=color, width=20).pack(side="left", padx=(8, 0))
            ctk.CTkLabel(row, text=check["name"], text_color=COLORS["text"], font=("Segoe UI", 9, "bold"), anchor="w").pack(side="left", padx=5, pady=7)
            ctk.CTkLabel(row, text=check["detail"], text_color=COLORS["muted"], anchor="e", justify="right", wraplength=330).pack(side="right", padx=8)

    # actions ------------------------------------------------------------------------
    def _start_cycle(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self._set_status("Cycle déjà en cours", COLORS["warn"])
            return
        if db.run_lock_holder():
            self._set_status("Un autre cycle détient le verrou", COLORS["warn"])
            return
        offer = self.production_offer.get() if hasattr(self, "production_offer") else "auto"
        if offer == "Aucune offre":
            self._set_status("Aucune offre disponible dans ce business", COLORS["warn"])
            return
        args = ["cycle"] if offer in {"", "auto", None} else ["cycle", "--offer", offer]
        self.proc, log = procs.spawn(args, "cycle")
        self._set_status(f"Cycle lancé · {self._business_label()}", COLORS["info"])
        self.page_subtitle.configure(text=f"Cycle lancé · journal {log.name}")

    def _start_mission(self) -> None:
        entry = getattr(self, "mission_entry", None)
        if entry is None:
            return
        text = entry.get().strip()
        if not text:
            return
        if self.proc is not None and self.proc.poll() is None:
            self._set_status("Une mission/cycle tourne déjà", COLORS["warn"])
            return
        context = self._business_label()
        goal = f"[Business: {context}] {text}" if context != "Tous les business" else text
        db.post("HUMAN", f"objectif : {goal}")
        entry.delete(0, "end")
        business = self.selected_business_id
        args = ["mission", "--business", business, goal] if business != DEFAULT_BUSINESS_ID else ["mission", goal]
        self.proc, _ = procs.spawn(args, "mission")
        self._set_status(f"Mission lancée · {context}", COLORS["info"])

    def _stop_cycle(self) -> None:
        db.request_stop()
        self._set_status("Arrêt demandé au prochain checkpoint", COLORS["warn"])

    def _toggle_worker(self) -> None:
        if self.worker_proc is not None and self.worker_proc.poll() is None:
            proc, self.worker_proc = self.worker_proc, None
            try:
                from octopus import tasks
                for task in tasks.list_tasks(status="running", limit=20):
                    tasks.cancel(task["id"], "worker arrêté depuis la GUI")
            except Exception:
                pass
            self._set_status("Arrêt du worker…", COLORS["warn"])
            self.after(12000, lambda: self._kill_worker(proc))
            return
        self.worker_proc, log = procs.spawn(["worker"], "worker", module="octopus")
        self._set_status(f"Worker lancé · {log.name}", COLORS["info"])

    def _kill_worker(self, proc: subprocess.Popen) -> None:
        from ..tools import kill_tree
        if proc.poll() is None:
            kill_tree(proc)
        self._set_status("Worker arrêté", COLORS["warn"])

    def _send_message(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        import re
        match = re.match(r"@(\w+)\s*(.*)", text, re.S)
        role, content = (match.group(1).upper(), match.group(2).strip()) if match and match.group(2).strip() else ("ORBIT", text)
        if role not in config.AGENTS:
            role = "ORBIT"
        db.post("HUMAN", text)
        proc, _ = procs.spawn(["msg", role, content], f"msg-{role}")
        self.msg_procs.append(proc)
        self._set_status(f"Message → {role}", COLORS["info"])

    def _handle_command(self, raw: str) -> None:
        text = raw.strip()
        if not text:
            return
        self.command_entry.delete(0, "end")
        if text.startswith("/"):
            command = text.split()[0].lower()
            if command in {"/business", "/biz"}:
                self._show_page("Business"); return
            if command in {"/mission", "/goal"}:
                self._show_page("Missions"); return
            if command in {"/production", "/video"}:
                self._show_page("Production"); return
            if command in {"/agents", "/agent"}:
                self._show_page("Agents"); return
            if command in {"/human", "/handoff"}:
                self._show_page("Humain"); return
            if command in {"/browser", "/web"}:
                self._show_page("Navigateur"); return
            if command in {"/system", "/doctor"}:
                self._show_page("Système")
                if command == "/doctor": self._run_doctor()
                return
        self._send_message(text)

    def _publish(self) -> None:
        offer = self.production_offer.get() if hasattr(self, "production_offer") else "auto"
        if offer in {"", "auto", "Aucune offre"}:
            run = db.current_run() or {}
            offer = run.get("offer_id") or ""
        if not offer or not self._business_matches_offer(offer):
            self._set_status("Aucune offre publiable dans ce contexte", COLORS["warn"])
            return
        from ..publish import publish
        try:
            result = publish(offer, dry_run=True)
        except Exception as exc:
            self._set_status(f"Publication : {exc}", COLORS["bad"])
            return
        self._set_status(f"Publication dry-run · {result['plan'].get('title', offer)}", COLORS["good"])

    def _open_browser(self) -> None:
        proc, _ = procs.spawn(["browse-open"], "browse-open")
        self.msg_procs.append(proc)
        self._set_status("Navigateur visible lancé", COLORS["info"])

    def _browse_url(self) -> None:
        url = getattr(self, "browser_entry", None).get().strip() if hasattr(self, "browser_entry") else ""
        if not url:
            return
        db.set_state("browser_url", url)
        proc, _ = procs.spawn(["browser", url], "browser-check")
        self.msg_procs.append(proc)
        self._set_status("Navigation demandée", COLORS["info"])

    def _open_studio(self) -> None:
        from .studio import StudioWindow
        if getattr(self, "_studio", None) is not None and self._studio.winfo_exists():
            self._studio.focus()
            return
        self._studio = StudioWindow(self, start_worker=self._toggle_worker)

    def _open_video(self) -> None:
        run = db.current_run() or {}
        offer = run.get("offer_id") or (self.production_offer.get() if hasattr(self, "production_offer") else "")
        if not offer or not self._business_matches_offer(offer):
            self._set_status("Aucune vidéo dans ce contexte", COLORS["warn"])
            return
        path = config.PROJECT_ROOT / "out" / offer / "final.mp4"
        if path.exists():
            _open_path(path)
        else:
            self._set_status(f"Pas de final.mp4 pour {offer}", COLORS["warn"])

    def _answer(self, item: dict, entry: ctk.CTkEntry) -> None:
        text = entry.get().strip()
        if not text:
            return
        try:
            if item["source"] == "task":
                from octopus import tasks
                tasks.answer(item["id"], text)
                db.post("HUMAN", f"réponse envoyée à la tâche #{item['task_id']}")
            else:
                db.answer(item["id"], text)
                db.post("HUMAN", f"réponse envoyée à la demande #{item['id']}")
        except Exception as exc:
            self._set_status(f"Réponse : {exc}", COLORS["bad"])
            return
        entry.delete(0, "end")
        self._set_status("Réponse envoyée", COLORS["good"])
        self._show_page("Humain")

    def _run_doctor(self) -> None:
        if self._system_busy or not hasattr(self, "system_checks"):
            return
        self._system_busy = True
        self._set_status("Diagnostic en cours…", COLORS["info"])

        def worker() -> None:
            try:
                from ..doctor import run_checks
                snapshot = [check.__dict__ for check in run_checks()]
            except Exception as exc:
                snapshot = [{"name": "Doctor", "ok": False, "blocking": True, "detail": f"{type(exc).__name__}: {exc}"}]
            self._background_results.put(("doctor", snapshot))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_doctor(self, snapshot: list[dict]) -> None:
        self._system_busy = False
        if hasattr(self, "system_checks") and self.system_checks.winfo_exists():
            self._paint_system_checks(snapshot)
        blocking = sum(1 for item in snapshot if not item["ok"] and item["blocking"])
        self._set_status("Diagnostic OK" if not blocking else f"{blocking} blocage(s)", COLORS["good"] if not blocking else COLORS["bad"])

    def _orca_status(self) -> None:
        if self._orca_busy:
            return
        self._orca_busy = True
        if hasattr(self, "orca_result"):
            self.orca_result.configure(text="Lecture du statut Orca…", text_color=COLORS["info"])

        def worker() -> None:
            try:
                from .. import orca
                if not orca.enabled():
                    text, color = "Pont Orca désactivé (optionnel).", COLORS["muted"]
                else:
                    text, color = f"Orca actif : {orca.status()}", COLORS["good"]
            except Exception as exc:
                text, color = f"Orca indisponible : {exc}", COLORS["warn"]
            self._background_results.put(("orca", text, color))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_orca(self, text: str, color: str) -> None:
        self._orca_busy = False
        if hasattr(self, "orca_result") and self.orca_result.winfo_exists():
            self.orca_result.configure(text=text, text_color=color)

    def _new_business_dialog(self) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("Nouveau business")
        dialog.geometry("520x430")
        dialog.configure(fg_color=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()
        card = self._card(dialog)
        card.pack(fill="both", expand=True, padx=18, pady=18)
        ctk.CTkLabel(card, text="Créer un contexte business", text_color=COLORS["text"], font=("Segoe UI", 18, "bold")).pack(anchor="w", padx=16, pady=(16, 3))
        ctk.CTkLabel(card, text="Les métadonnées sont locales au poste de contrôle.", text_color=COLORS["muted"], anchor="w").pack(fill="x", padx=16, pady=(0, 12))
        fields = {}
        for label, placeholder in (("Identifiant", "ex. agence_b2b"), ("Nom", "ex. Agence B2B"), ("Description", "à qui s'adresse ce business ?"), ("Offres", "IDs séparés par des virgules")):
            ctk.CTkLabel(card, text=label, text_color=COLORS["muted"], font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=16, pady=(5, 2))
            entry = ctk.CTkEntry(card, placeholder_text=placeholder, height=34)
            entry.pack(fill="x", padx=16, pady=(0, 4))
            fields[label] = entry
        def save() -> None:
            try:
                offers = [item.strip() for item in fields["Offres"].get().split(",") if item.strip()]
                business = self.registry.upsert(fields["Identifiant"].get(), fields["Nom"].get(), fields["Description"].get(), offers)
                self._sync_business_menu()
                self._select_business(business.id)
                dialog.destroy()
                self._show_page("Business")
                self._set_status(f"Business créé : {business.label()}", COLORS["good"])
            except Exception as exc:
                ctk.CTkLabel(card, text=str(exc), text_color=COLORS["bad"], anchor="w", wraplength=430).pack(fill="x", padx=16, pady=5)
        ctk.CTkButton(card, text="Créer le business", height=38, command=save).pack(fill="x", padx=16, pady=14)

    def _select_business(self, business_id: str) -> None:
        self.selected_business_id = business_id
        self.registry.set_current(business_id)
        self._sync_business_menu()
        self._show_page("Business" if business_id != DEFAULT_BUSINESS_ID else "Cockpit")
        self._set_status(f"Contexte : {self._business_label()}", COLORS["info"])

    def _select_business_then(self, page: str, business_id: str) -> None:
        self.selected_business_id = business_id
        self.registry.set_current(business_id)
        self._sync_business_menu()
        self._show_page(page)

    def _attention_text(self) -> str:
        pending = len(self._pending_requests())
        if pending:
            return f"{pending} intervention(s) humaine(s) attendent une réponse.\n\nPasse par Humain avant de relancer une production."
        run = db.current_run() or {}
        if run:
            return f"Dernier contexte : {run.get('offer_id') or '—'}\nÉtape : {run.get('step') or run.get('status') or '—'}"
        return "Aucune alerte opérationnelle.\n\nLe poste est prêt à recevoir un objectif."

    # refresh ----------------------------------------------------------------------------
    def _refresh(self) -> None:
        self._drain_background_results()
        self.msg_procs = [p for p in self.msg_procs if p.poll() is None]
        if self.proc is not None and self.proc.poll() is not None:
            run = db.current_run() or {}
            status = run.get("status") or "done"
            label, color = {"done": ("✓ Terminé", COLORS["good"]), "error": ("✗ Erreur", COLORS["bad"]), "stopped": ("■ Arrêté", COLORS["warn"])}.get(status, ("● Prêt", COLORS["muted"]))
            self._set_status(label, color)
            self.proc = None
        if self.worker_proc is not None and self.worker_proc.poll() is not None:
            self.worker_proc = None
        self.side_worker.configure(text="● Worker actif" if self.worker_proc else "● Worker arrêté", text_color=COLORS["good"] if self.worker_proc else COLORS["muted"])
        # Le libellé n'existe que sur la page Missions : une fois la page quittée, le widget est détruit et
        # l'erreur Tcl arrêtait définitivement ce rafraîchissement (le self.after final n'était plus atteint).
        if self.current_page == "Missions" and hasattr(self, "worker_state") and self.worker_state.winfo_exists():
            self.worker_state.configure(text="Actif" if self.worker_proc else "Arrêté", text_color=COLORS["good"] if self.worker_proc else COLORS["muted"])
        try:
            if self.current_page == "Cockpit" and hasattr(self, "activity_feed"):
                self._render_activity(self.activity_feed)
                self._render_agents(self.agent_overview, compact=True)
            elif self.current_page == "Missions" and hasattr(self, "mission_tasks"):
                self._render_task_list(self.mission_tasks)
            elif self.current_page == "Production" and hasattr(self, "production_metrics"):
                self._render_production_summary()
                self._render_metrics(self.production_metrics)
            elif self.current_page == "Humain":
                for child in self.page_host.winfo_children():
                    if isinstance(child, ctk.CTkScrollableFrame):
                        self._render_handoffs(child); break
            elif self.current_page == "Navigateur":
                self._refresh_browser_view()
        except Exception as exc:
            self._set_status(f"Interface : {type(exc).__name__}", COLORS["bad"])
        self.after(1500, self._refresh)

    def _drain_background_results(self) -> None:
        while True:
            try:
                result = self._background_results.get_nowait()
            except queue.Empty:
                return
            if result[0] == "doctor":
                self._finish_doctor(result[1])
            elif result[0] == "orca":
                self._finish_orca(result[1], result[2])

    def _refresh_browser_view(self) -> None:
        if not hasattr(self, "browser_preview"):
            return
        url = db.get_state("browser_url") or ""
        shot = db.get_state("browser_shot")
        self.browser_state.configure(text=f"URL : {url or '(inactive)'}")
        if not shot or shot == self._last_browser_shot or not Path(shot).exists():
            return
        self._last_browser_shot = shot
        try:
            image = Image.open(shot)
            ratio = min(1.0, 980 / image.width)
            size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
            self._browser_image = ctk.CTkImage(light_image=image, dark_image=image, size=size)
            self.browser_preview.configure(image=self._browser_image, text="")
        except Exception:
            pass

    # utilities -----------------------------------------------------------------------------
    def _set_status(self, text: str, color: str) -> None:
        self.header_status.configure(text=f"● {text}", text_color=color)

    def _run_status(self, run: dict) -> str:
        if self.proc is not None and self.proc.poll() is None:
            return "RUNNING"
        return str(run.get("status") or "IDLE").upper()

    @staticmethod
    def _run_color(run: dict) -> str:
        return {"done": COLORS["good"], "error": COLORS["bad"], "stopped": COLORS["warn"], "running": COLORS["info"]}.get(run.get("status"), COLORS["muted"])

    @staticmethod
    def _format_age(ts: float) -> str:
        age = max(0.0, time.time() - float(ts))
        if age < 60:
            return "à l'instant"
        if age < 3600:
            return f"il y a {int(age // 60)} min"
        if age < 86400:
            return f"il y a {int(age // 3600)} h"
        return time.strftime("%d/%m %H:%M", time.localtime(ts))


def _open_path(path: str | Path) -> None:
    target = str(path)
    if not Path(target).exists():
        return
    if platform.system() == "Windows":
        os.startfile(target)
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


def main() -> None:
    PodaluxWorkbench().mainloop()


if __name__ == "__main__":
    main()
