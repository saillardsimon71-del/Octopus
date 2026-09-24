"""Regression tests for the public evidence acquisition path.

Fixtures are reduced snapshots modeled on the real H2 failure classes (article, JS shell,
dictionary, CloudFront/Akamai block). They intentionally keep only enough markup/text to assert
that the fact an agent needs survives acquisition while navigation chrome does not.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents import browser, runtime


FIXTURES = Path(__file__).parent / "fixtures" / "web_pages.json"


def _cases():
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case["name"])
def test_reduced_real_world_pages_extract_expected_content(case):
    title, text, method = browser.extract_public_html(case["html"])

    assert method == case["expected_method"]
    if case["expected_text"]:
        assert case["expected_text"] in text
    else:
        assert text == ""
    if case["forbidden_text"]:
        assert case["forbidden_text"].lower() not in text.lower()
    assert isinstance(title, str)


def test_block_pages_are_detected_from_http_content(monkeypatch):
    case = next(item for item in _cases() if item["name"] == "cloudfront_block")

    class Response:
        status_code = 403
        headers = {"content-type": "text/html; charset=utf-8"}
        encoding = "utf-8"

        def iter_content(self, chunk_size=65536):
            yield case["html"].encode("utf-8")

        def close(self):
            pass

    monkeypatch.setattr(browser.requests, "get", lambda *a, **k: Response())

    record = browser.fetch_public_http(case["source_url"], guard=lambda _url: True)

    assert record.blocked is True
    assert record.http_status == 403
    assert record.error == "http_status_403"
    assert "Request blocked" in record.main_text


def test_http_first_returns_without_starting_chromium(monkeypatch):
    case = next(item for item in _cases() if item["name"] == "humanite_article")

    class Response:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        encoding = "utf-8"

        def iter_content(self, chunk_size=65536):
            yield case["html"].encode("utf-8")

        def close(self):
            pass

    monkeypatch.setattr(browser.requests, "get", lambda *a, **k: Response())
    monkeypatch.setattr(
        browser,
        "new_browser",
        lambda *a, **k: pytest.fail("Chromium ne doit pas démarrer quand HTTP suffit"),
    )

    record = browser.acquire_public_page(case["source_url"], guard=lambda _url: True)

    assert record.rendered is False
    assert record.extraction_method == "http:html_main"
    assert case["expected_text"] in record.main_text


def test_sparse_http_falls_back_to_rendered_html(monkeypatch):
    requested = "https://example.com/js"
    sparse = browser.PublicPageRecord(
        requested_url=requested,
        final_url=requested,
        fetched_at="2026-09-24T00:00:00+00:00",
        http_status=200,
        content_type="text/html",
        title="",
        extraction_method="http:html_body",
        rendered=False,
        blocked=False,
        main_text="",
        text_chars=0,
        raw_chars=100,
        truncated=False,
        error=None,
    )

    rendered_html = """<!doctype html><html><body><nav>Menu Compte</nav><main>
    <h1>Délai de paiement</h1>
    <p>Le contenu rendu contient enfin la preuve économique attendue après JavaScript.</p>
    <p>Cette seconde phrase rend le contenu suffisamment long pour être exploitable par le pipeline.</p>
    <p>Une troisième phrase confirme que le corps principal, et non la navigation, est retenu.</p>
    </main></body></html>"""

    class FakeBrowser:
        def goto(self, url):
            self._url = url
        def wait_for_public_render(self):
            pass
        def url(self):
            return self._url
        def html(self):
            return rendered_html
        def stop(self):
            pass

    monkeypatch.setattr(browser, "fetch_public_http", lambda *a, **k: sparse)
    monkeypatch.setattr(browser, "new_browser", lambda *a, **k: FakeBrowser())

    record = browser.acquire_public_page(requested, guard=lambda _url: True)

    assert record.rendered is True
    assert record.extraction_method == "playwright:html_main"
    assert "preuve économique attendue" in record.main_text
    assert "Menu Compte" not in record.main_text


def test_browse_prompt_view_keeps_fact_beyond_old_1500_character_cutoff():
    text = ("contexte utile " * 180) + "FAIT_CIBLE_2026" + (" suite" * 50)
    result = {
        "url": "https://example.com/article",
        "page": {
            "final_url": "https://example.com/article",
            "title": "Article",
            "fetched_at": "2026-09-24T00:00:00+00:00",
            "http_status": 200,
            "extraction_method": "http:html_main",
            "rendered": False,
            "blocked": False,
            "error": None,
            "text_chars": len(text),
            "main_text": text,
        },
    }

    view = runtime._tool_result_view("browse", result)

    assert "FAIT_CIBLE_2026" in view
    assert len(view) > 1500


def test_structured_page_record_survives_agent_step_while_prompt_is_compact(monkeypatch):
    text = ("preuve structurée " * 500) + "FIN_PREUVE"
    full_result = {
        "url": "https://example.com/preuve",
        "page": {
            "final_url": "https://example.com/preuve",
            "title": "Preuve",
            "fetched_at": "2026-09-24T00:00:00+00:00",
            "http_status": 200,
            "content_type": "text/html",
            "extraction_method": "http:html_main",
            "rendered": False,
            "blocked": False,
            "main_text": text,
            "text_chars": len(text),
            "raw_chars": len(text),
            "truncated": False,
            "error": None,
        },
        "texte": text,
    }
    monkeypatch.setitem(runtime.TOOLS["browse"], "fn", lambda args: full_result)

    actions = iter([
        {"tool": "browse", "args": {"url": "https://example.com/preuve"}},
        {"final": "terminé"},
    ])
    monkeypatch.setattr(runtime.deepseek, "call_json", lambda *a, **k: next(actions))

    result = runtime.run_agent(
        "SOUT",
        "collecter",
        max_steps=3,
        allowed_tools={"browse"},
    )

    step = result["steps"][0]
    assert step["result_data"]["page"]["main_text"] == text
    assert step["browse_meta"]["text_chars"] == len(text)
    assert len(step["result"]) < len(text)
    assert "extraction_method" in step["result"]


def test_synthesis_projection_drops_full_structured_payload():
    huge = "x" * 20000
    results = [{
        "role": "SOUT",
        "task": "collecter",
        "final": "ok",
        "steps": [{
            "step": 1,
            "tool": "browse",
            "args": {"url": "https://example.com"},
            "result": '{"texte":"vue compacte"}',
            "result_data": {"page": {"main_text": huge}},
            "browse_meta": {"url": "https://example.com", "text_chars": len(huge), "blocked": False},
        }],
    }]

    projected = runtime._mission_prompt_results(results)
    encoded = json.dumps(projected, ensure_ascii=False)

    assert "result_data" not in encoded
    assert huge not in encoded
    assert "vue compacte" in encoded
