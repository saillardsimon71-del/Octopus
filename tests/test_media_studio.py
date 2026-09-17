"""Studio vidéo : formulaire, réutilisation, historique (logique sans interface)."""
from __future__ import annotations

import pytest

from octopus import tasks
from octopus.media import library, studio


def test_form_to_input_validates_and_maps():
    inp = studio.form_to_input({"prompt": "  Un renard court dans la neige  ", "model_type": "minimax_h3_fl2va",
                                "resolution": "Short 9:16 (480x832)", "duration_s": "5,5", "steps": "8",
                                "seed": "", "variants": "3", "allow_with_webui": True})
    assert inp == {"prompt": "Un renard court dans la neige", "model_type": "minimax_h3_fl2va", "business": "studio",
                   "duration_s": 5.5, "variants": 3, "settings": {"num_inference_steps": 8}, "resolution": "480x832",
                   "allow_with_webui": True}
    assert "resolution" not in studio.form_to_input({"prompt": "abc", "resolution": "Défaut du modèle"})


@pytest.mark.parametrize("form, message", [
    ({"prompt": "a"}, "prompt trop court"),
    ({"prompt": "abcd", "duration_s": "cinq"}, "durée invalide"),
    ({"prompt": "abcd", "variants": "12"}, "variantes hors limites"),
    ({"prompt": "abcd", "resolution": "énorme"}, "résolution invalide"),
])
def test_form_errors(form, message):
    with pytest.raises(studio.FormError, match=message):
        studio.form_to_input(form)


def test_submit_reuse_history_and_cancel():
    task_id = studio.submit({"prompt": "Un phare sous l'orage", "resolution": "Paysage (832x480)", "duration_s": "4"})
    assert tasks.get(task_id)["resource"] == "gpu"
    assert [t["id"] for t in studio.pending_without_generation()] == [task_id]
    gen_id = library.create("studio", "wangp", "minimax_h3_fl2va", "Un phare sous l'orage",
                            {"resolution": "832x480", "video_length": "4s", "num_inference_steps": 8, "seed": 3},
                            task_id=task_id)
    assert studio.pending_without_generation() == []
    form = studio.form_from_generation(library.get(gen_id))
    assert form == {"prompt": "Un phare sous l'orage", "model_type": "minimax_h3_fl2va", "resolution": "Paysage (832x480)",
                    "steps": "8", "seed": "", "variants": "1", "duration_s": "4", "parent_id": gen_id}
    child_task = studio.submit(form)
    assert tasks.get(child_task)["input"]["parent_id"] == gen_id
    row = studio.history()[0]
    assert row["id"] == gen_id and row["task_status"] == "queued"
    assert studio.cancel_generation(row) == "cancelled"


def test_model_choices_without_probe_and_hardware_summary():
    choices = studio.model_choices()
    assert choices and all("diagnostic à lancer" in label for label, _ in choices)
    assert studio.hardware_summary() == "Diagnostic WanGP jamais lancé"
