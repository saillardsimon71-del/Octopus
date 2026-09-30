"""Superviseur autonome borné : objectif persistant -> travail -> preuve -> décision -> suite.

Le superviseur n'est pas un second ordonnanceur : il s'exécute comme une tâche durable
(`supervisor.tick`) dans le worker existant et hérite donc de ses baux, de son heartbeat, de son
budget, de son annulation et de sa reprise. Il ne lit et n'écrit que l'état canonique :

- `strategy` : objectifs, décisions et preuves (état épistémique) ;
- `tasks`    : file durable, tentatives, demandes humaines, `not_before` ;
- `journal`  : runs et coûts.

Boucle, sans commande manuelle entre les transitions :

    objectif actif -> tâche `supervisor.objective_work` -> résultat/preuve persistés
      -> évaluation déterministe -> décision -> tâche suivante, clôture ou frontière humaine

Décisions possibles (aucune n'accorde de droit ni ne dépense) :

- `satisfied`      : critère mesuré atteint -> objectif `achieved` ;
- `retry`          : pas de mesure concluante -> prochaine tentative différée (objectif reste actif) ;
- `human_boundary` : ressource ou autorisation manquante -> demande humaine persistée ;
- `exhausted`      : budget de tentatives atteint sans mesure -> objectif `paused`, réactivation humaine.

Un objectif n'est jamais déclaré atteint sans mesure : l'absence de critère mesurable conduit à
`inconclusive`, conformément à `docs/ACCEPTANCE_GATES.md`.
"""
from __future__ import annotations

import re
import hashlib
import time

from . import journal, strategy, tasks

TICK_KIND = "supervisor.tick"
WORK_KIND = "supervisor.objective_work"
ACTOR = "supervisor"

DEFAULT_BUSINESS = "octopus"
DEFAULT_TICK_EVERY_S = 300.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_DELAY_S = 900.0
DEFAULT_MAX_STEPS = 6
DEFAULT_MAX_DURATION_S = 900.0

# Métriques réellement mesurables sans LLM : acquisitions publiques utilisables d'une mission
# (`agents.task_handlers._mission_objective_result`) et actions navigateur dont l'effet a été
# constaté sur le site (registre `channel_actions`, `octopus.browser_workspace`) et fichiers
# téléchargés toujours présents et intacts dans l'espace de la tâche (`browser_workspace.kept_files`),
# et vidéos Agnes vérifiées physiquement (MP4 présent, header ftyp, SHA-256).
SUPPORTED_METRICS = ("usable_browse_count", "verified_browser_actions", "kept_browser_files",
                     "kept_video_files", "verified_video_count")
BROWSER_METRICS = ("verified_browser_actions", "kept_browser_files")
VIDEO_METRICS = ("kept_video_files", "verified_video_count")
METRIC_HELP = {
    "verified_browser_actions": "actions à effet dont le résultat a été constaté sur la page réelle",
    "kept_browser_files": "fichiers téléchargés avec browser_download et présents, intacts, dans l'espace de la tâche",
    "kept_video_files": "vidéos Agnes téléchargées et vérifiées (MP4 présent, SHA-256 intact) dans l'espace de la tâche",
    "verified_video_count": "nombre de générations vidéo Agnes vérifiées physiquement (MP4 + SHA-256)",
}
_CRITERION_RE = re.compile(r"^\s*([a-z_]+)\s*>=\s*(\d+)\s*$", re.IGNORECASE)
# Critères cumulatifs : « a>=1 ; b>=1 », « a>=1 et b>=1 », « a>=1 and b>=1 », « a>=1 && b>=1 ».
_CRITERIA_SPLIT_RE = re.compile(r"\s*(?:;|&&|\bet\b|\band\b)\s*", re.IGNORECASE)

# Pannes qui exigent une ressource ou une autorisation que le superviseur ne crée jamais lui-même.
HUMAN_BOUNDARY_STATUSES = {
    "llm_unavailable": "aucune route LLM gratuite disponible (quota/429) ; aucun fallback payant autorisé",
}


class SupervisorError(ValueError):
    pass


# --- lecture de l'état canonique ------------------------------------------------------------------

def businesses_with_active_objectives() -> list[str]:
    rows = journal.query("SELECT DISTINCT business FROM strategy_objectives WHERE status='active' "
                         "ORDER BY business")
    return [row["business"] for row in rows]


def active_objectives(business: str) -> list[dict]:
    return sorted(strategy.list_items("objective", business, status="active"), key=lambda item: item["id"])


def work_tasks(business: str, objective_id: int) -> list[dict]:
    """Tâches de travail rattachées à l'objectif (lien `executed_by`), dans l'ordre de création."""
    rows = journal.query(
        "SELECT t.* FROM tasks t JOIN strategy_links l ON l.to_type='task' AND l.to_id=t.id "
        "WHERE l.business=? AND l.from_type='objective' AND l.from_id=? AND l.relation='executed_by' "
        "AND t.business=? ORDER BY t.id", (business, objective_id, business))
    return [tasks._row(row) for row in rows]


def evaluated(business: str, objective_id: int, task_id: int) -> bool:
    return bool(_link(business, objective_id, task_id, "evaluated"))


def awaiting_human(business: str, objective_id: int, task_id: int) -> bool:
    """Une frontière humaine a été décidée sur ce travail et n'a pas encore été levée."""
    return bool(_link(business, objective_id, task_id, "awaiting_human"))


def _link(business: str, objective_id: int, task_id: int, relation: str) -> list[dict]:
    return [dict(row) for row in journal.query(
        "SELECT id FROM strategy_links WHERE business=? AND from_type='objective' AND from_id=? "
        "AND to_type='task' AND to_id=? AND relation=?", (business, objective_id, task_id, relation))]


def pending_boundary(business: str, objective_id: int) -> dict | None:
    """Demande humaine déjà ouverte pour cet objectif : ne jamais en ouvrir une seconde."""
    rows = journal.query(
        "SELECT h.* FROM human_requests h JOIN tasks t ON t.id=h.task_id "
        "WHERE h.business=? AND h.key=? AND h.status='pending' AND t.status='waiting_human' ORDER BY h.id",
        (business, boundary_key(objective_id)))
    return dict(rows[0]) if rows else None


def boundary_key(objective_id: int) -> str:
    return f"objective#{int(objective_id)}"


# --- critère mesurable ----------------------------------------------------------------------------

def criterion_parts(criterion: dict | None) -> list[dict]:
    """Critère simple `{"metric", "gte"}` ou cumulatif `{"all": [...]}` -> liste de critères simples."""
    if not criterion:
        return []
    return list(criterion["all"]) if "all" in criterion else [criterion]


def parse_criterion(text) -> dict | None:
    """`success_criteria` lisible -> critère mesurable. Rien d'inventé : None si non mesurable.

    Plusieurs critères cumulatifs sont tous exigés ; un seul critère illisible rend l'ensemble
    non mesurable (jamais un sous-ensemble plus facile à satisfaire)."""
    pieces = [p for p in _CRITERIA_SPLIT_RE.split(str(text or "").strip()) if p.strip()]
    parts = []
    for piece in pieces:
        match = _CRITERION_RE.match(piece)
        if not match:
            return None
        metric, target = match.group(1).lower(), int(match.group(2))
        if metric not in SUPPORTED_METRICS or target <= 0:
            return None
        parts.append({"metric": metric, "gte": target})
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else {"all": parts}


def _checked_part(criterion) -> dict:
    if not isinstance(criterion, dict):
        raise SupervisorError("success_criterion doit être un objet")
    metric = str(criterion.get("metric") or "")
    if metric not in SUPPORTED_METRICS:
        raise SupervisorError(f"métrique non mesurable sans LLM : {metric or '(vide)'} "
                              f"(supportées : {', '.join(SUPPORTED_METRICS)})")
    try:
        target = int(criterion["gte"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SupervisorError("success_criterion.gte doit être un entier positif") from exc
    if target <= 0:
        raise SupervisorError("success_criterion.gte doit être un entier positif")
    return {"metric": metric, "gte": target}


def criterion_for(objective: dict, default: dict | None = None) -> dict | None:
    criterion = parse_criterion(objective.get("success_criteria")) or default
    if criterion in (None, {}):
        return None
    if not isinstance(criterion, dict):
        raise SupervisorError("success_criterion doit être un objet")
    if "all" in criterion:
        parts = criterion["all"]
        if not isinstance(parts, list) or not parts:
            raise SupervisorError("success_criterion.all doit être une liste non vide")
        return {"all": [_checked_part(p) for p in parts]}
    return _checked_part(criterion)


def describe_criterion(criterion: dict | None) -> str:
    return " ET ".join(f"{p['metric']}>={p['gte']}" for p in criterion_parts(criterion))


def goal_text(objective: dict) -> str:
    parts = [f"Objectif persistant #{objective['id']} : {objective['statement']}"]
    if objective.get("success_criteria"):
        parts.append(f"Critères de succès déclarés : {objective['success_criteria']}")
        measured = criterion_parts(parse_criterion(objective["success_criteria"]))
        helps = [f"{p['metric']} = {METRIC_HELP[p['metric']]}" for p in measured if p["metric"] in METRIC_HELP]
        if helps:
            parts.append("Mesure (sur le registre et le disque, pas sur ta réponse) : " + " ; ".join(helps)
                         + (". Tous les critères sont exigés." if len(measured) > 1 else "."))
    if objective.get("timeframe"):
        parts.append(f"Horizon : {objective['timeframe']}")
    b_channels = browser_channels(objective.get("business") or "")
    a_channels = agnes_channels(objective.get("business") or "")
    if b_channels:
        parts.append(_describe_browser_channels(b_channels))
    if a_channels:
        parts.append(_describe_agnes_channels(a_channels))
    if b_channels or a_channels:
        parts.append("Travail borné : hors de ces sites/ressources autorisés, acquisition et qualification seulement. "
                     "Aucun achat, paiement ni dépense sans autorisation humaine explicite. "
                     "Le coût API Agnes reste inconnu tant qu'il n'est pas observé ; "
                     "chaque génération exige une autorisation humaine explicite.")
    else:
        parts.append("Travail borné : acquisition et qualification seulement. Aucun contact, publication, "
                     "achat ni dépense sans autorisation humaine explicite.")
    return "\n".join(parts)


def browser_channels(business: str) -> list[dict]:
    """Canaux sur lesquels l'humain a accordé l'accès `act` pour l'espace navigateur."""
    if not business:
        return []
    from .browser_workspace import CAPABILITY
    rows = journal.query("SELECT id, name, locator, capabilities FROM economic_channels WHERE business=? "
                         "AND status='active' AND access='act' ORDER BY id", (business,))
    return [dict(r) for r in rows if CAPABILITY in str(r["capabilities"] or "")]


def agnes_channels(business: str) -> list[dict]:
    """Canaux sur lesquels l'humain a accordé l'accès `act` pour Agnes vidéo."""
    if not business:
        return []
    from . import economy
    return [c for c in economy.channels(business, status="active", capability="agnes_submit")
            if c["kind"] == "agnes_video" and c["access"] == "act" and c.get("locator")]


def create_video_objective(business: str, prompt: str) -> int:
    """Record a human-authorized Agnes mission; the worker still checks channel access."""
    from . import agnes, economy

    prompt = str(prompt or "").strip()
    if not 1 <= len(prompt) <= agnes.MAX_PROMPT_LENGTH:
        raise SupervisorError(f"Le prompt doit contenir 1 à {agnes.MAX_PROMPT_LENGTH} caractères")
    channels = [c for c in economy.channels(business, status="active", capability="agnes_submit")
                if c["kind"] == "agnes_video" and c["access"] == "act" and c.get("locator")]
    if not channels:
        raise SupervisorError("Un canal Agnes actif avec accès act accordé par un humain est requis")
    agnes._base_url(channels[0]["locator"])
    objective_id = strategy.create("objective", business, f"Vidéo Agnes : {prompt[:80]}",
                                   created_by="human", statement=prompt,
                                   success_criteria="kept_video_files>=1")
    decision_id = strategy.create("decision", business, f"Autorisation vidéo objectif #{objective_id}",
                                  created_by="human", decision="agnes_generate_video",
                                  rationale="Génération Agnes autorisée par l'humain pour cet objectif",
                                  resulting_action="prompt_sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest())
    strategy.transition("decision", decision_id, business, "approved", actor="human")
    strategy.link(business, "decision", decision_id, "objective", objective_id, "authorizes")
    strategy.transition("objective", objective_id, business, "active", actor="human")
    return objective_id


def _video_authorization(business: str, objective_id: int) -> dict | None:
    rows = journal.query(
        "SELECT d.id, d.resulting_action FROM strategy_decisions d JOIN strategy_links l "
        "ON l.from_type='decision' AND l.from_id=d.id "
        "WHERE l.business=? AND l.to_type='objective' AND l.to_id=? AND l.relation='authorizes' "
        "AND d.business=? AND d.status='approved' AND d.decision='agnes_generate_video' "
        "AND d.created_by='human' AND d.decided_by='human' LIMIT 1",
        (business, objective_id, business))
    return dict(rows[0]) if rows else None


def video_authorized(business: str, objective_id: int) -> bool:
    decision = _video_authorization(business, objective_id)
    objective = strategy.get("objective", objective_id, business)
    return bool(decision and objective and decision["resulting_action"] ==
                "prompt_sha256:" + hashlib.sha256(objective["statement"].encode("utf-8")).hexdigest())


def _describe_agnes_channels(channels: list[dict]) -> str:
    if not channels:
        return ""
    return "Ressource vidéo où l'humain a autorisé la génération (outils agnes_* ou agnes.generate_video) : " + \
           " ; ".join(f"canal #{c['id']} {c['name']} ({c['locator']})" for c in channels) + \
           ". Chaque génération est tracée, limitée en quantité/durée, et le MP4 est vérifié physiquement."


def _describe_browser_channels(channels: list[dict]) -> str:
    if not channels:
        return ""
    return "Sites où l'humain a autorisé l'action dans le navigateur (outils browser_*) : " + \
           " ; ".join(f"canal #{c['id']} {c['name']} ({c['locator']})" for c in channels) + \
           ". Chaque action à effet y est tracée et vérifiée ; ailleurs, lecture seule."


# --- création de travail --------------------------------------------------------------------------

def plan_work(business: str, objective: dict, *, criterion: dict | None = None, delay_s: float = 0.0,
              priority: int = 0, max_steps: int = DEFAULT_MAX_STEPS, max_attempts: int = 2,
              max_duration_s: float = DEFAULT_MAX_DURATION_S, profile: str | None = None,
              allowed_tools: list[str] | None = None, parent_id: int | None = None) -> int:
    """Met en file le travail durable d'un objectif. Idempotent par (objectif, tentative)."""
    objective_id = int(objective["id"])
    if _video_authorization(business, objective_id) and not video_authorized(business, objective_id):
        raise SupervisorError("Le prompt vidéo a changé depuis l'autorisation humaine")
    if video_authorized(business, objective_id):
        if objective.get("status") != "active" or criterion_for(objective) != {"metric": "kept_video_files", "gte": 1}:
            raise SupervisorError("Objectif vidéo autorisé invalide ou inactif")
        channels = agnes_channels(business)
        if not channels:
            raise SupervisorError("Canal Agnes actif avec accès act et capacité agnes_submit requis")
        task_id = tasks.enqueue(business, "agnes.generate_video",
                                {"prompt": objective["statement"], "objective_id": objective_id,
                                 "idempotency_key": f"agnes.objective#{objective_id}",
                                 "base_url": channels[0]["locator"]},
                                parent_id=parent_id, max_attempts=1,
                                idempotency_key=f"agnes.objective#{objective_id}")
        strategy.link(business, "objective", objective_id, "task", task_id, "executed_by")
        return task_id
    attempt = len(work_tasks(business, objective_id)) + 1
    payload = {
        "objective_id": objective_id,
        "goal": goal_text(objective),
        "success_criterion": criterion,
        "max_steps": int(max_steps),
        "max_duration_s": float(max_duration_s),
        "profile": profile,
        "allowed_tools": list(allowed_tools) if allowed_tools is not None else None,
        "attempt": attempt,
    }
    task_id = tasks.enqueue(business, WORK_KIND, payload, delay_s=delay_s, priority=priority,
                            max_attempts=max_attempts, parent_id=parent_id,
                            idempotency_key=f"supervisor.work#{objective_id}#{attempt}")
    strategy.link(business, "objective", objective_id, "task", task_id, "executed_by")
    return task_id


def bootstrap(business: str = DEFAULT_BUSINESS, *, tick_every_s: float = DEFAULT_TICK_EVERY_S,
              max_attempts: int = DEFAULT_MAX_ATTEMPTS, retry_delay_s: float = DEFAULT_RETRY_DELAY_S,
              now: float | None = None, max_steps: int | None = None) -> int | None:
    """Démarrage durable : garantit qu'un tick du superviseur est en file, sans le dupliquer.

    C'est la seule action de démarrage nécessaire : le tick se réarme lui-même ensuite.
    """
    if not isinstance(tick_every_s, (int, float)) or not tick_every_s > 0:
        raise SupervisorError("tick_every_s doit être une durée strictement positive")
    if int(max_attempts) < 1:
        raise SupervisorError("max_attempts doit être >= 1")
    if retry_delay_s < 0:
        raise SupervisorError("retry_delay_s doit être positif ou nul")
    open_tick = next((task for task in tasks.list_tasks(business=business, limit=200)
                      if task["kind"] == TICK_KIND and task["status"] in tasks.ACTIVE), None)
    if open_tick is not None:
        return int(open_tick["id"])
    return tasks.enqueue(business, TICK_KIND, tick_input(tick_every_s=tick_every_s, max_attempts=max_attempts,
                                                         retry_delay_s=retry_delay_s, max_steps=max_steps),
                         idempotency_key=f"supervisor.bootstrap#{business}#{int((now or time.time()) // 60)}")


def tick_input(*, tick_every_s: float = DEFAULT_TICK_EVERY_S, max_attempts: int = DEFAULT_MAX_ATTEMPTS,
               retry_delay_s: float = DEFAULT_RETRY_DELAY_S, max_steps: int | None = None) -> dict:
    payload = {"tick_every_s": float(tick_every_s), "max_attempts": int(max_attempts),
               "retry_delay_s": float(retry_delay_s)}
    if max_steps is not None:
        # Les parcours web multi-étapes demandent plus d'actions qu'une acquisition documentaire.
        payload["max_steps"] = int(max_steps)
    return payload


# --- exécution du travail -------------------------------------------------------------------------

def execute_objective_work(ctx) -> dict:
    """Travail durable d'un objectif, exécuté par le runtime de mission existant.

    L'étape coûteuse (la mission) est mémoïsée : une reprise après réponse humaine, nouvelle
    tentative ou redémarrage ne la rejoue pas.
    """
    objective_id = int(ctx.input["objective_id"])
    objective = strategy.get("objective", objective_id, ctx.business)
    if objective is None:
        raise SupervisorError(f"objectif #{objective_id} introuvable pour le business {ctx.business!r}")
    criterion = criterion_for(objective, ctx.input.get("success_criterion"))
    result = ctx.memo("mission", lambda: _run_mission(ctx, objective, criterion))
    return work_output(ctx.business, objective, criterion, result, task_id=ctx.id)


def _run_mission(ctx, objective: dict, criterion: dict | None) -> dict:
    """Délègue au runtime de mission existant, via le même pont que les handlers de mission.

    `task_handlers._run` initialise l'état partagé des agents et propage l'annulation de la tâche
    durable au runtime : sans lui, la mission échoue sur un état non initialisé.
    """
    from agents import task_handlers
    from agents.runtime import run_mission
    return task_handlers._run(ctx, lambda: run_mission(
        goal_text(objective),
        max_steps_per_agent=int(ctx.input.get("max_steps", DEFAULT_MAX_STEPS)),
        business=ctx.business,
        allowed_tools=set(ctx.input["allowed_tools"]) if ctx.input.get("allowed_tools") else None,
        profile=ctx.input.get("profile"),
        business_signal_focus=bool(ctx.input.get("business_signal_focus", False)),
        max_duration_s=float(ctx.input.get("max_duration_s", DEFAULT_MAX_DURATION_S)),
    ))


def browser_result(business: str, task_id: int | None, criterion: dict) -> dict:
    """Mesure sur le registre et le disque, pour CETTE tâche uniquement."""
    target = int(criterion["gte"])
    if criterion["metric"] == "kept_browser_files":
        from .browser_workspace import kept_files
        kept = kept_files(business, task_id) if task_id is not None else []
        return {"metric": "kept_browser_files", "observed": len(kept), "target": target,
                "success": len(kept) >= target, "files": [{k: f[k] for k in ("file", "sha256", "bytes")} for f in kept],
                "scope": "task_steps+disque", "note": "fichier présent dans l'espace de la tâche, sha256 identique"}
    from .browser_workspace import task_actions
    rows = task_actions(business, task_id) if task_id is not None else []
    verified = [r for r in rows if r["status"] == "verified"]
    return {"metric": "verified_browser_actions", "observed": len(verified), "target": target,
            "success": len(verified) >= target, "verified_action_ids": [r["id"] for r in verified],
            "scope": "channel_actions", "note": "effet constaté sur la page réelle (texte absent avant, présent après)"}


def video_result(business: str, task_id: int | None, criterion: dict) -> dict:
    """Mesure sur le registre Agnes et le disque, pour CETTE tâche ou ce business.

    - kept_video_files: vidéos Agnes vérifiées physiquement et présentes dans l'espace tâche
    - verified_video_count: nombre de générations Agnes vérifiées (MP4 + SHA-256) pour la tâche ou business
    """
    target = int(criterion["gte"])
    from pathlib import Path
    from . import agnes_production
    from .agnes import verify_mp4

    agnes_production.ensure_schema()
    sql = ("SELECT g.*, e.id AS proof_id FROM agnes_video_generations g "
           "LEFT JOIN strategy_evidence e ON e.id=g.evidence_id AND e.business=g.business "
           "AND e.status='active' AND e.nature='observed' AND e.source_type='file' "
           "AND e.source_ref=g.output_path "
           "WHERE g.business=? AND g.status='done'")
    params: tuple = (business,)
    if task_id is not None:
        sql += " AND g.task_id=?"
        params += (task_id,)
    rows = journal.query(sql + " ORDER BY g.id DESC LIMIT 50", params)
    kept = []
    for row in rows:
        if not row["output_path"] or not row["sha256"] or not row["proof_id"]:
            continue
        ver = verify_mp4(Path(row["output_path"]))
        if ver.get("verified") and ver.get("sha256") == row["sha256"]:
            kept.append({"file": row["output_path"], "sha256": ver["sha256"],
                         "bytes": ver["bytes"], "evidence_id": row["proof_id"]})
    return {"metric": criterion["metric"], "observed": len(kept), "target": target,
            "success": len(kept) >= target, "files": kept,
            "scope": "agnes_video_generations+strategy_evidence+disque",
            "note": "preuve observée persistée, vidéo décodable et SHA-256 identique"}


def measure(business: str, task_id: int | None, criterion: dict, results: list) -> dict:
    """Critère simple : sa mesure. Critère cumulatif : succès seulement si CHAQUE partie est
    satisfaite ; `observed` compte alors les parties satisfaites (cible = nombre de parties)."""
    from agents import task_handlers

    def one(part: dict) -> dict:
        if part["metric"] in BROWSER_METRICS:
            return browser_result(business, task_id, part)
        if part["metric"] in VIDEO_METRICS:
            return video_result(business, task_id, part)
        return task_handlers._mission_objective_result(results, part)

    if "all" not in criterion:
        return one(criterion)
    parts = [one(p) for p in criterion["all"]]
    satisfied = sum(1 for p in parts if p.get("success"))
    return {"metric": "all", "observed": satisfied, "target": len(parts), "success": satisfied == len(parts),
            "parts": parts, "note": "tous les critères sont exigés"}


def work_output(business: str, objective: dict, criterion: dict | None, result: dict, *,
                task_id: int | None = None) -> dict:
    """Résultat mesurable d'un travail : ce qui est constaté, jamais ce qui est espéré."""
    from agents import task_handlers

    from .browser_workspace import task_actions
    results = result.get("results") or []
    objective_result = measure(business, task_id, criterion, results) if criterion else None
    execution_status = str(result.get("execution_status") or "unknown")
    ambiguous_browser = [r["id"] for r in task_actions(business, task_id) if r["status"] in ("ambiguous", "proposed")] \
        if task_id is not None else []
    # Agnes ambiguous actions (channel_actions with kind agnes_video)
    ambiguous_agnes = []
    if task_id is not None:
        try:
            rows = journal.query(
                "SELECT id FROM channel_actions WHERE business=? AND kind='agnes_video' AND status IN ('ambiguous','proposed') "
                "AND idempotency_key LIKE ?",
                (business, f"%{task_id}%"),
            )
            ambiguous_agnes = [r["id"] for r in rows]
            # Also check agnes_video_generations that are ambiguous due to submission
            if not ambiguous_agnes:
                rows2 = journal.query(
                    "SELECT id FROM channel_actions WHERE business=? AND kind='agnes_video' AND status='ambiguous' ORDER BY id DESC LIMIT 5",
                    (business,),
                )
                ambiguous_agnes = [r["id"] for r in rows2]
        except Exception:
            ambiguous_agnes = []

    boundary = HUMAN_BOUNDARY_STATUSES.get(execution_status)
    if ambiguous_browser and not (objective_result or {}).get("success"):
        boundary = (f"action(s) navigateur au résultat inconnu {', '.join(f'#{i}' for i in ambiguous_browser)} : vérifier "
                    f"sur le site puis `python -m octopus browser resolve {business} <id> executed|not_executed`")
    if ambiguous_agnes and not (objective_result or {}).get("success"):
        # Do not blindly retry Agnes generation if outcome uncertain
        ag_msg = (f"action(s) Agnes au résultat incertain {', '.join(f'#{i}' for i in ambiguous_agnes)} : "
                  f"reconcile avec le service Agnes existant (GET /api/tasks) ou solliciter l'humain, "
                  f"aucune nouvelle génération ne doit être déclenchée aveuglément")
        boundary = f"{boundary} ; {ag_msg}" if boundary else ag_msg

    return {
        "objective_id": int(objective["id"]),
        "business": business,
        "execution_status": execution_status,
        "synthesis_status": result.get("synthesis_status"),
        "criterion": criterion,
        "objective_result": objective_result,
        "observed": None if objective_result is None else objective_result["observed"],
        "success": None if objective_result is None else bool(objective_result["success"]),
        "citations": task_handlers._usable_browse_urls(results),
        "business_signals": len(result.get("business_signals") or []),
        "human_boundary": boundary,
        "browser_ambiguous_actions": ambiguous_browser,
        "agnes_ambiguous_actions": ambiguous_agnes,
        "measured": objective_result is not None,
    }


# --- évaluation et décision -----------------------------------------------------------------------

def decide(business: str, objective: dict, work: dict, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS,
           retry_delay_s: float = DEFAULT_RETRY_DELAY_S, parent_id: int | None = None,
           max_steps: int = DEFAULT_MAX_STEPS) -> dict:
    """Décision déterministe à partir du résultat persisté. Renvoie la décision et son effet."""
    output = work.get("output") or {}
    if work.get("kind") == "agnes.generate_video":
        criterion = criterion_for(objective)
        measured = video_result(business, int(work["id"]), criterion)
        output = {"success": work["status"] == "done" and measured["success"],
                  "measured": True, "observed": measured["observed"],
                  "criterion": criterion, "objective_result": measured,
                  "execution_status": work["status"]}
        if not output["success"]:
            output["human_boundary"] = (f"Génération Agnes #{work['id']} {work['status']} sans MP4 "
                                        "vérifié et preuve persistée ; intervention humaine requise")
    attempts = len(work_tasks(business, int(objective["id"])))
    if output.get("success") is True:
        outcome = "satisfied"
    elif work.get("kind") == "agnes.generate_video":
        outcome = "exhausted"
    elif output.get("human_boundary"):
        outcome = "human_boundary"
    elif attempts >= max(1, int(max_attempts)):
        outcome = "exhausted"
    else:
        outcome = "retry"
    decision = {
        "outcome": outcome,
        "objective_id": int(objective["id"]),
        "work_task_id": int(work["id"]),
        "attempts": attempts,
        "measured": bool(output.get("measured")),
        "observed": output.get("observed"),
        "criterion": output.get("criterion"),
        "execution_status": output.get("execution_status"),
        "reason": _reason(outcome, output, attempts, max_attempts),
    }
    # `_persist` renseigne `next_work_task_id` quand la décision crée la tâche suivante.
    decision["next_work_task_id"] = None
    _persist(business, objective, work, decision, retry_delay_s=retry_delay_s, parent_id=parent_id,
             max_steps=max_steps)
    return decision


def _reason(outcome: str, output: dict, attempts: int, max_attempts: int) -> str:
    if outcome == "satisfied":
        result = output.get("objective_result") or {}
        parts = result.get("parts") or [result]
        return "critère mesuré " + " ET ".join(f"{p.get('metric')} : {p.get('observed')} >= {p.get('target')}"
                                               for p in parts) + " (tâche de travail)"
    if outcome == "human_boundary":
        return str(output.get("human_boundary"))
    if outcome == "exhausted":
        if output.get("human_boundary"):
            return str(output["human_boundary"])
        return (f"{attempts}/{max_attempts} tentatives sans mesure concluante "
                f"(exécution : {output.get('execution_status')}) ; réactivation humaine requise")
    return f"aucune mesure concluante (exécution : {output.get('execution_status')}) ; nouvelle tentative"


def _persist(business: str, objective: dict, work: dict, decision: dict, *, retry_delay_s: float,
             parent_id: int | None, max_steps: int = DEFAULT_MAX_STEPS) -> None:
    """Écrit la décision, la preuve calculée et l'effet (tâche suivante ou transition d'objectif)."""
    objective_id, work_id, outcome = decision["objective_id"], decision["work_task_id"], decision["outcome"]
    resulting_action = {
        "satisfied": f"objectif #{objective_id} clos (achieved)",
        "human_boundary": f"demande humaine {boundary_key(objective_id)}",
        "exhausted": f"objectif #{objective_id} suspendu (paused)",
        "retry": "prochaine tâche supervisor.objective_work différée",
    }[outcome]
    decision_id = strategy.create(
        "decision", business, f"Supervision objectif #{objective_id} : {outcome}", created_by=ACTOR,
        decision=outcome, rationale=decision["reason"][:900], resulting_action=resulting_action)
    strategy.transition("decision", decision_id, business, "approved", actor=ACTOR)
    strategy.link(business, "decision", decision_id, "objective", objective_id, "supervises")
    strategy.link(business, "objective", objective_id, "task", work_id, "evaluated")
    if outcome == "human_boundary":
        # Marque durable : la frontière survit à un redémarrage du tick tant qu'aucun travail
        # plus récent n'existe pas.
        strategy.link(business, "objective", objective_id, "task", work_id, "awaiting_human")
    if decision["measured"]:
        evidence_id = strategy.create(
            "evidence", business, f"Travail #{work_id} : {decision['observed']} sur {decision['criterion']}",
            created_by=ACTOR, nature="computed", source_type="journal", source_ref=f"tasks#{work_id}",
            observation=decision["reason"][:900], metric="objective_work_observed",
            value=float(decision["observed"] or 0), unit="count",
            confidence="high" if outcome == "satisfied" else "medium")
        strategy.link(business, "evidence", evidence_id, "objective", objective_id, "measures")
    if outcome == "satisfied":
        strategy.transition("objective", objective_id, business, "achieved", actor=ACTOR,
                            note=f"tâche #{work_id}")
    elif outcome == "exhausted":
        strategy.transition("objective", objective_id, business, "paused", actor=ACTOR,
                            note=f"{decision['attempts']} tentatives sans mesure")
    elif outcome == "retry":
        next_id = plan_work(business, objective, criterion=decision["criterion"], delay_s=retry_delay_s,
                            parent_id=parent_id, max_steps=max_steps)
        decision["next_work_task_id"] = next_id


# --- tick du superviseur --------------------------------------------------------------------------

def _last_supervision(business: str, objective_id: int) -> tuple[str | None, str | None]:
    rows = journal.query(
        "SELECT d.decision, d.rationale FROM strategy_decisions d JOIN strategy_links l "
        "ON l.from_type='decision' AND l.from_id=d.id WHERE l.business=? AND l.to_type='objective' "
        "AND l.to_id=? AND l.relation='supervises' ORDER BY d.id DESC LIMIT 1", (business, objective_id))
    return (rows[0]["decision"], rows[0]["rationale"]) if rows else (None, None)


def boundary_question(objective: dict, reason: str | None) -> str:
    return (f"Objectif #{objective['id']} ({objective['summary']}) : {reason or 'ressource ou autorisation manquante'} "
            "Ressource ou autorisation à fournir pour continuer (répondre avec « octopus answer »).")


def supervise_objective(business: str, objective: dict, *, max_attempts: int, retry_delay_s: float,
                        parent_id: int | None = None, max_steps: int = DEFAULT_MAX_STEPS) -> dict:
    """Une passe de supervision pour un objectif : lecture d'état, puis création, évaluation ou attente."""
    objective_id = int(objective["id"])
    rows = work_tasks(business, objective_id)
    entry = {"business": business, "objective_id": objective_id, "summary": objective["summary"],
             "status": objective["status"], "action": None, "decision": None, "work_task_id": None,
             "human_question": None}
    open_rows = [row for row in rows if row["status"] in tasks.ACTIVE]
    if open_rows:
        entry.update(action="in_flight", work_task_id=int(open_rows[-1]["id"]))
        return entry
    done = [row for row in rows if row["status"] not in tasks.ACTIVE]
    if not done:
        entry.update(action="work_created",
                     work_task_id=plan_work(business, objective, parent_id=parent_id, max_steps=max_steps))
        return entry
    work = done[-1]
    entry["work_task_id"] = int(work["id"])
    if not evaluated(business, objective_id, int(work["id"])):
        entry.update(action="evaluated",
                     decision=decide(business, objective, work, max_attempts=max_attempts,
                                     retry_delay_s=retry_delay_s, parent_id=parent_id, max_steps=max_steps))
    elif awaiting_human(business, objective_id, int(work["id"])):
        # Frontière humaine décidée avant un redémarrage : elle reste ouverte jusqu'à réponse.
        outcome, rationale = _last_supervision(business, objective_id)
        entry.update(action="awaiting_human",
                     decision={"outcome": outcome or "human_boundary", "objective_id": objective_id,
                               "work_task_id": int(work["id"]), "reason": rationale})
    else:
        entry.update(action="already_evaluated")
    if (entry.get("decision") or {}).get("outcome") == "human_boundary":
        entry["human_question"] = boundary_question(objective, (entry["decision"] or {}).get("reason"))
    return entry


def tick(*, ctx=None, businesses: list[str] | None = None, max_attempts: int = DEFAULT_MAX_ATTEMPTS,
         retry_delay_s: float = DEFAULT_RETRY_DELAY_S, tick_every_s: float = DEFAULT_TICK_EVERY_S,
         limit: int = 20) -> dict:
    """Une passe complète : tous les objectifs actifs, puis réarmement du tick suivant.

    `ctx` (TaskContext) donne la mémoïsation, l'annulation et les demandes humaines. Sans `ctx`
    (appel direct, diagnostic) les frontières humaines sont seulement rapportées.
    """
    if ctx is not None:
        tick_every_s = float((ctx.input or {}).get("tick_every_s", tick_every_s))
        max_attempts = int((ctx.input or {}).get("max_attempts", max_attempts))
        retry_delay_s = float((ctx.input or {}).get("retry_delay_s", retry_delay_s))
    max_steps = int((ctx.input or {}).get("max_steps", DEFAULT_MAX_STEPS)) if ctx is not None else DEFAULT_MAX_STEPS
    parent_id = ctx.id if ctx is not None else None
    report: dict = {"objectives": [], "human_boundaries": [], "next_tick": None, "checked": 0}
    for business in list(businesses) if businesses else businesses_with_active_objectives():
        for objective in active_objectives(business)[:limit]:
            report["checked"] += 1
            entry = supervise_objective(business, objective, max_attempts=max_attempts,
                                        retry_delay_s=retry_delay_s, parent_id=parent_id, max_steps=max_steps)
            if entry.get("human_question"):
                report["human_boundaries"].append(entry)
            report["objectives"].append(entry)
    # Le tick se réarme avant toute attente : la chaîne survit à une demande humaine ouverte.
    if ctx is not None:
        report["next_tick"] = _rearm(ctx, tick_every_s)
    for entry in report["human_boundaries"]:
        key = boundary_key(entry["objective_id"])
        answer = tasks.answer_for(ctx.id, key) if ctx is not None else None
        if answer is None and pending_boundary(entry["business"], entry["objective_id"]):
            entry["action"] = "waiting_human"
            continue
        if ctx is None:
            continue
        if answer is None:
            answer = ctx.ask_human(key, entry["human_question"],
                                   context={"objective_id": entry["objective_id"],
                                            "work_task_id": entry["work_task_id"]})
        _after_human_answer(entry, answer, parent_id=parent_id, retry_delay_s=retry_delay_s, max_steps=max_steps)
        break  # une seule frontière humaine traitée par passe : la suivante le sera au tick suivant
    return report


def _rearm(ctx, tick_every_s: float) -> int | None:
    """Réarme le tick suivant. Idempotent : jamais deux ticks ouverts pour le même business."""
    payload = dict(ctx.input or {})
    payload.setdefault("tick_every_s", tick_every_s)
    open_tick = next((task for task in tasks.list_tasks(business=ctx.business, limit=200)
                      if task["kind"] == TICK_KIND and task["status"] in tasks.ACTIVE
                      and task["id"] != ctx.id), None)
    if open_tick is not None:
        return int(open_tick["id"])
    return tasks.enqueue(ctx.business, TICK_KIND, payload, delay_s=max(0.0, float(tick_every_s)),
                         parent_id=ctx.id)


def _after_human_answer(entry: dict, answer: str, *, parent_id: int | None, retry_delay_s: float,
                        max_steps: int = DEFAULT_MAX_STEPS) -> None:
    """La réponse humaine est l'autorisation : elle est enregistrée puis le travail reprend."""
    business, objective_id = entry["business"], entry["objective_id"]
    decision_id = strategy.create(
        "decision", business, f"Réponse humaine objectif #{objective_id}", created_by="human",
        decision="human_answer", rationale=str(answer)[:900], resulting_action="reprise du travail supervisé")
    strategy.transition("decision", decision_id, business, "approved", actor="human")
    strategy.link(business, "decision", decision_id, "objective", objective_id, "authorizes")
    objective = strategy.get("objective", objective_id, business)
    if objective is None or objective["status"] != "active":
        entry["action"] = "human_answered_objective_closed"
        return
    entry["work_task_id"] = plan_work(business, objective, criterion=parse_criterion(objective.get("success_criteria")),
                                      delay_s=0.0, parent_id=parent_id, max_steps=max_steps)
    for request in tasks.pending_human_requests(business):
        if request["key"] == boundary_key(objective_id):
            tasks.answer(request["id"], answer)
    entry["action"] = "work_created_after_human_answer"
    entry["human_answer"] = str(answer)[:500]
