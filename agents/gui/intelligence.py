"""Extension entrepreneuriale du Workbench OCTOPUS.

Le centre Intelligence ne remplace aucun moteur métier. Il transforme la vision
long-terme du système en objectifs explicites pour ORBIT et garde les actions
sensibles sous contrôle humain.
"""
from __future__ import annotations

import subprocess

import customtkinter as ctk

from .. import db, procs
from octopus import strategy as strategy_store

from .strategy import STRATEGIC_ACTIONS, build_objective, get_action, overview_lines, portfolio_lines
from .workbench import COLORS, DEFAULT_BUSINESS_ID, PAGE_META, PodaluxWorkbench

INTELLIGENCE_META = {
    "title": "Intelligence & stratégie",
    "subtitle": "Découvrir, valider, construire, distribuer, convertir, retenir et réinvestir",
}


class EntrepreneurialWorkbench(PodaluxWorkbench):
    """Workbench avec un espace dédié au pilotage entrepreneurial long-terme."""

    def _build_shell(self) -> None:
        super()._build_shell()
        button = ctk.CTkButton(
            self.sidebar,
            text="◎  Intelligence",
            anchor="w",
            height=39,
            corner_radius=9,
            fg_color="transparent",
            hover_color=COLORS["surface3"],
            text_color=COLORS["muted"],
            command=lambda: self._show_page("Intelligence"),
        )
        button.pack(fill="x", padx=12, pady=2)
        self.nav_buttons["Intelligence"] = button

    def __init__(self) -> None:
        super().__init__()
        self.bind_all("<Control-Key-9>", lambda _e: self._show_page("Intelligence"))

    def _show_page(self, page: str) -> None:
        if page != "Intelligence":
            return super()._show_page(page)
        self.current_page = page
        for name, button in self.nav_buttons.items():
            button.configure(
                fg_color=COLORS["surface3"] if name == page else "transparent",
                text_color=COLORS["text"] if name == page else COLORS["muted"],
            )
        self.page_title.configure(text=INTELLIGENCE_META["title"])
        self.page_subtitle.configure(text=INTELLIGENCE_META["subtitle"])
        self.context_chip.configure(text=self._business_label())
        for child in self.page_host.winfo_children():
            child.destroy()
        self._page_intelligence()

    def _page_intelligence(self) -> None:
        root = self.page_host
        root.grid_columnconfigure(0, weight=2)
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(1, weight=1)
        root.grid_rowconfigure(2, weight=1)

        overview = self._card(root, "Boucle entrepreneuriale")
        overview.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 9))
        ctk.CTkLabel(
            overview,
            text="Découvrir → Valider → Construire → Distribuer → Convertir → Servir → Réinvestir → Revoir",
            text_color=COLORS["text"],
            font=("Segoe UI", 14, "bold"),
            anchor="w",
            wraplength=1000,
        ).pack(fill="x", padx=14, pady=(2, 4))
        business = self.registry.get(self.selected_business_id)
        business_text = business.description if business else "Vue portefeuille : ORBIT peut travailler à travers plusieurs businesses."
        ctk.CTkLabel(
            overview,
            text=f"Contexte actif : {self._business_label()} · {business_text}",
            text_color=COLORS["muted"],
            anchor="w",
            justify="left",
            wraplength=1100,
        ).pack(fill="x", padx=14, pady=(0, 12))

        actions = self._card(root, "Actions stratégiques")
        actions.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        actions.grid_columnconfigure((0, 1), weight=1)
        action_frame = ctk.CTkScrollableFrame(actions, fg_color="transparent")
        action_frame.pack(fill="both", expand=True, padx=6, pady=(0, 7))
        for idx, action in enumerate(STRATEGIC_ACTIONS):
            card = ctk.CTkFrame(action_frame, fg_color=COLORS["surface2"], corner_radius=10)
            card.grid(row=idx // 2, column=idx % 2, sticky="nsew", padx=4, pady=4)
            ctk.CTkLabel(card, text=action.phase.upper(), text_color=COLORS["accent"], font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", padx=12, pady=(10, 2))
            ctk.CTkLabel(card, text=action.title, text_color=COLORS["text"], font=("Segoe UI", 14, "bold"), anchor="w").pack(fill="x", padx=12, pady=1)
            ctk.CTkLabel(card, text=action.description, text_color=COLORS["muted"], anchor="w", justify="left", wraplength=330).pack(fill="x", padx=12, pady=(3, 8))
            ctk.CTkButton(card, text="Lancer avec ORBIT", height=32, command=lambda key=action.key: self._launch_intelligence_action(key)).pack(fill="x", padx=10, pady=(0, 10))

        side = self._card(root, "Pilotage long-terme")
        side.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        offers = self.registry.offers_for(self.selected_business_id)
        run = db.current_run() or {}
        active_tasks = [t for t in self._task_rows() if t.get("status") in {"queued", "running", "waiting_human"}]
        self._mini_stat(side, "Offres actives", str(len(offers)), ", ".join(offers[:4]) or "Aucune offre")
        self._mini_stat(side, "Travail en cours", str(len(active_tasks)), "file durable OCTOPUS")
        self._mini_stat(side, "Dernier run", str(run.get("offer_id") or "—"), str(run.get("step") or run.get("status") or "aucun"))
        ctk.CTkLabel(side, text="Capacités couvertes aujourd'hui", text_color=COLORS["muted"], font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=14, pady=(10, 4))
        for label in ("Recherche marché", "Stratégie de contenu", "Funnels & CTA", "Opérations client", "Réinvestissement"):
            ctk.CTkLabel(side, text=f"✓  {label}", text_color=COLORS["good"], anchor="w", font=("Segoe UI", 10)).pack(fill="x", padx=14, pady=2)
        ctk.CTkLabel(side, text="Données encore à brancher", text_color=COLORS["warn"], font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=14, pady=(14, 4))
        ctk.CTkLabel(side, text="CRM clients structuré · revenus/marges consolidés · métriques natives des plateformes sociales", text_color=COLORS["muted"], anchor="w", justify="left", wraplength=350).pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkLabel(side, text="Garde-fou", text_color=COLORS["warn"], font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=14, pady=(7, 3))
        ctk.CTkLabel(side, text="ORBIT peut rechercher, analyser, proposer et préparer des actions. Les données absentes ne sont pas inventées et les opérations irréversibles restent contrôlées.", text_color=COLORS["muted"], anchor="w", justify="left", wraplength=350).pack(fill="x", padx=14, pady=(0, 12))

        self._strategy_state_card(root)

    def _strategy_state_card(self, root) -> None:
        """Lecture seule de la boucle persistée (octopus.strategy) : business actif ou portefeuille."""
        card = self._card(root, "État stratégique persisté")
        card.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(9, 0))
        try:
            if self.selected_business_id == DEFAULT_BUSINESS_ID:
                lines = ["Portefeuille"] + portfolio_lines(strategy_store.portfolio())
            else:
                lines = overview_lines(strategy_store.overview(self.selected_business_id))
        except Exception as exc:  # base occupée ou migration en cours : afficher, ne pas casser la page
            lines = [f"Lecture impossible : {type(exc).__name__}: {exc}"]
        box = ctk.CTkTextbox(card, height=200, fg_color=COLORS["surface2"], text_color=COLORS["text"], wrap="word")
        box.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        box.insert("1.0", "\n".join(lines))
        box.configure(state="disabled")

    def _mini_stat(self, parent, title: str, value: str, detail: str) -> None:
        card = ctk.CTkFrame(parent, fg_color=COLORS["surface2"], corner_radius=9)
        card.pack(fill="x", padx=10, pady=4)
        ctk.CTkLabel(card, text=title.upper(), text_color=COLORS["muted"], font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", padx=11, pady=(8, 1))
        ctk.CTkLabel(card, text=value, text_color=COLORS["text"], font=("Segoe UI", 15, "bold"), anchor="w", wraplength=330).pack(fill="x", padx=11)
        ctk.CTkLabel(card, text=detail, text_color=COLORS["muted"], anchor="w", justify="left", wraplength=330).pack(fill="x", padx=11, pady=(1, 8))

    def _launch_intelligence_action(self, key: str) -> None:
        action = get_action(key)
        business = self.registry.get(self.selected_business_id) if self.selected_business_id != DEFAULT_BUSINESS_ID else None
        objective = build_objective(action, business, offers=self.registry.offers_for(self.selected_business_id))
        if self.proc is not None and self.proc.poll() is None:
            self._set_status("Une mission/cycle tourne déjà", COLORS["warn"])
            return
        db.post("HUMAN", f"objectif stratégique · {action.title} · {self._business_label()}")
        args = ["mission", "--business", business.id, objective] if business else ["mission", objective]
        self.proc, log = procs.spawn(args, "strategy-mission")
        self._set_status(f"ORBIT · {action.title} lancé", COLORS["info"])
        self.page_subtitle.configure(text=f"Mission stratégique lancée · journal {log.name}")
        self._show_page("Missions")


# Explicit entrypoint: all GUI launchers use the entrepreneurial workbench.
def main() -> None:
    EntrepreneurialWorkbench().mainloop()
