"""B-roll par offre : chaine de fournisseurs, licences, et repli sur les images du depot."""
from __future__ import annotations

import json

import pytest

from tools import fetch_broll


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("PEXELS_API_KEY", "PIXABAY_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_openverse_prefers_free_licenses(monkeypatch):
    payload = {"results": [
        {"url": "https://x/by.jpg", "license": "by", "license_version": "4.0", "attribution": "photo de A",
         "title": "signing contract", "foreign_landing_url": "https://page/by"},
        {"url": "https://x/cc0.jpg", "license": "cc0", "license_version": "1.0", "attribution": "photo de B",
         "title": "contract signature", "foreign_landing_url": "https://page/cc0"},
        {"url": "https://x/hors-sujet.jpg", "license": "cc0", "license_version": "1.0", "title": "buste romain",
         "foreign_landing_url": "https://page/hs"},
    ]}
    monkeypatch.setattr(fetch_broll, "_get", lambda url, headers=None, timeout=20: json.dumps(payload).encode())
    found = fetch_broll.openverse("signing contract")
    assert [f["url"] for f in found] == ["https://x/cc0.jpg", "https://x/by.jpg"]  # CC0 d'abord, hors-sujet ecarte
    assert found[0]["licence"] == "cc0 1.0" and found[0]["provider"] == "openverse"


def test_irrelevant_results_are_dropped():
    """Le moteur elargit la recherche : un resultat sans aucun mot de la requete est ecarte."""
    assert fetch_broll._relevant({"title": "Signing contract", "tags": []}, "signing contract")
    assert fetch_broll._relevant({"title": "x", "tags": [{"name": "office"}]}, "office stress")
    assert not fetch_broll._relevant({"title": "Buste romain", "tags": None}, "worried man")


def test_pexels_used_first_when_key_present(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "cle")
    payload = {"photos": [{"src": {"large2x": "https://p/1.jpg"}, "photographer": "Ana",
                           "url": "https://pexels.com/photo/1"}]}
    monkeypatch.setattr(fetch_broll, "_get", lambda url, headers=None, timeout=20: json.dumps(payload).encode())
    monkeypatch.setattr(fetch_broll, "openverse", lambda q, k="", page=1: pytest.fail("openverse ne doit pas etre appele"))
    found = fetch_broll.fetch_one("contrat")
    assert found["provider"] == "pexels" and "Ana" in found["credit"]


def test_page_varies_by_offer():
    """Deux offres ne doivent pas recevoir la meme image pour le meme role."""
    pages = {1 + fetch_broll.zlib.crc32(o.encode()) % 4 for o in
             ("cash_avenant_scope01", "cash_impayes_relance01", "cash_devis_cgv01")}
    assert len(pages) > 1


def test_provider_failure_falls_through(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "cle")
    monkeypatch.setattr(fetch_broll, "pexels", lambda q, k, page=1: (_ for _ in ()).throw(OSError("reseau")))
    monkeypatch.setattr(fetch_broll, "openverse", lambda q, k="", page=1: [{"provider": "openverse", "url": "u",
                                                                           "credit": "c", "source": "s",
                                                                           "licence": "cc0 1.0"}])
    assert fetch_broll.fetch_one("contrat")["provider"] == "openverse"


def test_main_writes_images_credits_and_job(tmp_path, monkeypatch):
    job_path = tmp_path / "offre.json"
    job_path.write_text(json.dumps({"keywords": ["avenant", "scope"], "visuel": {"hook": {"label": "H"}}}),
                        encoding="utf-8")
    monkeypatch.setattr(fetch_broll, "ROOT", tmp_path)
    monkeypatch.setattr(fetch_broll, "fetch_candidates", lambda query, page=1, wanted=2: [
        {"url": "https://x.jpg", "credit": "photo de A", "source": "https://page", "licence": "cc0 1.0",
         "provider": "openverse"},
        {"url": "https://x2.jpg", "credit": "photo de B", "source": "https://page2", "licence": "cc0 1.0",
         "provider": "openverse"}] if "contract" in query or "signing" in query else [])
    monkeypatch.setattr(fetch_broll, "_get", lambda url, headers=None, timeout=20: b"JPEGDATA")

    def fake_ffmpeg(cmd, capture_output=True, text=True):
        from pathlib import Path as P
        P(cmd[-1]).write_bytes(b"jpeg")
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(fetch_broll.subprocess, "run", fake_ffmpeg)
    monkeypatch.setattr(fetch_broll.sys, "argv", ["fetch_broll.py", str(job_path), "offre01"])
    assert fetch_broll.main() == 0

    job = json.loads(job_path.read_text(encoding="utf-8"))
    assert job["visuel"]["preuve"]["img"] == "offre01/preuve.jpg"  # seul le role "signing contract" a des images
    assert job["visuel"]["preuve"]["img2"] == "offre01/preuve_b.jpg"  # deux plans par segment
    assert "img" not in job["visuel"]["hook"] and job["visuel"]["hook"]["label"] == "H"
    assert (tmp_path / "remotion" / "public" / "img" / "offre01" / "preuve.jpg").exists()
    credits = json.loads((tmp_path / "out" / "offre01" / "credits.json").read_text(encoding="utf-8"))
    assert len(credits) == 2 and credits[0]["licence"] == "cc0 1.0"
    assert "https://page" in (tmp_path / "out" / "offre01" / "credits.txt").read_text(encoding="utf-8")


def test_main_falls_back_to_photo_when_all_clip_downloads_fail(tmp_path, monkeypatch):
    job_path = tmp_path / "offre.json"
    job_path.write_text(json.dumps({"visuel": {}}), encoding="utf-8")
    monkeypatch.setattr(fetch_broll, "ROOT", tmp_path)
    monkeypatch.setattr(fetch_broll, "ROLE_QUERIES", {"hook": ["working computer"]})
    monkeypatch.setattr(fetch_broll, "fetch_clips", lambda *args, **kwargs: [
        {"url": "https://clip.mp4", "credit": "video de A", "source": "https://clip",
         "licence": "Pexels License", "provider": "pexels-video"},
    ])
    monkeypatch.setattr(fetch_broll, "_download_clip", lambda *args, **kwargs: None)
    monkeypatch.setattr(fetch_broll, "fetch_candidates", lambda *args, **kwargs: [
        {"url": "https://photo.jpg", "credit": "photo de B", "source": "https://photo",
         "licence": "Pexels License", "provider": "pexels"},
    ])

    def save_photo(url, target):
        target.write_bytes(b"jpeg")
        return target

    monkeypatch.setattr(fetch_broll, "_download_crop", save_photo)
    monkeypatch.setattr(fetch_broll.sys, "argv", ["fetch_broll.py", str(job_path), "offre01"])

    assert fetch_broll.main() == 0
    job = json.loads(job_path.read_text(encoding="utf-8"))
    assert job["visuel"]["hook"]["img"] == "offre01/hook.jpg"
    credits = json.loads((tmp_path / "out" / "offre01" / "credits.json").read_text(encoding="utf-8"))
    assert [credit["provider"] for credit in credits] == ["pexels"]
