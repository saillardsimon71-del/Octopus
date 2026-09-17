"""Tests de migration sans appel réseau ni rendu vidéo réel."""
from __future__ import annotations

import pytest

from octopus.video.contract import VideoResult, VideoStatus
from octopus.video.service import VideoService, VideoServiceError, stable_job_id


JOB = {
    "offer_id": "cash_devis_cgv01",
    "langue": "fr",
    "duree_cible_s": 24,
    "titre": "Titre",
    "hook": "Hook",
    "douleur": "Douleur",
    "preuve": "Preuve",
    "soulagement": "Soulagement",
    "cta": "37 € — lien en description",
    "prix": "37 €",
    "stripe_link": "https://example.invalid/stripe",
    "sub_id": "sub-1",
    "voix": {"moteur": "chatterbox", "nom": "vivienne-fr"},
    "keywords": ["devis", "CGV"],
    "palette": {"bgTop": "#fff"},
    "visuel": {"hook": {"label": "DEVIS"}},
    "narration": [
        {"id": "hook", "role": "hook", "rate": "-8%", "texte": "Hook"},
    ],
}


def test_stable_job_id_is_order_independent():
    a = dict(JOB)
    b = dict(reversed(list(JOB.items())))
    assert stable_job_id(a) == stable_job_id(b)


def test_local_service_keeps_callback_boundary():
    seen = []

    def local(offer_id, job):
        seen.append((offer_id, job))
        return {"duration_s": 24}

    result = VideoService(mode="local", local_renderer=local).render(JOB["offer_id"], JOB)
    assert result == {"duration_s": 24}
    assert seen == [(JOB["offer_id"], JOB)]


def test_cloud_service_returns_qc_from_video_result():
    class FakeRenderer:
        def render(self, video_job):
            return VideoResult(video_job.job_id, VideoStatus.COMPLETED, "https://example/video.mp4",
                               qc={"duration_s": 24, "resolution": "1080x1920"})

    result = VideoService(mode="cloud", cloud_renderer=FakeRenderer()).render(JOB["offer_id"], JOB)
    assert result["resolution"] == "1080x1920"


def test_cloud_service_forwards_contract_identity():
    seen = []

    class FakeRenderer:
        def render(self, video_job):
            seen.append(video_job)
            return VideoResult(video_job.job_id, VideoStatus.COMPLETED, "https://example/video.mp4",
                               qc={"ok": True})

    VideoService(mode="cloud", cloud_renderer=FakeRenderer()).render(JOB["offer_id"], JOB)
    assert seen[0].offer_id == JOB["offer_id"]
    assert seen[0].voice["nom"] == "vivienne-fr"
    assert seen[0].script["stripe_link"] == JOB["stripe_link"]


def test_cloud_service_rejects_mismatched_offer_before_renderer():
    class ExplodingRenderer:
        def render(self, video_job):
            raise AssertionError("renderer ne doit jamais être appelé")

    with pytest.raises(VideoServiceError, match="incohérence offer_id"):
        VideoService(mode="cloud", cloud_renderer=ExplodingRenderer()).render("other-offer", JOB)
