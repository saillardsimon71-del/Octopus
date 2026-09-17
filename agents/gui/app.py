"""Application desktop Podalux (customtkinter).

Cockpit de suivi + interaction :
- salon des agents (live),
- tableau de bord (coût, rubric, demandes humaines),
- contrôles (démarrer/arrêter un cycle, publier, répondre).

Le cycle tourne en SOUS-PROCESSUS (venv MPT) et communique via SQLite.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from .. import config, db, procs

COLORS = {
    "ORBIT": "#ffb74d", "GROWTH": "#81c784", "LEDGER": "#64b5f6",
    "FORGE": "#ff8a65", "CONVERT": "#ba68c8", "SOUT": "#4db6ac",
    "BROWSER": "#b0bec5", "HUMAN": "#4fc3f7", "TEST": "#90a4ae",
}
DEFAULT_COLOR = "#eceff1"


class PodaluxApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.title("Podalux — usine à Shorts")
        self.geometry("1200x740")
        self.minsize(980, 640)

        self.proc: subprocess.Popen | None = None
        self.msg_procs: list = []
        self._last_msg_id = 0
        self._handoff_widgets = []
        self._handoff_ids = ()
        self._last_shot = None
        self.worker_proc: subprocess.Popen | None = None

        self._build_ui()
        db.init_db()
        self.after(1000, self._refresh)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=2)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))
        top.grid_columnconfigure(5, weight=1)

        self.status_label = ctk.CTkLabel(top, text="● Idle", text_color="#90a4ae",
                                         font=("Segoe UI", 15, "bold"))
        self.status_label.grid(row=0, column=0, padx=(10, 6))
        self.step_label = ctk.CTkLabel(top, text="", text_color="#b0bec5", font=("Segoe UI", 12))
        self.step_label.grid(row=0, column=1, padx=6, sticky="w")

        self.offer_menu = ctk.CTkOptionMenu(top, values=self._offer_values(), width=200)
        self.offer_menu.grid(row=0, column=2, padx=6)

        ctk.CTkButton(top, text="▶ Démarrer cycle", width=130,
                      command=self._start_cycle).grid(row=0, column=3, padx=4)
        ctk.CTkButton(top, text="■ Arrêter", width=90, fg_color="#c62828",
                      hover_color="#b71c1c", command=self._stop_cycle).grid(row=0, column=4, padx=4)
        ctk.CTkButton(top, text="Publier", width=90, fg_color="#2e7d32",
                      hover_color="#1b5e20", command=self._publish).grid(row=0, column=5, padx=4, sticky="e")
        ctk.CTkButton(top, text="▶ Vidéo", width=80, fg_color="#455a64", hover_color="#37474f",
                      command=self._open_video).grid(row=0, column=6, padx=4)
        ctk.CTkButton(top, text="🎬 Studio vidéo", width=120, fg_color="#6a1b9a", hover_color="#4a148c",
                      command=self._open_studio).grid(row=0, column=7, padx=4)
        self.worker_btn = ctk.CTkButton(top, text="⚙ Worker", width=90, fg_color="#37474f", hover_color="#455a64",
                                        command=self._toggle_worker)
        self.worker_btn.grid(row=0, column=8, padx=(4, 10))

        self.salon = ctk.CTkScrollableFrame(self, label_text="Salon des agents")
        self.salon.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=4)

        # barre d'envoi vers le groupe
        input_bar = ctk.CTkFrame(self)
        input_bar.grid(row=2, column=0, sticky="ew", padx=(10, 5), pady=(0, 10))
        input_bar.grid_columnconfigure(0, weight=1)
        self.msg_entry = ctk.CTkEntry(input_bar, placeholder_text="Écrire au groupe (ex. « @SOUT fais une veille sur X »)")
        self.msg_entry.grid(row=0, column=0, sticky="ew", padx=(10, 4), pady=8)
        self.msg_entry.bind("<Return>", lambda e: self._send_message())
        ctk.CTkButton(input_bar, text="Envoyer", width=90,
                      command=self._send_message).grid(row=0, column=1, padx=(4, 10), pady=8)

        # barre « objectif » → mission multi-agents (ORBIT planifie + délègue)
        objective_bar = ctk.CTkFrame(self)
        objective_bar.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))
        objective_bar.grid_columnconfigure(0, weight=1)
        self.objective_entry = ctk.CTkEntry(objective_bar,
            placeholder_text="Objectif libre → mission multi-agents (ex. « fais une veille sur X et propose un plan »)")
        self.objective_entry.grid(row=0, column=0, sticky="ew", padx=(10, 4), pady=8)
        self.objective_entry.bind("<Return>", lambda e: self._start_mission())
        ctk.CTkButton(objective_bar, text="▶ Objectif", width=110, fg_color="#5c6bc0",
                      hover_color="#3f51b5", command=self._start_mission).grid(
            row=0, column=1, padx=(4, 10), pady=8)

        right = ctk.CTkFrame(self)
        right.grid(row=1, column=1, sticky="nsew", padx=(5, 10), pady=4)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(4, weight=1)

        self.cost_label = ctk.CTkLabel(right, text="Coût : —", anchor="w",
                                       font=("Segoe UI", 14, "bold"))
        self.cost_label.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 2))

        self.metrics_label = ctk.CTkLabel(right, text="Métriques : —", anchor="w", justify="left",
                                          font=("Segoe UI", 12))
        self.metrics_label.grid(row=1, column=0, sticky="ew", padx=10, pady=2)

        self.tasks_label = ctk.CTkLabel(right, text="Tâches : —", anchor="w", justify="left",
                                        font=("Segoe UI", 12))
        self.tasks_label.grid(row=2, column=0, sticky="ew", padx=10, pady=2)

        self.browser_frame = ctk.CTkFrame(right, fg_color="#1e272e")
        self.browser_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=(6, 2))
        self.browser_url_label = ctk.CTkLabel(self.browser_frame, text="Navigateur : (inactif)",
                                              anchor="w", font=("Segoe UI", 11))
        self.browser_url_label.pack(anchor="w", padx=8, pady=(6, 2))
        self.browser_open_btn = ctk.CTkButton(self.browser_frame, text="🌐 Ouvrir le navigateur",
                                              height=28, fg_color="#37474f", hover_color="#455a64",
                                              command=self._open_browser)
        self.browser_open_btn.pack(anchor="e", padx=8, pady=(2, 4))
        self.browser_img_label = ctk.CTkLabel(self.browser_frame, text="")
        self.browser_img_label.pack(padx=8, pady=(2, 6))

        self.handoffs_frame = ctk.CTkScrollableFrame(right, label_text="Demandes humaines")
        self.handoffs_frame.grid(row=4, column=0, sticky="nsew", padx=10, pady=(6, 10))

    def _offer_values(self):
        vals = ["auto"]
        if config.JOBS_DIR.exists():
            vals += [p.stem for p in sorted(config.JOBS_DIR.glob("*.json")) if p.stem not in vals]
        for k in config.CATALOG_OFFERS:
            if k not in vals:
                vals.append(k)
        return vals

    # --- rafraîchissement (polling 1 s) ---
    def _refresh(self):
        self.msg_procs = [p for p in self.msg_procs if p.poll() is None]
        try:
            self._refresh_run()
            self._refresh_salon()
            self._refresh_dashboard()
            self._refresh_browser()
            self._refresh_tasks()
            self._refresh_handoffs()
        except Exception as e:
            self.status_label.configure(text=f"● erreur : {e}", text_color="#ef5350")
        self.after(1000, self._refresh)

    def _refresh_run(self):
        run = db.current_run()
        if self.proc is not None and self.proc.poll() is not None:
            # le sous-processus vient de finir
            status = run.get("status") if run else "done"
            txt = {"done": "✓ Terminé", "error": "✗ Erreur", "stopped": "■ Arrêté"}.get(status, "● Idle")
            col = {"done": "#81c784", "error": "#ef5350", "stopped": "#ffb74d"}.get(status, "#90a4ae")
            self.status_label.configure(text=txt, text_color=col)
            self.step_label.configure(text=(run or {}).get("step", ""))
            self.proc = None
        elif self.proc is not None:
            self.status_label.configure(text="● Running", text_color="#ffb74d")
            self.step_label.configure(text=(run or {}).get("step", ""))
        else:
            self.status_label.configure(text="● Idle", text_color="#90a4ae")
            self.step_label.configure(text=(run or {}).get("step", ""))

    def _refresh_salon(self):
        # suivi par identifiant (et non par compteur) : ne rate aucun message,
        # même quand la base dépasse la fenêtre des 120 derniers.
        for m in db.recent_messages(120):
            if m["id"] > self._last_msg_id:
                color = COLORS.get(m["from_agent"], DEFAULT_COLOR)
                ctk.CTkLabel(self.salon, text=f"[{m['from_agent']}] {m['content']}",
                             anchor="w", justify="left", text_color=color,
                             font=("Segoe UI", 12), wraplength=640).pack(anchor="w", pady=1)
                self._last_msg_id = m["id"]

    def _refresh_dashboard(self):
        self.cost_label.configure(
            text=f"Coût aujourd'hui : ${db.cost_today():.4f} · budget par cycle ${config.CYCLE_BUDGET_USD:.2f}")
        mets = db.metrics_list()
        if mets:
            lines = [f"  {m['offer_id']}: {m['score']}/35 · humanité {m['humanite']}/5 · {m['verdict']}"
                     for m in mets[-6:]]
            self.metrics_label.configure(text="Métriques (rubric) :\n" + "\n".join(lines))
        else:
            self.metrics_label.configure(text="Métriques : (aucune encore)")

    def _refresh_tasks(self):
        """File OCTOPUS : 5 dernières tâches et état du worker lancé par la GUI."""
        if self.worker_proc is not None and self.worker_proc.poll() is not None:
            self.worker_proc = None
        self.worker_btn.configure(text="■ Worker" if self.worker_proc else "⚙ Worker",
                                  fg_color="#2e7d32" if self.worker_proc else "#37474f")
        try:
            from octopus import tasks
            recent = tasks.list_tasks(limit=5)
        except Exception as e:
            self.tasks_label.configure(text=f"Tâches : indisponibles ({type(e).__name__})")
            return
        if not recent:
            self.tasks_label.configure(text="Tâches : (file vide)")
            return
        icons = {"queued": "…", "running": "▶", "waiting_human": "?", "done": "✓", "failed": "✗", "cancelled": "■"}
        lines = [f"  {icons.get(t['status'], '·')} #{t['id']} {t['kind']} · {t['status']}" for t in recent]
        self.tasks_label.configure(text="Tâches :\n" + "\n".join(lines))

    def _pending_requests(self) -> list[dict]:
        """Demandes du cycle (podalux.db) et des tâches OCTOPUS, dans un seul panneau."""
        items = [{"key": ("p", h["id"]), "who": h["agent"], "question": h["question"]} for h in db.pending_handoffs()]
        try:
            from octopus import tasks
            items += [{"key": ("t", r["id"]), "who": f"tâche #{r['task_id']}", "question": r["question"]}
                      for r in tasks.pending_human_requests()]
        except Exception:
            pass
        return items

    def _refresh_handoffs(self):
        pending = self._pending_requests()
        ids = tuple(h["key"] for h in pending)
        if ids == self._handoff_ids:
            return  # rien de nouveau → ne pas reconstruire (évite clignotement / perte de focus)
        self._handoff_ids = ids
        for w in self._handoff_widgets:
            w.destroy()
        self._handoff_widgets = []
        for h in pending:
            frm = ctk.CTkFrame(self.handoffs_frame, fg_color="#263238")
            frm.pack(fill="x", pady=4)
            ctk.CTkLabel(frm, text=f"[{h['who']}] {h['question']}", anchor="w",
                         justify="left", wraplength=320, font=("Segoe UI", 12)).pack(anchor="w", padx=8, pady=(6, 2))
            entry = ctk.CTkEntry(frm, placeholder_text="Votre réponse…")
            entry.pack(fill="x", padx=8, pady=2)
            ctk.CTkButton(frm, text="Envoyer", height=28, width=80,
                          command=lambda key=h["key"], e=entry: self._answer(key, e)).pack(anchor="e", padx=8, pady=(2, 6))
            self._handoff_widgets.append(frm)

    # --- actions ---
    def _start_cycle(self):
        if self.proc is not None and self.proc.poll() is None:
            return
        holder = db.run_lock_holder()
        if holder:  # cycle lancé par la CLI, un agent ou une GUI précédente
            self.step_label.configure(text=f"un cycle tourne déjà ({holder})")
            return
        offer = self.offer_menu.get()
        args = ["cycle"]
        if offer != "auto":
            args += ["--offer", offer]
        self.proc, log = procs.spawn(args, "cycle")
        self.step_label.configure(text=f"journal : {log.name}")
        self.status_label.configure(text="● Running", text_color="#ffb74d")

    def _start_mission(self):
        text = self.objective_entry.get().strip()
        if not text:
            return
        if self.proc is not None and self.proc.poll() is None:
            self.step_label.configure(text="un cycle/mission tourne déjà")
            return
        db.post("HUMAN", f"objectif : {text}")
        self.objective_entry.delete(0, "end")
        self.proc, _ = procs.spawn(["mission", text], "mission")
        self.status_label.configure(text="● Mission", text_color="#5c6bc0")

    def _stop_cycle(self):
        db.request_stop()
        self.step_label.configure(text="arrêt demandé (au prochain point d'étape)…")

    def _open_browser(self):
        """Ouvre le navigateur Chromium (visible, persistant) via un sous-processus."""
        p, _ = procs.spawn(["browse-open"], "browse-open")
        self.msg_procs.append(p)

    def _publish(self):
        offer = self.offer_menu.get()
        if offer == "auto":
            run = db.current_run()
            offer = (run or {}).get("offer_id") or ""
        if not offer:
            return
        from ..publish import publish
        try:
            r = publish(offer, dry_run=True)
        except Exception as e:  # avant : exception invisible, le bouton semblait ne rien faire
            ctk.CTkLabel(self.handoffs_frame, text=f"[PUBLICATION] échec : {e}", anchor="w", wraplength=320,
                         text_color="#e57373").pack(fill="x", pady=4)
            return
        ctk.CTkLabel(self.handoffs_frame, text=f"[PUBLICATION dry-run] {r['plan'].get('title', offer)}",
                     anchor="w", wraplength=320, text_color="#81c784").pack(fill="x", pady=4)

    def _answer(self, key, entry):
        val = entry.get().strip()
        if not val:
            return
        source, hid = key
        if source == "t":
            from octopus import tasks
            task_id = tasks.answer(hid, val)
            db.post("HUMAN", f"réponse envoyée (tâche #{task_id}, reprise par le worker)")
        else:
            db.answer(hid, val)
            db.post("HUMAN", f"réponse envoyée (demande #{hid})")

    def _send_message(self):
        import re
        text = self.msg_entry.get().strip()
        if not text:
            return
        # router vers un agent via @mention (défaut : ORBIT, le CEO)
        m = re.match(r"@(\w+)\s*(.*)", text, re.S)
        if m and m.group(2).strip():
            role = m.group(1).upper()
            content = m.group(2).strip()
        else:
            role = "ORBIT"
            content = text
        if role not in config.AGENTS:
            role = "ORBIT"
        db.post("HUMAN", text)
        self.msg_entry.delete(0, "end")
        ctk.CTkLabel(self.salon, text=f"[HUMAN] {text}", anchor="w", justify="left",
                     text_color="#4fc3f7", font=("Segoe UI", 12), wraplength=640).pack(anchor="w", pady=1)
        # dispatcher à l'agent en sous-processus (non-bloquant, la réponse arrive au salon)
        p, _ = procs.spawn(["msg", role, content], f"msg-{role}")
        self.msg_procs.append(p)

    def _open_studio(self):
        """Fenêtre de génération vidéo locale (WanGP)."""
        from .studio import StudioWindow
        if getattr(self, "_studio", None) is not None and self._studio.winfo_exists():
            self._studio.focus()
            return
        self._studio = StudioWindow(self, start_worker=self._toggle_worker)

    def _toggle_worker(self):
        """Lance ou arrête le worker OCTOPUS (exécute les tâches en file et planifiées)."""
        if self.worker_proc is not None and self.worker_proc.poll() is None:
            proc, self.worker_proc = self.worker_proc, None
            try:  # arrêt propre : la tâche en cours s'arrête (verrou libéré, processus enfants fermés)
                from octopus import tasks
                for task in tasks.list_tasks(status="running", limit=20):
                    tasks.cancel(task["id"], "worker arrêté depuis la GUI")
            except Exception:
                pass
            self.step_label.configure(text="arrêt du worker…")
            self.after(15000, lambda: self._kill_worker(proc))
            return
        self.worker_proc, log = procs.spawn(["worker"], "worker", module="octopus")
        self.step_label.configure(text=f"worker lancé (journal : {log.name})")

    def _kill_worker(self, proc):
        from ..tools import kill_tree
        if proc.poll() is None:
            kill_tree(proc)  # arbre complet : node, ffmpeg, navigateur
        self.step_label.configure(text="worker arrêté")

    def _refresh_browser(self):
        url = db.get_state("browser_url")
        shot = db.get_state("browser_shot")
        if url:
            self.browser_url_label.configure(text=f"Navigateur : {url[:64]}")
        if shot == self._last_shot:
            return  # capture inchangée : ne pas relire l'image chaque seconde (audit M5)
        self._last_shot = shot
        if shot and Path(shot).exists():
            try:
                img = Image.open(shot)
                w, h = img.size
                nw = 300
                nh = max(1, int(h * nw / w))
                ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(nw, min(nh, 170)))
                self.browser_img_label.configure(image=ctk_img, text="")
            except Exception:
                pass

    def _open_video(self):
        run = db.current_run()
        offer = (run or {}).get("offer_id") or self.offer_menu.get()
        if offer == "auto":
            return
        mp4 = config.PROJECT_ROOT / "out" / offer / "final.mp4"
        if mp4.exists():
            os.startfile(str(mp4))
        else:
            self.step_label.configure(text=f"pas de final.mp4 pour {offer}")


def main():
    app = PodaluxApp()
    app.mainloop()


if __name__ == "__main__":
    main()

