"""Sources externes : indisponibles tant qu'elles ne sont pas branchées, jamais simulées."""
from __future__ import annotations

import pytest

from agents.gui.strategy import overview_lines
from octopus import connectors, strategy
from octopus.connectors import SourceStatus


@pytest.fixture(autouse=True)
def no_probes(monkeypatch):
    monkeypatch.setattr(connectors, "_PROBES", {})


def test_every_domain_is_unavailable_by_default():
    states = connectors.status("atelier_test")
    assert [s.domain for s in states] == list(connectors.DOMAINS)
    assert all(not s.available and s.reason == "non configuré" for s in states)
    assert all(s.period_start is None and s.provenance is None for s in states)


def test_registered_probe_reports_period_and_provenance_and_failures_stay_unavailable():
    connectors.register("finance", lambda b: SourceStatus("finance", True, "ok", provider="export-banque",
                                                          period_start=1.0, period_end=2.0, provenance="fichier.csv"))
    connectors.register("crm", lambda b: (_ for _ in ()).throw(TimeoutError("lent")))
    connectors.register("social", lambda b: SourceStatus("finance", True, "mauvais domaine"))
    states = {s.domain: s for s in connectors.status("atelier_test")}
    assert states["finance"].available and states["finance"].provenance == "fichier.csv"
    assert not states["crm"].available and "TimeoutError" in states["crm"].reason
    assert not states["social"].available and "incohérente" in states["social"].reason
    line = connectors.summary_line("atelier_test")
    assert "connectées : revenus, coûts et marges (export-banque)" in line and "clients et prospects" in line
    with pytest.raises(ValueError):
        connectors.register("ads", lambda b: None)


def test_missing_sources_are_visible_in_reviews_and_gui_without_numbers():
    snapshot = strategy.review_snapshot("atelier_test")
    lines = overview_lines(strategy.overview("atelier_test"))
    for text in (snapshot["text"], "\n".join(lines)):
        tail = text.split("Données externes")[1]
        assert "non connectées, non évaluées" in tail and not any(ch.isdigit() for ch in tail)
