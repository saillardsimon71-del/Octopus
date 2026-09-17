"""Calage des sous-titres : ancrage sur les mots reconnus, interpolation des autres, repli sur."""
from __future__ import annotations

import pytest

from tools import word_sync


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("PODALUX_WORD_SYNC", "0")
    assert not word_sync.enabled()


def test_recognised_words_take_the_measured_timing():
    toks = "Votre facture est impayee".split()
    heard = [("Votre", 0.0, 0.4), ("facture", 0.4, 1.0), ("est", 1.0, 1.2), ("impayee", 1.2, 1.8)]
    spans = word_sync.align(toks, heard, 10.0, 11.8)
    assert spans[0][0] == pytest.approx(10.0, abs=0.01)
    assert spans[1][0] == pytest.approx(10.4, abs=0.02)   # mesure, pas prorata
    assert spans[-1][1] == pytest.approx(11.8, abs=0.01)
    assert all(s < e for s, e in spans)


def test_unrecognised_word_is_interpolated_between_anchors():
    toks = "Votre facture est impayee".split()
    heard = [("Votre", 0.0, 0.4), ("facture", 0.4, 1.0), ("est", 1.0, 1.2), ("impie", 1.2, 1.8)]
    spans = word_sync.align(toks, heard, 0.0, 1.8)
    assert spans[3][0] == pytest.approx(1.2, abs=0.05)  # cale apres "est", jusqu'a la fin du segment
    assert spans[3][1] == pytest.approx(1.8, abs=0.01)


def test_accents_and_punctuation_do_not_block_the_match():
    spans = word_sync.align(["Impayée,", "vraiment"], [("impayee", 0.0, 0.5), ("Vraiment.", 0.5, 1.0)], 0.0, 1.0)
    assert spans[0][1] == pytest.approx(0.5, abs=0.02)


def test_falls_back_to_proportional_without_transcription():
    toks = "un deux trois".split()
    assert word_sync.align(toks, [], 0.0, 3.0) == word_sync._proportional(toks, 0.0, 3.0)


def test_timings_stay_inside_the_segment_window():
    heard = [("hors", 5.0, 9.0), ("cadre", 9.0, 12.0)]  # transcription decalee : tout doit etre ramene
    spans = word_sync.align(["hors", "cadre"], heard, 30.0, 31.0)
    assert all(30.0 <= s <= 31.0 and 30.0 <= e <= 31.0 for s, e in spans)
