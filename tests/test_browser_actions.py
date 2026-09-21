from __future__ import annotations

import json

import pytest

from agents import browser, web_guard
from octopus import actions, browser_actions, economy, strategy
from octopus.strategy import StrategyError

B = "phase2_test"


class FakeBrowser:
    def __init__(self, *, final_url: str | None = None, proof: str = "Envoyé"):
        self.final_url = final_url
        self.proof = proof
        self._url = ""
        self.typed: list[tuple[str, str]] = []
        self.clicked: list[str] = []
        self.waited: list[str] = []
        self.stopped = False

    def goto(self, url: str):
        self._url = self.final_url or url
        return self._url

    def url(self):
        return self._url

    def type(self, selector: str, value: str):
        self.typed.append((selector, value))

    def click(self, selector: str):
        self.clicked.append(selector)

    def wait_for(self, text: str):
        self.waited.append(text)
        if text != self.proof:
            raise TimeoutError("confirmation absente")

    def stop(self):
        self.stopped = True


def channel(locator: str = "https://linkedin.com/messaging") -> dict:
    return {
        "locator": locator,
        "capabilities": json.dumps([browser_actions.CAPABILITY]),
    }


def safe_payload() -> dict:
    return {
        "url": "https://www.linkedin.com/messaging/thread/123",
        "fields": [{"selector": "textarea[name='message']", "value": "Bonjour"}],
        "submit_selector": "button[type='submit']",
        "success_text": "Envoyé",
        "metric": "messages_envoyes",
        "value": 1,
        "unit": "message",
    }


def patch_browser(monkeypatch, fake: FakeBrowser):
    created = {}

    monkeypatch.setattr(web_guard, "classify", lambda url: web_guard.ACCOUNT)

    def make(**kwargs):
        created.update(kwargs)
        return fake

    monkeypatch.setattr(browser, "new_browser", make)
    return created


def test_builtin_browser_form_executor_is_registered():
    assert ("browser_form", "submit") in actions.executors()


def test_submit_form_observes_proof_and_stays_on_declared_site(monkeypatch):
    fake = FakeBrowser(final_url="https://www.linkedin.com/messaging/thread/123")
    created = patch_browser(monkeypatch, fake)

    result = browser_actions.submit_form(channel(), safe_payload())

    assert created["account"] is True and created["headless"] is False
    assert fake.typed == [("textarea[name='message']", "Bonjour")]
    assert fake.clicked == ["button[type='submit']"]
    assert fake.waited == ["Envoyé"] and fake.stopped is True
    assert result == {
        "observation": "Formulaire soumis; confirmation observée: Envoyé",
        "source_ref": "https://www.linkedin.com/messaging/thread/123",
        "metric": "messages_envoyes",
        "value": 1,
        "unit": "message",
    }


@pytest.mark.parametrize(
    "chan,payload,message",
    [
        (
            channel("https://linkedin.com/"),
            {**safe_payload(), "url": "https://evil.example/form"},
            "domaine déclaré",
        ),
        (
            channel("https://stripe.com/dashboard"),
            {**safe_payload(), "url": "https://stripe.com/settings"},
            "financier ou de sécurité",
        ),
        (
            channel(),
            {
                **safe_payload(),
                "fields": [{"selector": "input[name='password']", "value": "ne-jamais-stocker"}],
            },
            "champ sensible",
        ),
        (
            channel(),
            {k: v for k, v in safe_payload().items() if k != "success_text"},
            "success_text requis",
        ),
    ],
)
def test_submit_form_fails_closed_before_browser(monkeypatch, chan, payload, message):
    called = []
    monkeypatch.setattr(browser, "new_browser", lambda **kwargs: called.append(kwargs))
    with pytest.raises(StrategyError, match=message):
        browser_actions.submit_form(chan, payload)
    assert called == []


def test_redirect_outside_declared_site_is_refused(monkeypatch):
    fake = FakeBrowser(final_url="https://evil.example/phish")
    patch_browser(monkeypatch, fake)

    with pytest.raises(StrategyError, match="redirection hors"):
        browser_actions.submit_form(channel(), safe_payload())

    assert fake.typed == [] and fake.clicked == [] and fake.stopped is True


def test_action_pipeline_records_observed_evidence(monkeypatch):
    fake = FakeBrowser(final_url="https://www.linkedin.com/messaging/thread/123")
    patch_browser(monkeypatch, fake)

    channel_id = economy.add_channel(
        B,
        "browser_form",
        "LinkedIn messages",
        created_by="human",
        locator="https://linkedin.com/messaging",
        capabilities=[browser_actions.CAPABILITY],
        nature="observed",
        source_ref="https://linkedin.com/messaging",
    )
    economy.update_channel(B, channel_id, actor="human", status="active", access="act")

    objective = strategy.create("objective", B, "Premier retour", created_by="human", statement="Obtenir une réponse")
    hypothesis = strategy.create(
        "hypothesis", B, "Message direct", created_by="human", parent_id=objective,
        statement="Un message ciblé obtient une réponse",
    )
    experiment = strategy.create(
        "experiment", B, "Envoyer un message", created_by="human", parent_id=hypothesis,
        action="Envoyer un message réel", metric="messages_envoyes", target_value=1,
        channel_id=channel_id,
    )
    strategy.transition("experiment", experiment, B, "running", actor="human")

    result = actions.propose(
        B,
        channel_id,
        "submit",
        safe_payload(),
        requested_by="agent:GROWTH",
        experiment_id=experiment,
        idempotency_key="phase2-message-1",
    )

    assert result["status"] == "executed"
    evidence = strategy.get("evidence", result["evidence_id"], B)
    assert evidence["nature"] == "observed"
    assert evidence["source_ref"] == "https://www.linkedin.com/messaging/thread/123"
    assert evidence["metric"] == "messages_envoyes" and evidence["value"] == 1
    assert economy.evaluate_experiment(B, experiment)["verdict"] == "supports"

    duplicate = actions.propose(
        B,
        channel_id,
        "submit",
        safe_payload(),
        requested_by="agent:GROWTH",
        experiment_id=experiment,
        idempotency_key="phase2-message-1",
    )
    assert duplicate["duplicate"] is True
