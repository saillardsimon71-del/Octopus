"""Cockpit desktop OCTOPUS : centre de travail unique du control-plane.

La GUI orchestre les fonctions existantes ; elle ne contient pas de logique métier nouvelle.
Les opérations longues passent par des sous-processus ou des threads, jamais par le thread Tk principal.
"""
from __future__ import annotations

import os
import platform
import subprocess
import threading
import time
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from .. import config, db, procs

COLORS = {
    "bg": "#0b1220", "surface": "#111827", "surface2": "#172033", "surface3": "#1f2a40",
    "border": "#26344d", "text": "#e6edf7", "muted": "#8ea0ba", "accent": "#5b8def",
    "accent_hover": "#4c78d6", "good": "#3ecf8e", "warn": "#f0b35b", "bad": "#ef6b73", "info": "#58b8ff",
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
    "Cockpit": ("Centre de contrôle", "Vue opérationnelle de l'usine Podalux"),
    "Missions": ("Missions", "Donner un objectif à ORBIT et suivre son exécution"),
    "Agents": ("Agents", "Activité et santé du collectif"),
    "Production": ("Production vidéo", "Offres, rendu, studio et QC"),
    "Humain": ("Interventions humaines", "Questions, validations et handoffs"),
    "Navigateur": ("Navigateur", "Session Chromium agentique et observations"),
    "Système": ("Système", "Prévol local, cloud-first et développement Orca"),
}


class PodaluxApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.title("OCTOPUS — Centre de travail")
        self.geometry("1440x900")
        self.minsize(1120, 720)
        self.configure(fg_color=COLORS["bg"])
        self.proc: subprocess.Popen | None = None
        self.worker_proc: subprocess.Popen | None = None
        self.msg_procs: list[subprocess.Popen] = []
        self.current_page = "Cockpit"
        self._last_browser_shot: str | None = None
        self._browser_image = None
        self._refreshing_system = False
        self._orca_state_busy = False
        self._build_shell()
        db.init_db()
        self._show_page("Cockpit")
        self.after(800, self._refresh)

    # shell -------------------------------------------------------------------------
    def _build_shell(self) -> None:
        self.grid_columnconfigure(0, weight=0, minsize=228)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.sidebar = ctk.CTkFrame(self, width=228, corner_radius=0, fg_color=COLORS["surface"])
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        brand = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=18, pady=(20, 18))
        ctk.CTkLabel(brand, text="OCTOPUS", text_color=COLORS["text"], font=("Segoe UI", 24, "bold")).pack(anchor="w")
        ctk.CTkLabel(brand, text="PODALUX CONTROL PLANE", text_color=COLORS["muted"], font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(2, 0))
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        for page in PAGE_META:
            button = ctk.CTkButton(self.sidebar, text=self._nav_label(page), anchor="w", height=40, corner_radius=10,
                                   fg_color="transparent", hover_color=COLORS["surface3"], text_color=COLORS["muted"],
                                   command=lambda p=page: self._show_page(p))
            button.pack(fill="x", padx=12, pady=3)
            self.nav_buttons[page] = button
        foot = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        foot.pack(fill="x", padx=12, pady=14)
        ctk.CTkLabel(foot, text="CLOUD-FIRST", text_color=COLORS["good"], font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=8)
        ctk.CTkLabel(foot, text="control-plane léger · vidéo distante", text_color=COLORS["muted"], font=("Segoe UI", 9)).pack(anchor="w", padx=8, pady=(3, 0))
        main = ctk.CTkFrame(self, fg_color=COLORS["bg"], corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1); main.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 10)); header.grid_columnconfigure(1, weight=1)
        self.page_title = ctk.CTkLabel(header, text="", anchor="w", text_color=COLORS["text"], font=("Segoe UI", 22, "bold")); self.page_title.grid(row=0, column=0, sticky="w")
        self.page_subtitle = ctk.CTkLabel(header, text="", anchor="w", text_color=COLORS["muted"], font=("Segoe UI", 11)); self.page_subtitle.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.header_status = ctk.CTkLabel(header, text="● Idle", text_color=COLORS["muted"], font=("Segoe UI", 11, "bold")); self.header_status.grid(row=0, column=1, rowspan=2, sticky="e")
        self.page_host = ctk.CTkFrame(main, fg_color="transparent"); self.page_host.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 20))
        self.page_host.grid_columnconfigure(0, weight=1); self.page_host.grid_rowconfigure(0, weight=1)

    @staticmethod
    def _nav_label(page: str) -> str:
        return {"Cockpit": "⌂  Cockpit", "Missions": "◇  Missions", "Agents": "◉  Agents",
                "Production": "▣  Production", "Humain": "?  Humain", "Navigateur": "◌  Navigateur",
                "Système": "⚙  Système"}.get(page, page)

    def _show_page(self, page: str) -> None:
        self.current_page = page
        for name, button in self.nav_buttons.items():
            button.configure(fg_color=COLORS["surface3"] if name == page else "transparent", text_color=COLORS["text"] if name == page else COLORS["muted"])
        title, subtitle = PAGE_META[page]
        self.page_title.configure(text=title); self.page_subtitle.configure(text=subtitle)
        for child in self.page_host.winfo_children(): child.destroy()
        getattr(self, f"_page_{self._slug(page)}")()

    @staticmethod
    def _slug(page: str) -> str:
        return page.lower().replace("é", "e").replace(" ", "_")

    def _card(self, parent, title: str | None = None):
        card = ctk.CTkFrame(parent, fg_color=COLORS["surface"], border_width=1, border_color=COLORS["border"], corner_radius=14)
        if title:
            ctk.CTkLabel(card, text=title.upper(), text_color=COLORS["muted"], anchor="w", font=("Segoe UI", 9, "bold")).pack(fill="x", padx=14, pady=(12, 6))
        return card

    def _metric(self, parent, column: int, title: str, value: str, detail: str, color: str = COLORS["text"]) -> None:
        card = self._card(parent); card.grid(row=0, column=column, sticky="nsew", padx=5)
        ctk.CTkLabel(card, text=title.upper(), text_color=COLORS["muted"], font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=14, pady=(12, 2))
        ctk.CTkLabel(card, text=value, text_color=color, font=("Segoe UI", 21, "bold")).pack(anchor="w", padx=14)
        ctk.CTkLabel(card, text=detail, text_color=COLORS["muted"], font=("Segoe UI", 10), anchor="w", wraplength=230).pack(anchor="w", padx=14, pady=(0, 12))

    def _pill(self, parent, text: str, color: str):
        return ctk.CTkLabel(parent, text=f"  {text}  ", text_color=color, fg_color=COLORS["surface3"], corner_radius=8, font=("Segoe UI", 9, "bold"))

    # pages --------------------------------------------------------------------------
    def _page_cockpit(self) -> None:
        root = self.page_host; root.grid_columnconfigure(0, weight=2); root.grid_columnconfigure(1, weight=1); root.grid_rowconfigure(3, weight=1)
        metrics = ctk.CTkFrame(root, fg_color="transparent"); metrics.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        for i in range(4): metrics.grid_columnconfigure(i, weight=1)
        run = db.current_run() or {}; tasks = self._task_rows(); pending = self._pending_requests()
        self._metric(metrics, 0, "Run", self._run_status(run), run.get("step") or "aucun run actif", self._run_color(run))
        self._metric(metrics, 1, "Coût aujourd'hui", f"${db.cost_today():.4f}", f"budget/cycle ${config.CYCLE_BUDGET_USD:.2f}")
        active = sum(1 for task in tasks if task.get("status") in {"queued", "running", "waiting_human"})
        self._metric(metrics, 2, "Tâches", str(active), f"{len(tasks)} dernières dans la file")
        self._metric(metrics, 3, "À traiter", str(len(pending)), "demandes humaines", COLORS["warn"] if pending else COLORS["good"])
        actions = self._card(root, "Actions rapides"); actions.grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=6)
        for i in range(4): actions.grid_columnconfigure(i, weight=1)
        buttons = [("▶  Démarrer cycle", self._start_cycle, COLORS["accent"]), ("◇  Nouvelle mission", lambda: self._show_page("Missions"), COLORS["surface3"]),
                   ("▣  Studio vidéo", self._open_studio, COLORS["surface3"]), ("◌  Navigateur", self._open_browser, COLORS["surface3"])]
        for i, (text, command, fg) in enumerate(buttons):
            ctk.CTkButton(actions, text=text, height=38, command=command, fg_color=fg, hover_color=COLORS["accent_hover"] if fg == COLORS["accent"] else COLORS["surface3"]).grid(row=1, column=i, sticky="ew", padx=6, pady=(0, 12))
        composer = self._card(root, "Message au collectif"); composer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=6); composer.grid_columnconfigure(0, weight=1)
        self.cockpit_msg = ctk.CTkEntry(composer, height=38, placeholder_text="Ex. : @SOUT surveille les nouvelles opportunités B2B")
        self.cockpit_msg.grid(row=1, column=0, sticky="ew", padx=(14, 6), pady=(0, 12)); self.cockpit_msg.bind("<Return>", lambda _e: self._send_message())
        ctk.CTkButton(composer, text="Envoyer", width=110, height=38, command=self._send_message).grid(row=1, column=1, padx=(6, 14), pady=(0, 12))
        activity = self._card(root, "Flux d'activité"); activity.grid(row=3, column=0, sticky="nsew", padx=(0, 6), pady=6); activity.grid_columnconfigure(0, weight=1); activity.grid_rowconfigure(1, weight=1)
        self.activity_feed = ctk.CTkScrollableFrame(activity, fg_color="transparent"); self.activity_feed.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8)); self._render_activity(self.activity_feed)
        agents = self._card(root, "État des agents"); agents.grid(row=3, column=1, sticky="nsew", padx=(6, 0), pady=6)
        self.agent_overview = ctk.CTkScrollableFrame(agents, fg_color="transparent"); self.agent_overview.pack(fill="both", expand=True, padx=6, pady=(0, 6)); self._render_agents(self.agent_overview, compact=True)

    def _page_missions(self) -> None:
        root = self.page_host; root.grid_columnconfigure(0, weight=2); root.grid_columnconfigure(1, weight=1); root.grid_rowconfigure(1, weight=1)
        compose = self._card(root, "Nouvelle mission"); compose.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10)); compose.grid_columnconfigure(0, weight=1)
        self.mission_entry = ctk.CTkEntry(compose, height=40, placeholder_text="Ex. : analyse les offres B2B et prépare une proposition exploitable")
        self.mission_entry.grid(row=1, column=0, sticky="ew", padx=(14, 6), pady=(4, 14)); self.mission_entry.bind("<Return>", lambda _e: self._start_mission())
        ctk.CTkButton(compose, text="Lancer la mission", width=150, height=40, command=self._start_mission).grid(row=1, column=1, padx=(6, 14), pady=(4, 14))
        task_card = self._card(root, "File OCTOPUS"); task_card.grid(row=1, column=0, sticky="nsew", padx=(0, 6)); self.mission_tasks = ctk.CTkScrollableFrame(task_card, fg_color="transparent"); self.mission_tasks.pack(fill="both", expand=True, padx=6, pady=(0, 8)); self._render_task_list(self.mission_tasks)
        worker = self._card(root, "Worker"); worker.grid(row=1, column=1, sticky="nsew", padx=(6, 0)); self.worker_state = ctk.CTkLabel(worker, text="Worker actif" if self.worker_proc else "Worker arrêté", text_color=COLORS["muted"], anchor="w"); self.worker_state.pack(fill="x", padx=14, pady=7)
        ctk.CTkButton(worker, text="Démarrer / arrêter", command=self._toggle_worker).pack(fill="x", padx=14, pady=6); ctk.CTkLabel(worker, text="La file durable garde leases, retries, idempotence et handoffs.", text_color=COLORS["muted"], justify="left", wraplength=300, anchor="w").pack(fill="x", padx=14, pady=8)

    def _page_agents(self) -> None:
        root = self.page_host; root.grid_rowconfigure(0, weight=1); root.grid_columnconfigure(0, weight=1); frame = ctk.CTkScrollableFrame(root, fg_color="transparent"); frame.grid(row=0, column=0, sticky="nsew"); frame.grid_columnconfigure((0, 1), weight=1); self._render_agents(frame)

    def _page_production(self) -> None:
        root = self.page_host; root.grid_columnconfigure(0, weight=1); root.grid_columnconfigure(1, weight=2); root.grid_rowconfigure(1, weight=1)
        controls = self._card(root, "Production"); controls.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 8)); controls.grid_columnconfigure(0, weight=1)
        self.production_offer = ctk.CTkOptionMenu(controls, values=self._offer_values()); self.production_offer.pack(fill="x", padx=14, pady=(2, 8))
        row = ctk.CTkFrame(controls, fg_color="transparent"); row.pack(fill="x", padx=10, pady=(0, 8))
        for text, command in (("▶ Cycle", self._start_cycle), ("🎬 Studio", self._open_studio), ("▶ Ouvrir", self._open_video)):
            ctk.CTkButton(row, text=text, command=command, fg_color=COLORS["surface3"], hover_color=COLORS["surface3"]).pack(side="left", fill="x", expand=True, padx=3)
        ctk.CTkButton(controls, text="Publication dry-run", fg_color="#28623f", hover_color="#32774d", command=self._publish).pack(fill="x", padx=14, pady=(0, 14))
        self.production_summary = self._card(root, "Résultat courant"); self.production_summary.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=(0, 8)); self._render_production_summary()
        qc = self._card(root, "Métriques QC"); qc.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=8); self.production_metrics = ctk.CTkScrollableFrame(qc, fg_color="transparent"); self.production_metrics.pack(fill="both", expand=True, padx=8, pady=(0, 8)); self._render_metrics(self.production_metrics)

    def _page_humain(self) -> None:
        root = self.page_host; root.grid_rowconfigure(0, weight=1); root.grid_columnconfigure(0, weight=1); frame = ctk.CTkScrollableFrame(root, fg_color="transparent"); frame.grid(row=0, column=0, sticky="nsew"); self._render_handoffs(frame)

    def _page_navigateur(self) -> None:
        root = self.page_host; root.grid_rowconfigure(1, weight=1); root.grid_columnconfigure(0, weight=1)
        controls = self._card(root, "Session navigateur"); controls.grid(row=0, column=0, sticky="ew", pady=(0, 8)); controls.grid_columnconfigure(0, weight=1)
        self.browser_entry = ctk.CTkEntry(controls, placeholder_text="https://…"); self.browser_entry.grid(row=1, column=0, sticky="ew", padx=(14, 6), pady=(0, 12)); self.browser_entry.insert(0, db.get_state("browser_url") or "https://www.google.com"); self.browser_entry.bind("<Return>", lambda _e: self._browse_url())
        ctk.CTkButton(controls, text="Ouvrir", width=100, command=self._browse_url).grid(row=1, column=1, padx=(6, 4), pady=(0, 12)); ctk.CTkButton(controls, text="Fenêtre Chromium", width=150, fg_color=COLORS["surface3"], hover_color=COLORS["surface3"], command=self._open_browser).grid(row=1, column=2, padx=(4, 14), pady=(0, 12))
        view = self._card(root, "Observation"); view.grid(row=1, column=0, sticky="nsew"); view.grid_rowconfigure(2, weight=1); view.grid_columnconfigure(0, weight=1)
        self.browser_state = ctk.CTkLabel(view, text="", text_color=COLORS["muted"], anchor="w"); self.browser_state.grid(row=1, column=0, sticky="ew", padx=14); self.browser_preview = ctk.CTkLabel(view, text="Aucune capture récente", text_color=COLORS["muted"]); self.browser_preview.grid(row=2, column=0, sticky="nsew", padx=14, pady=14)

    def _page_systeme(self) -> None:
        root = self.page_host; root.grid_columnconfigure((0, 1), weight=1); root.grid_rowconfigure(0, weight=1)
        health = self._card(root, "Prévol local"); health.grid(row=0, column=0, sticky="nsew", padx=(0, 6)); self.system_checks = ctk.CTkScrollableFrame(health, fg_color="transparent"); self.system_checks.pack(fill="both", expand=True, padx=8, pady=(0, 8));
        ctk.CTkButton(health, text="Rafraîchir diagnostic", command=self._run_doctor).pack(fill="x", padx=14, pady=(0, 14)); self._paint_system_checks([{"name": "Doctor", "ok": True, "blocking": False, "detail": "Diagnostic en cours…"}]); self._run_doctor()
        dev = self._card(root, "Développement assisté Orca"); dev.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.orca_state = ctk.CTkLabel(dev, text="", text_color=COLORS["muted"], anchor="w", justify="left", wraplength=420); self.orca_state.pack(fill="x", padx=14, pady=(0, 8))
        ctk.CTkLabel(dev, text="Objectif", text_color=COLORS["muted"], anchor="w").pack(fill="x", padx=14); self.orca_objective = ctk.CTkEntry(dev, placeholder_text="Ex. : auditer le control-plane"); self.orca_objective.pack(fill="x", padx=14, pady=(3, 8))
        ctk.CTkLabel(dev, text="Spécification", text_color=COLORS["muted"], anchor="w").pack(fill="x", padx=14); self.orca_spec = ctk.CTkTextbox(dev, height=130, wrap="word"); self.orca_spec.pack(fill="x", padx=14, pady=(3, 8))
        self.orca_agent = ctk.CTkOptionMenu(dev, values=["codex", "claude", "opencode"]); self.orca_agent.pack(fill="x", padx=14, pady=(0, 8)); ctk.CTkButton(dev, text="Lancer dans Orca", command=self._orca_start).pack(fill="x", padx=14, pady=6)
        self.orca_result = ctk.CTkLabel(dev, text="", text_color=COLORS["muted"], anchor="w", justify="left", wraplength=420); self.orca_result.pack(fill="x", padx=14, pady=8); self._refresh_orca_state_async()

    # data ---------------------------------------------------------------------------
    def _task_rows(self) -> list[dict]:
        try:
            from octopus import tasks
            return tasks.list_tasks(limit=40)
        except Exception:
            return []

    def _pending_requests(self) -> list[dict]:
        items = [{"source": "podalux", "id": h["id"], "who": h["agent"], "question": h["question"]} for h in db.pending_handoffs()]
        try:
            from octopus import tasks
            items += [{"source": "task", "id": r["id"], "task_id": r["task_id"], "who": f"tâche #{r['task_id']}", "question": r["question"]} for r in tasks.pending_human_requests()]
        except Exception: pass
        return items

    def _agent_rows(self) -> list[dict]:
        messages, tasks, rows = db.recent_messages(300), self._task_rows(), []
        for role in config.AGENTS:
            role_msgs = [m for m in messages if m["from_agent"] == role]
            role_tasks = [t for t in tasks if role in str(t.get("business", "")).upper() or role in str(t.get("kind", "")).upper()]
            running = any(t.get("status") == "running" for t in role_tasks); waiting = any(t.get("status") == "waiting_human" for t in role_tasks); status = "en cours" if running else ("attente humain" if waiting else "au repos")
            rows.append({"role": role, "latest": role_msgs[-1]["content"][:140] if role_msgs else "Aucune activité récente", "activity": status, "status": status, "status_color": COLORS["info"] if running else (COLORS["warn"] if waiting else COLORS["muted"]), "tasks": len(role_tasks), "messages": len(role_msgs)})
        return rows

    def _render_agents(self, parent, compact: bool = False) -> None:
        for widget in list(parent.winfo_children()): widget.destroy()
        for index, row in enumerate(self._agent_rows()):
            role, color = row["role"], AGENT_COLORS.get(row["role"], COLORS["text"])
            if compact:
                card = ctk.CTkFrame(parent, fg_color=COLORS["surface2"], corner_radius=10); card.grid_columnconfigure(1, weight=1)
                ctk.CTkLabel(card, text="●", text_color=color, font=("Segoe UI", 14)).grid(row=0, column=0, padx=(8, 5), pady=7); ctk.CTkLabel(card, text=role, text_color=COLORS["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=1, sticky="w"); ctk.CTkLabel(card, text=row["activity"], text_color=COLORS["muted"], font=("Segoe UI", 9)).grid(row=0, column=2, padx=8); card.pack(fill="x", pady=3)
            else:
                card = self._card(parent); card.grid(row=index // 2, column=index % 2, sticky="nsew", padx=5, pady=5); head = ctk.CTkFrame(card, fg_color="transparent"); head.pack(fill="x", padx=14, pady=(12, 2)); ctk.CTkLabel(head, text=role, text_color=color, font=("Segoe UI", 16, "bold")).pack(side="left"); self._pill(head, row["status"], row["status_color"]).pack(side="right"); ctk.CTkLabel(card, text=AGENT_DESCRIPTIONS.get(role, ""), text_color=COLORS["muted"], anchor="w").pack(fill="x", padx=14); ctk.CTkLabel(card, text=row["latest"], text_color=COLORS["text"], anchor="w", justify="left", wraplength=560).pack(fill="x", padx=14, pady=(9, 4)); ctk.CTkLabel(card, text=f"{row['tasks']} tâches · {row['messages']} messages", text_color=COLORS["muted"], anchor="w").pack(fill="x", padx=14, pady=(0, 12))

    def _render_activity(self, parent) -> None:
        for widget in list(parent.winfo_children()): widget.destroy()
        for m in db.recent_messages(80)[-30:][::-1]:
            role = m["from_agent"]; color = AGENT_COLORS.get(role, COLORS["info"] if role == "HUMAN" else COLORS["muted"]); row = ctk.CTkFrame(parent, fg_color=COLORS["surface2"], corner_radius=9); row.pack(fill="x", pady=2); top = ctk.CTkFrame(row, fg_color="transparent"); top.pack(fill="x", padx=10, pady=(6, 1)); ctk.CTkLabel(top, text=role, text_color=color, font=("Segoe UI", 9, "bold")).pack(side="left"); ctk.CTkLabel(top, text=self._format_age(m["ts"]), text_color=COLORS["muted"], font=("Segoe UI", 8)).pack(side="right"); ctk.CTkLabel(row, text=m["content"], text_color=COLORS["text"], anchor="w", justify="left", wraplength=780).pack(fill="x", padx=10, pady=(0, 7))

    def _render_task_list(self, parent) -> None:
        for widget in list(parent.winfo_children()): widget.destroy()
        icons = {"queued": "…", "running": "▶", "waiting_human": "?", "done": "✓", "failed": "✗", "cancelled": "■"}
        for task in self._task_rows():
            card = ctk.CTkFrame(parent, fg_color=COLORS["surface2"], corner_radius=10); card.pack(fill="x", pady=3); line = ctk.CTkFrame(card, fg_color="transparent"); line.pack(fill="x", padx=10, pady=(7, 2)); ctk.CTkLabel(line, text=f"{icons.get(task['status'], '·')}  #{task['id']} · {task['kind']}", text_color=COLORS["text"], font=("Segoe UI", 10, "bold")).pack(side="left"); self._pill(line, task["status"], {"running": COLORS["info"], "waiting_human": COLORS["warn"], "done": COLORS["good"], "failed": COLORS["bad"]}.get(task["status"], COLORS["muted"])).pack(side="right"); detail = task.get("error") if task["status"] == "failed" else task.get("business"); ctk.CTkLabel(card, text=str(detail or ""), text_color=COLORS["muted"], anchor="w", wraplength=820).pack(fill="x", padx=10, pady=(0, 7))

    def _render_handoffs(self, parent) -> None:
        for widget in list(parent.winfo_children()): widget.destroy()
        pending = self._pending_requests()
        if not pending: ctk.CTkLabel(parent, text="Aucune intervention humaine en attente.", text_color=COLORS["muted"]).pack(anchor="w", padx=10, pady=20); return
        for item in pending:
            card = self._card(parent); card.pack(fill="x", pady=5, padx=2); ctk.CTkLabel(card, text=item["who"], text_color=COLORS["warn"], font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x", padx=14, pady=(10, 2)); ctk.CTkLabel(card, text=item["question"], text_color=COLORS["text"], anchor="w", justify="left", wraplength=900).pack(fill="x", padx=14); entry = ctk.CTkEntry(card, placeholder_text="Votre réponse…"); entry.pack(fill="x", padx=14, pady=7); ctk.CTkButton(card, text="Envoyer", width=100, command=lambda i=item, e=entry: self._answer(i, e)).pack(anchor="e", padx=14, pady=(0, 10))

    def _render_metrics(self, parent) -> None:
        for widget in list(parent.winfo_children()): widget.destroy()
        metrics = db.metrics_list()
        if not metrics: ctk.CTkLabel(parent, text="Aucune métrique QC enregistrée.", text_color=COLORS["muted"]).pack(anchor="w", padx=10, pady=16); return
        for metric in metrics[-20:][::-1]:
            row = ctk.CTkFrame(parent, fg_color=COLORS["surface2"], corner_radius=9); row.pack(fill="x", pady=2); score = metric.get("score") or 0; color = COLORS["good"] if score >= config.QC_SHIP_SCORE else COLORS["warn"]; ctk.CTkLabel(row, text=metric["offer_id"], text_color=COLORS["text"], font=("Segoe UI", 10, "bold")).pack(side="left", padx=10, pady=7); ctk.CTkLabel(row, text=f"{score}/35", text_color=color, font=("Segoe UI", 10, "bold")).pack(side="right", padx=10); ctk.CTkLabel(row, text=f"humanité {metric.get('humanite')}/5 · {metric.get('verdict')}", text_color=COLORS["muted"]).pack(side="right")

    def _render_production_summary(self) -> None:
        for widget in list(self.production_summary.winfo_children()): widget.destroy()
        run = db.current_run() or {}; ctk.CTkLabel(self.production_summary, text=run.get("offer_id") or "Aucun rendu courant", text_color=COLORS["text"], font=("Segoe UI", 16, "bold"), anchor="w").pack(fill="x", padx=14, pady=(4, 2)); ctk.CTkLabel(self.production_summary, text=run.get("step") or "Prêt", text_color=COLORS["muted"], anchor="w", wraplength=650).pack(fill="x", padx=14, pady=(0, 10)); offer = run.get("offer_id"); final = config.PROJECT_ROOT / "out" / offer / "final.mp4" if offer else None; ctk.CTkLabel(self.production_summary, text=f"Final : {final if final and final.exists() else 'Pas encore disponible'}", text_color=COLORS["muted"], anchor="w", wraplength=650).pack(fill="x", padx=14, pady=(0, 12))

    def _paint_system_checks(self, checks: list[dict]) -> None:
        if not hasattr(self, "system_checks"): return
        for widget in list(self.system_checks.winfo_children()): widget.destroy()
        for check in checks:
            color = COLORS["good"] if check["ok"] else (COLORS["bad"] if check["blocking"] else COLORS["warn"]); row = ctk.CTkFrame(self.system_checks, fg_color=COLORS["surface2"], corner_radius=8); row.pack(fill="x", pady=2); ctk.CTkLabel(row, text="●", text_color=color, width=20).pack(side="left", padx=(8, 0)); ctk.CTkLabel(row, text=check["name"], text_color=COLORS["text"], font=("Segoe UI", 10, "bold"), anchor="w").pack(side="left", padx=6, pady=7); ctk.CTkLabel(row, text=check["detail"], text_color=COLORS["muted"], anchor="e", justify="right", wraplength=230).pack(side="right", padx=8)

    # actions ------------------------------------------------------------------------
    def _start_cycle(self) -> None:
        if self.proc is not None and self.proc.poll() is None: self._set_status("● Cycle déjà en cours", COLORS["warn"]); return
        if db.run_lock_holder(): self._set_status("● Un autre cycle détient le verrou", COLORS["warn"]); return
        offer = self.production_offer.get() if hasattr(self, "production_offer") else "auto"; args = ["cycle"] if offer in {"", "auto", None} else ["cycle", "--offer", offer]; self.proc, log = procs.spawn(args, "cycle"); self._set_status("● Cycle en cours", COLORS["info"]); self.page_subtitle.configure(text=f"Cycle lancé · journal {log.name}")

    def _start_mission(self) -> None:
        entry = getattr(self, "mission_entry", None)
        if entry is None: return
        text = entry.get().strip()
        if not text: return
        if self.proc is not None and self.proc.poll() is None: self._set_status("● Une mission/cycle tourne déjà", COLORS["warn"]); return
        db.post("HUMAN", f"objectif : {text}"); entry.delete(0, "end"); self.proc, _ = procs.spawn(["mission", text], "mission"); self._set_status("● Mission en cours", COLORS["info"])

    def _stop_cycle(self) -> None:
        db.request_stop(); self._set_status("● Arrêt demandé", COLORS["warn"])

    def _toggle_worker(self) -> None:
        if self.worker_proc is not None and self.worker_proc.poll() is None:
            proc, self.worker_proc = self.worker_proc, None
            try:
                from octopus import tasks
                for task in tasks.list_tasks(status="running", limit=20): tasks.cancel(task["id"], "worker arrêté depuis la GUI")
            except Exception: pass
            self._set_status("● Arrêt du worker…", COLORS["warn"]); self.after(12000, lambda: self._kill_worker(proc)); return
        self.worker_proc, log = procs.spawn(["worker"], "worker", module="octopus"); self._set_status("● Worker en cours", COLORS["info"])
        if hasattr(self, "worker_state"): self.worker_state.configure(text=f"Worker actif · {log.name}")

    def _kill_worker(self, proc: subprocess.Popen) -> None:
        from ..tools import kill_tree
        if proc.poll() is None: kill_tree(proc)
        self._set_status("● Worker arrêté", COLORS["warn"])

    def _send_message(self) -> None:
        entry = getattr(self, "cockpit_msg", None)
        if entry is None: return
        text = entry.get().strip()
        if not text: return
        import re
        match = re.match(r"@(\w+)\s*(.*)", text, re.S); role, content = (match.group(1).upper(), match.group(2).strip()) if match and match.group(2).strip() else ("ORBIT", text)
        if role not in config.AGENTS: role = "ORBIT"
        db.post("HUMAN", text); entry.delete(0, "end"); proc, _ = procs.spawn(["msg", role, content], f"msg-{role}"); self.msg_procs.append(proc); self._set_status(f"● Message → {role}", COLORS["info"])

    def _answer(self, item: dict, entry: ctk.CTkEntry) -> None:
        text = entry.get().strip()
        if not text: return
        if item["source"] == "task":
            from octopus import tasks
            tasks.answer(item["id"], text); db.post("HUMAN", f"réponse envoyée à la tâche #{item['task_id']}")
        else:
            db.answer(item["id"], text); db.post("HUMAN", f"réponse envoyée à la demande #{item['id']}")
        entry.delete(0, "end"); self._set_status("✓ Réponse envoyée", COLORS["good"])

    def _publish(self) -> None:
        offer = self.production_offer.get() if hasattr(self, "production_offer") else "auto"
        if offer == "auto": offer = (db.current_run() or {}).get("offer_id") or ""
        if not offer: self._set_status("● Aucune offre à publier", COLORS["warn"]); return
        from ..publish import publish
        try: result = publish(offer, dry_run=True)
        except Exception as exc: self._set_status(f"● Publication : {exc}", COLORS["bad"]); return
        self._set_status(f"✓ Publication dry-run : {result['plan'].get('title', offer)}", COLORS["good"])

    def _open_browser(self) -> None:
        proc, _ = procs.spawn(["browse-open"], "browse-open"); self.msg_procs.append(proc); self._set_status("● Navigateur visible", COLORS["info"])

    def _browse_url(self) -> None:
        if not hasattr(self, "browser_entry"): return
        url = self.browser_entry.get().strip()
        if not url: return
        db.set_state("browser_url", url); proc, _ = procs.spawn(["browser", url], "browser-check"); self.msg_procs.append(proc); self._set_status("● Navigation demandée", COLORS["info"])

    def _open_studio(self) -> None:
        from .studio import StudioWindow
        if getattr(self, "_studio", None) is not None and self._studio.winfo_exists(): self._studio.focus(); return
        self._studio = StudioWindow(self, start_worker=self._toggle_worker)

    def _open_video(self) -> None:
        run = db.current_run() or {}; offer = run.get("offer_id") or (self.production_offer.get() if hasattr(self, "production_offer") else "")
        if not offer or offer == "auto": self._set_status("● Aucune offre sélectionnée", COLORS["warn"]); return
        path = config.PROJECT_ROOT / "out" / offer / "final.mp4"
        if path.exists(): _open_path(path)
        else: self._set_status(f"● Pas de final.mp4 pour {offer}", COLORS["warn"])

    def _run_doctor(self) -> None:
        if self._refreshing_system: return
        self._refreshing_system = True; self._set_status("● Diagnostic…", COLORS["info"])
        def worker() -> None:
            try:
                from ..doctor import run_checks
                snapshot = [c.__dict__ for c in run_checks()]
            except Exception as exc:
                snapshot = [{"name": "Doctor", "ok": False, "blocking": True, "detail": f"{type(exc).__name__}: {exc}"}]
            self.after(0, lambda: self._finish_doctor(snapshot))
        threading.Thread(target=worker, daemon=True).start()

    def _finish_doctor(self, snapshot: list[dict]) -> None:
        self._refreshing_system = False
        if hasattr(self, "system_checks"): self._paint_system_checks(snapshot)
        blocking = sum(1 for c in snapshot if not c["ok"] and c["blocking"]); self._set_status("✓ Diagnostic OK" if not blocking else f"● {blocking} blocage(s)", COLORS["good"] if not blocking else COLORS["bad"])

    def _orca_start(self) -> None:
        objective = self.orca_objective.get().strip(); spec = self.orca_spec.get("1.0", "end").strip(); agent = self.orca_agent.get()
        if not objective or not spec: self.orca_result.configure(text="Renseigne l'objectif et la spécification.", text_color=COLORS["warn"]); return
        from .. import orca
        if not orca.enabled(): self.orca_result.configure(text="Active OCTOPUS_ORCA_ENABLED=1 pour utiliser Orca.", text_color=COLORS["warn"]); return
        self.orca_result.configure(text="Création Run → Task → Worker…", text_color=COLORS["info"])
        def worker() -> None:
            try:
                result = orca.start_development_task(objective=objective, spec=spec, agent=agent); text = f"Worker lancé · Run {result['run_id']}"; color = COLORS["good"]
            except Exception as exc:
                text = f"Échec Orca : {exc}"; color = COLORS["bad"]
            self.after(0, lambda: self.orca_result.configure(text=text, text_color=color))
        threading.Thread(target=worker, daemon=True).start()

    # refresh ----------------------------------------------------------------------------
    def _refresh_orca_state_async(self) -> None:
        if not hasattr(self, "orca_state") or self._orca_state_busy: return
        self._orca_state_busy = True
        def worker() -> None:
            try:
                from .. import orca
                if not orca.enabled(): text, color = "Pont Orca désactivé (optionnel).", COLORS["muted"]
                else: text, color = f"Orca actif · {orca.status()}", COLORS["good"]
            except Exception as exc: text, color = f"Orca indisponible : {exc}", COLORS["warn"]
            self.after(0, lambda: self._apply_orca_state(text, color)); self._orca_state_busy = False
        threading.Thread(target=worker, daemon=True).start()

    def _apply_orca_state(self, text: str, color: str) -> None:
        if hasattr(self, "orca_state"): self.orca_state.configure(text=text, text_color=color)

    def _refresh(self) -> None:
        self.msg_procs = [p for p in self.msg_procs if p.poll() is None]
        if self.proc is not None and self.proc.poll() is not None:
            run = db.current_run() or {}; status = run.get("status") or "done"; label, color = {"done": ("✓ Terminé", COLORS["good"]), "error": ("✗ Erreur", COLORS["bad"]), "stopped": ("■ Arrêté", COLORS["warn"]) }.get(status, ("● Idle", COLORS["muted"])); self._set_status(label, color); self.proc = None
        if self.worker_proc is not None and self.worker_proc.poll() is not None: self.worker_proc = None
        try:
            if self.current_page == "Cockpit" and hasattr(self, "activity_feed"): self._render_activity(self.activity_feed); self._render_agents(self.agent_overview, compact=True)
            elif self.current_page == "Missions" and hasattr(self, "mission_tasks"): self._render_task_list(self.mission_tasks); self.worker_state.configure(text="Worker actif" if self.worker_proc else "Worker arrêté")
            elif self.current_page == "Production" and hasattr(self, "production_metrics"): self._render_production_summary(); self._render_metrics(self.production_metrics)
            elif self.current_page == "Humain":
                for child in self.page_host.winfo_children():
                    if isinstance(child, ctk.CTkScrollableFrame): self._render_handoffs(child); break
            elif self.current_page == "Navigateur": self._refresh_browser_view()
            elif self.current_page == "Système": self._refresh_orca_state_async()
        except Exception as exc: self._set_status(f"● Interface : {type(exc).__name__}", COLORS["bad"])
        self.after(1500, self._refresh)

    def _refresh_browser_view(self) -> None:
        if not hasattr(self, "browser_preview"): return
        url = db.get_state("browser_url") or ""; shot = db.get_state("browser_shot"); self.browser_state.configure(text=f"URL : {url or '(inactive)'}")
        if not shot or shot == self._last_browser_shot or not Path(shot).exists(): return
        self._last_browser_shot = shot
        try:
            image = Image.open(shot); ratio = min(1.0, 950 / image.width); size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))); self._browser_image = ctk.CTkImage(light_image=image, dark_image=image, size=size); self.browser_preview.configure(image=self._browser_image, text="")
        except Exception: pass

    # utilities ---------------------------------------------------------------------
    def _set_status(self, text: str, color: str) -> None: self.header_status.configure(text=text, text_color=color)
    def _run_status(self, run: dict) -> str: return "RUNNING" if self.proc is not None and self.proc.poll() is None else str(run.get("status") or "IDLE").upper()
    @staticmethod
    def _run_color(run: dict) -> str: return {"done": COLORS["good"], "error": COLORS["bad"], "stopped": COLORS["warn"], "running": COLORS["info"]}.get(run.get("status"), COLORS["muted"])
    @staticmethod
    def _format_age(ts: float) -> str:
        age = max(0, time.time() - float(ts))
        if age < 60: return "à l'instant"
        if age < 3600: return f"il y a {int(age // 60)} min"
        if age < 86400: return f"il y a {int(age // 3600)} h"
        return time.strftime("%d/%m %H:%M", time.localtime(ts))
    @staticmethod
    def _offer_values() -> list[str]:
        vals = ["auto"]
        if config.JOBS_DIR.exists(): vals += [p.stem for p in sorted(config.JOBS_DIR.glob("*.json")) if p.stem not in vals]
        for key in config.CATALOG_OFFERS:
            if key not in vals: vals.append(key)
        return vals


def _open_path(path: str | Path) -> None:
    target = str(path)
    if platform.system() == "Windows": os.startfile(target)
    elif platform.system() == "Darwin": subprocess.Popen(["open", target])
    else: subprocess.Popen(["xdg-open", target])


if __name__ == "__main__":
    PodaluxApp().mainloop()
