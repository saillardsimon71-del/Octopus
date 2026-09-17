"""Studio vidéo (customtkinter) : génération locale WanGP / MiniMax H3 depuis l'application."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from octopus.media import library, studio, wangp

from .. import procs

STATUS_COLORS = {"done": "#81c784", "failed": "#ef5350", "cancelled": "#ffb74d", "running": "#4fc3f7",
                 "queued": "#b0bec5"}


def _open_path(path: str | Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - ouverture volontaire par l'utilisateur
    else:
        subprocess.Popen(["xdg-open", str(path)])


class StudioWindow(ctk.CTkToplevel):
    def __init__(self, master=None, start_worker=None):
        super().__init__(master)
        self.title("Studio vidéo — WanGP / MiniMax H3")
        self.geometry("1180x760")
        self.start_worker = start_worker
        self._rows_signature = None
        self._row_widgets: list = []
        self._preview_path = None
        self._models = studio.model_choices()
        self._build()
        self.after(200, self._refresh_environment)
        self.after(500, self._refresh_history)

    # --- construction -------------------------------------------------------------------------
    def _build(self):
        self.grid_columnconfigure(0, weight=0, minsize=430)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.env_label = ctk.CTkLabel(self, text="WanGP : vérification…", anchor="w", font=("Segoe UI", 12))
        self.env_label.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 4))

        form = ctk.CTkFrame(self)
        form.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=6)
        form.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(form, text="Prompt", anchor="w").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 2))
        self.prompt = ctk.CTkTextbox(form, height=190, wrap="word")
        self.prompt.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10)

        self.model_var = ctk.StringVar(value=self._models[0][0] if self._models else "")
        self.resolution_var = ctk.StringVar(value=next(iter(studio.RESOLUTIONS)))
        self.duration_var, self.steps_var = ctk.StringVar(value="5"), ctk.StringVar(value="")
        self.seed_var, self.variants_var = ctk.StringVar(value=""), ctk.StringVar(value="1")
        self.webui_var = ctk.BooleanVar(value=False)
        fields = [("Modèle", ctk.CTkOptionMenu(form, variable=self.model_var, values=[m[0] for m in self._models] or ["-"],
                                               dynamic_resizing=False, width=280)),
                  ("Résolution", ctk.CTkOptionMenu(form, variable=self.resolution_var, values=list(studio.RESOLUTIONS))),
                  ("Durée (s)", ctk.CTkEntry(form, textvariable=self.duration_var)),
                  ("Étapes (vide = défaut)", ctk.CTkEntry(form, textvariable=self.steps_var)),
                  ("Graine (vide = aléatoire)", ctk.CTkEntry(form, textvariable=self.seed_var)),
                  ("Variantes (1-8)", ctk.CTkEntry(form, textvariable=self.variants_var))]
        for i, (label, widget) in enumerate(fields, start=2):
            ctk.CTkLabel(form, text=label, anchor="w").grid(row=i, column=0, sticky="w", padx=10, pady=4)
            widget.grid(row=i, column=1, sticky="ew", padx=10, pady=4)
        row = 2 + len(fields)
        ctk.CTkCheckBox(form, text="Autoriser avec l'interface WanGP ouverte (risque mémoire)",
                        variable=self.webui_var).grid(row=row, column=0, columnspan=2, sticky="w", padx=10, pady=6)
        buttons = ctk.CTkFrame(form, fg_color="transparent")
        buttons.grid(row=row + 1, column=0, columnspan=2, sticky="ew", padx=10, pady=8)
        ctk.CTkButton(buttons, text="🎬 Générer", width=130, command=self._generate).pack(side="left", padx=(0, 6))
        ctk.CTkButton(buttons, text="Diagnostic WanGP", width=140, fg_color="#455a64", hover_color="#37474f",
                      command=self._probe).pack(side="left", padx=6)
        ctk.CTkButton(buttons, text="⚙ Worker", width=90, fg_color="#37474f", hover_color="#455a64",
                      command=self._worker).pack(side="left", padx=6)
        self.form_status = ctk.CTkLabel(form, text="", anchor="w", justify="left", wraplength=400, text_color="#b0bec5")
        self.form_status.grid(row=row + 2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 6))
        self.preview_label = ctk.CTkLabel(form, text="")
        self.preview_label.grid(row=row + 3, column=0, columnspan=2, padx=10, pady=6)

        self.history = ctk.CTkScrollableFrame(self, label_text="Historique des générations")
        self.history.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=6)

    # --- environnement ------------------------------------------------------------------------
    def _refresh_environment(self):
        def check():
            install = wangp.discover()
            busy = wangp.running_instances() if install.ok else []
            if not install.ok:
                text, color = "WanGP : " + "; ".join(install.problems), "#ef5350"
            elif busy:
                text = (f"WanGP ouvert dans Pinokio (PID {', '.join(str(b['pid']) for b in busy)}) : arrêtez-le avant de "
                        f"générer ici · {studio.hardware_summary()}")
                color = "#ffb74d"
            else:
                text, color = f"WanGP prêt : {install.app_dir} · {studio.hardware_summary()}", "#81c784"
            self.after(0, lambda: self.env_label.configure(text=text, text_color=color))
        threading.Thread(target=check, daemon=True).start()

    # --- actions ------------------------------------------------------------------------------
    def _form(self) -> dict:
        model_type = dict(self._models).get(self.model_var.get(), "")
        return {"prompt": self.prompt.get("1.0", "end"), "model_type": model_type, "resolution": self.resolution_var.get(),
                "duration_s": self.duration_var.get(), "steps": self.steps_var.get(), "seed": self.seed_var.get(),
                "variants": self.variants_var.get(), "allow_with_webui": self.webui_var.get(),
                "parent_id": getattr(self, "_parent_id", None)}

    def _generate(self):
        try:
            task_id = studio.submit(self._form())
        except studio.FormError as exc:
            self.form_status.configure(text=f"Formulaire : {exc}", text_color="#ef5350")
            return
        self._parent_id = None
        self.form_status.configure(text=f"Tâche #{task_id} en file. Le worker doit tourner (bouton ⚙ Worker).",
                                   text_color="#81c784")
        self._rows_signature = None

    def _probe(self):
        proc, log = procs.spawn(["video", "probe"], "wangp-probe", module="octopus")
        self.form_status.configure(text=f"Diagnostic lancé (1 à 3 min, journal {log.name})…", text_color="#b0bec5")

        def wait():
            proc.wait()
            self._models = studio.model_choices()
            self.after(0, self._after_probe)
        threading.Thread(target=wait, daemon=True).start()

    def _after_probe(self):
        self.form_status.configure(text="Diagnostic terminé.", text_color="#81c784")
        self._refresh_environment()

    def _worker(self):
        if callable(self.start_worker):
            self.start_worker()
            self.form_status.configure(text="Worker : état basculé depuis la fenêtre principale.", text_color="#b0bec5")
        else:
            procs.spawn(["worker"], "worker", module="octopus")
            self.form_status.configure(text="Worker lancé.", text_color="#81c784")

    def _reuse(self, gen: dict):
        form = studio.form_from_generation(gen)
        self.prompt.delete("1.0", "end")
        self.prompt.insert("1.0", form["prompt"])
        label = next((lbl for lbl, mt in self._models if mt == form["model_type"]), None)
        if label:
            self.model_var.set(label)
        if form["resolution"] in studio.RESOLUTIONS:
            self.resolution_var.set(form["resolution"])
        self.duration_var.set(form["duration_s"])
        self.steps_var.set(form["steps"])
        self.seed_var.set("")
        self._parent_id = gen["id"]
        self.form_status.configure(text=f"Prompt de #{gen['id']} repris : modifiez puis Générer (variante liée).",
                                   text_color="#b0bec5")

    def _cancel(self, gen: dict):
        self.form_status.configure(text=f"#{gen['id']} : {studio.cancel_generation(gen)}", text_color="#ffb74d")

    # --- historique ---------------------------------------------------------------------------
    def _refresh_history(self):
        try:
            rows = studio.history()
            pending = studio.pending_without_generation()
        except Exception as exc:  # base occupée : on réessaie au prochain tour
            self.form_status.configure(text=f"Historique indisponible : {exc}", text_color="#ef5350")
            self.after(3000, self._refresh_history)
            return
        signature = tuple((r["id"], r["status"], r["progress"], r["task_status"]) for r in rows) + \
            tuple((p["id"], p["status"]) for p in pending)
        if signature != self._rows_signature:
            self._rows_signature = signature
            self._render(rows, pending)
        running = next((r for r in rows if r["status"] == "running" and r.get("preview_path")), None)
        self._show_preview(running["preview_path"] if running else None)
        self.after(2000, self._refresh_history)

    def _render(self, rows, pending):
        for widget in self._row_widgets:
            widget.destroy()
        self._row_widgets = []
        for task in pending:
            text = f"Tâche #{task['id']} · {task['status']} · en attente du worker"
            if task.get("error"):
                text += f" · {(task['error'] or '').splitlines()[0][:120]}"
            label = ctk.CTkLabel(self.history, text=text, anchor="w", text_color="#ffb74d", wraplength=640, justify="left")
            label.pack(fill="x", padx=6, pady=2)
            self._row_widgets.append(label)
        for gen in rows:
            frame = ctk.CTkFrame(self.history, fg_color="#263238")
            frame.pack(fill="x", padx=4, pady=3)
            head = (f"#{gen['id']} · {gen['status']} · {gen['model_type']} · "
                    f"{gen.get('width') or '?'}x{gen.get('height') or '?'} · {gen.get('duration_s') or '?'} s")
            ctk.CTkLabel(frame, text=head, anchor="w", text_color=STATUS_COLORS.get(gen["status"], "#eceff1"),
                         font=("Segoe UI", 12, "bold")).pack(fill="x", padx=8, pady=(4, 0))
            ctk.CTkLabel(frame, text=gen["prompt"][:220], anchor="w", justify="left", wraplength=640).pack(fill="x", padx=8)
            if gen["status"] in studio.BUSY_STATUSES:
                bar = ctk.CTkProgressBar(frame)
                bar.set(max(0, min(1, (gen["progress"] or 0) / 100)))
                bar.pack(fill="x", padx=8, pady=2)
                ctk.CTkLabel(frame, text=gen.get("status_text") or gen.get("phase") or "", anchor="w",
                             text_color="#90a4ae").pack(fill="x", padx=8)
            error = gen.get("error") or (gen["task_error"] if gen["status"] == "failed" else "")
            if error:
                ctk.CTkLabel(frame, text=error[:240], anchor="w", justify="left", wraplength=640,
                             text_color="#ef9a9a").pack(fill="x", padx=8)
            actions = ctk.CTkFrame(frame, fg_color="transparent")
            actions.pack(fill="x", padx=6, pady=(2, 6))
            if gen.get("output_path") and Path(gen["output_path"]).exists():
                ctk.CTkButton(actions, text="▶ Ouvrir", width=80, command=lambda p=gen["output_path"]: _open_path(p)).pack(side="left", padx=2)
                ctk.CTkButton(actions, text="Dossier", width=70, fg_color="#455a64",
                              command=lambda p=gen["output_path"]: _open_path(Path(p).parent)).pack(side="left", padx=2)
            ctk.CTkButton(actions, text="Réutiliser", width=90, fg_color="#5c6bc0",
                          command=lambda g=gen: self._reuse(g)).pack(side="left", padx=2)
            if gen["status"] in studio.BUSY_STATUSES:
                ctk.CTkButton(actions, text="Annuler", width=80, fg_color="#c62828",
                              command=lambda g=gen: self._cancel(g)).pack(side="left", padx=2)
            self._row_widgets.append(frame)
        if not rows and not pending:
            label = ctk.CTkLabel(self.history, text="Aucune génération pour l'instant.", text_color="#90a4ae")
            label.pack(pady=20)
            self._row_widgets.append(label)

    def _show_preview(self, path):
        if path == self._preview_path:
            return
        self._preview_path = path
        if not path or not Path(path).exists():
            self.preview_label.configure(image=None, text="")
            return
        try:
            img = Image.open(path)
            ratio = min(380 / img.width, 220 / img.height)
            size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
            self.preview_label.configure(image=ctk.CTkImage(light_image=img, dark_image=img, size=size), text="")
        except Exception:
            pass
