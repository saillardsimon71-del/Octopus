"""Regression tests for the public evidence acquisition path.

Fixtures are reduced snapshots modeled on the real H2 failure classes (article, JS shell,
dictionary, CloudFront/Akamai block). They intentionally keep only enough markup/text to assert
that the fact an agent needs survives acquisition while navigation chrome does not.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents import browser, runtime, web_guard


FIXTURES = Path(__file__).parent / "fixtures" / "web_pages.json"
SEARCH_FIXTURES = Path(__file__).parent / "fixtures" / "search_results.json"


def _cases():
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def _search_cases():
    return json.loads(SEARCH_FIXTURES.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _search_cases(), ids=lambda case: case["name"])
def test_reduced_h2_search_results_keep_returned_urls(case):
    assert runtime._search_result_urls(case["result"]) == case["expected_urls"]
    candidates = runtime._search_result_candidates(case["result"])
    assert [item["url"] for item in candidates] == case["expected_urls"]


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


def test_nested_div_does_not_close_role_main_early():
    html = """<html><body>
    <div role="main">
      <div><span>Introduction utile.</span></div>
      <p>FAIT_APRES_DIV_IMBRIQUE : ce contenu doit rester dans la zone principale.</p>
      <p>Une seconde phrase allonge suffisamment le contenu principal pour l'extraction.</p>
    </div>
    <div>bruit hors contenu principal</div>
    </body></html>"""

    _, text, method = browser.extract_public_html(html)

    assert method == "html_main"
    assert "FAIT_APRES_DIV_IMBRIQUE" in text
    assert "bruit hors contenu principal" not in text


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


def test_blocked_or_empty_public_page_is_not_marked_as_visited(monkeypatch):
    url = "https://example.com/blocked"
    blocked = browser.PublicPageRecord(
        requested_url=url,
        final_url=url,
        fetched_at="2026-09-24T00:00:00+00:00",
        http_status=403,
        content_type="text/html",
        title="Access denied",
        extraction_method="http:html_body",
        rendered=False,
        blocked=True,
        main_text="Request blocked",
        text_chars=len("Request blocked"),
        raw_chars=200,
        truncated=False,
        error="http_status_403",
    )
    monkeypatch.setattr(browser, "acquire_public_page", lambda *a, **k: blocked)

    with web_guard.session():
        result = runtime._browse({"url": url})
        assert result["page"]["blocked"] is True
        assert url not in web_guard.current().visited


def test_public_browse_does_not_spend_an_llm_call_for_page_inspection(monkeypatch):
    url = "https://example.com/no-llm"
    text = "contenu principal directement extrait " * 20
    record = browser.PublicPageRecord(
        requested_url=url,
        final_url=url,
        fetched_at="2026-09-24T00:00:00+00:00",
        http_status=200,
        content_type="text/html",
        title="Sans LLM",
        extraction_method="http:html_main",
        rendered=False,
        blocked=False,
        main_text=text,
        text_chars=len(text),
        raw_chars=len(text),
        truncated=False,
        error=None,
    )
    monkeypatch.setattr(browser, "acquire_public_page", lambda *a, **k: record)
    monkeypatch.setattr(
        runtime.deepseek,
        "call",
        lambda *a, **k: pytest.fail("browse public ne doit plus appeler web.inspect_page"),
    )

    with web_guard.session():
        result = runtime._browse({"url": url})

    assert result["page"]["extraction_method"] == "http:html_main"
    assert result["vision"] is None


def test_usable_public_page_is_marked_as_visited(monkeypatch):
    url = "https://example.com/usable"
    text = "preuve factuelle exploitable " * 20
    usable = browser.PublicPageRecord(
        requested_url=url,
        final_url=url,
        fetched_at="2026-09-24T00:00:00+00:00",
        http_status=200,
        content_type="text/html",
        title="Preuve",
        extraction_method="http:html_main",
        rendered=False,
        blocked=False,
        main_text=text,
        text_chars=len(text),
        raw_chars=len(text),
        truncated=False,
        error=None,
    )
    monkeypatch.setattr(browser, "acquire_public_page", lambda *a, **k: usable)

    with web_guard.session():
        runtime._browse({"url": url})
        assert url in web_guard.current().visited


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


class _HttpResponse:
    def __init__(self, body, *, status=200, content_type=None, location=None, encoding="utf-8"):
        self.status_code = status
        self.headers = {}
        if content_type is not None:
            self.headers["content-type"] = content_type
        if location is not None:
            self.headers["location"] = location
        self.encoding = encoding
        self._body = body.encode(encoding, errors="replace") if isinstance(body, str) else body
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for idx in range(0, len(self._body), chunk_size):
            yield self._body[idx:idx + chunk_size]

    def close(self):
        self.closed = True


class _JsonResponse:
    def __init__(self, payload=None, *, status=200, json_error=None):
        self.status_code = status
        self._payload = payload
        self._json_error = json_error
        self.closed = False

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload

    def close(self):
        self.closed = True


class _RenderedBlockedPage:
    def goto(self, url):
        self._url = url

    def wait_for_public_render(self):
        pass

    def url(self):
        return getattr(self, "_url", "https://example.org/blocked")

    def html(self):
        return "<html><body>Request blocked by verification service.</body></html>"

    def stop(self):
        pass


def _allow_tavily_extract(monkeypatch, *, key="test-tavily-key"):
    monkeypatch.setattr(browser.config, "TAVILY_API_KEY", key)
    monkeypatch.setenv("OCTOPUS_SEARCH_TAVILY_COST_CLASS", "free_quota")


def _forbid_playwright(monkeypatch):
    monkeypatch.setattr(
        browser,
        "new_browser",
        lambda *a, **k: pytest.fail("Chromium ne doit pas démarrer pour ce cas"),
    )


def test_html_public_normal_does_not_call_tavily_extract(monkeypatch):
    html = "<main>" + ("Ce document parle de PDF sans être un PDF. " * 12) + "</main>"
    post_calls = []
    monkeypatch.setattr(browser.requests, "get", lambda *a, **k: _HttpResponse(
        html, content_type="text/html; charset=utf-8"))
    monkeypatch.setattr(browser.requests, "post", lambda *a, **k: post_calls.append((a, k)))
    _forbid_playwright(monkeypatch)
    _allow_tavily_extract(monkeypatch)

    record = browser.acquire_public_page("https://example.org/article", guard=lambda _url: True)

    assert post_calls == []
    assert record.extraction_method == "http:html_main"
    assert browser.is_public_text_acquisition(record)


def test_pdf_content_type_calls_tavily_once_and_returns_clean_truncated_text(monkeypatch):
    url = "https://example.org/rfp"
    raw_content = "Request for Proposal 17-525 Addendum 2\n" + ("Professional Services for Data Migration Services. " * 1200)
    post_calls = []
    monkeypatch.setattr(browser.requests, "get", lambda *a, **k: _HttpResponse(
        b"%PDF-1.5\n1 0 obj /FlateDecode", content_type="application/pdf"))

    def fake_post(endpoint, *, json, headers, timeout):
        post_calls.append({"endpoint": endpoint, "json": json, "headers": headers, "timeout": timeout})
        return _JsonResponse({"results": [{"url": url, "raw_content": raw_content, "title": "Tulsa RFP"}],
                              "failed_results": []})

    monkeypatch.setattr(browser.requests, "post", fake_post)
    _forbid_playwright(monkeypatch)
    _allow_tavily_extract(monkeypatch)

    record = browser.acquire_public_page(url, guard=lambda _url: True)

    assert len(post_calls) == 1
    assert post_calls[0]["endpoint"] == "https://api.tavily.com/extract"
    assert post_calls[0]["json"] == {"urls": [url], "extract_depth": "basic", "include_images": False}
    assert record.extraction_method == "tavily:extract_basic"
    assert record.title == "Tulsa RFP"
    assert record.main_text.startswith("Request for Proposal 17-525 Addendum 2")
    assert record.text_chars == browser.PUBLIC_MAIN_TEXT_MAX_CHARS
    assert record.raw_chars == len(raw_content)
    assert record.truncated is True
    assert record.error is None
    assert browser.is_public_text_acquisition(record)


def test_pdf_wrong_content_type_with_pdf_signature_calls_tavily(monkeypatch):
    url = "https://example.org/download?id=17"
    post_calls = []
    monkeypatch.setattr(browser.requests, "get", lambda *a, **k: _HttpResponse(
        b"%PDF-1.7\nstream FlateDecode binary", content_type=""))
    monkeypatch.setattr(browser.requests, "post", lambda endpoint, **kwargs: (
        post_calls.append(kwargs) or _JsonResponse({"results": [{"url": url, "raw_content": "texte propre " * 20}],
                                                    "failed_results": []})
    ))
    _forbid_playwright(monkeypatch)
    _allow_tavily_extract(monkeypatch)

    record = browser.acquire_public_page(url, guard=lambda _url: True)

    assert len(post_calls) == 1
    assert record.extraction_method == "tavily:extract_basic"
    assert "%PDF" not in record.main_text


def test_url_path_pdf_is_detected_but_query_parameter_pdf_is_not(monkeypatch):
    _allow_tavily_extract(monkeypatch)
    post_calls = []
    monkeypatch.setattr(browser.requests, "post", lambda endpoint, **kwargs: (
        post_calls.append(kwargs) or _JsonResponse({"results": [{"url": kwargs["json"]["urls"][0],
                                                                  "raw_content": "texte extrait " * 20}],
                                                    "failed_results": []})
    ))
    _forbid_playwright(monkeypatch)

    monkeypatch.setattr(browser.requests, "get", lambda *a, **k: _HttpResponse(
        "payload servi avec mauvais header", content_type="text/html"))
    path_record = browser.acquire_public_page("https://example.org/files/report.pdf", guard=lambda _url: True)
    assert path_record.extraction_method == "tavily:extract_basic"
    assert len(post_calls) == 1

    html = "<main>" + ("Lien vers file=report.pdf mais page HTML normale. " * 8) + "</main>"
    monkeypatch.setattr(browser.requests, "get", lambda *a, **k: _HttpResponse(
        html, content_type="text/html"))
    query_record = browser.acquire_public_page("https://example.org/download?file=report.pdf", guard=lambda _url: True)
    assert query_record.extraction_method == "http:html_main"
    assert len(post_calls) == 1


def test_html_containing_word_pdf_does_not_trigger_document_extract(monkeypatch):
    _allow_tavily_extract(monkeypatch)
    post_calls = []
    monkeypatch.setattr(browser.requests, "post", lambda *a, **k: post_calls.append((a, k)))
    monkeypatch.setattr(browser.requests, "get", lambda *a, **k: _HttpResponse(
        "<main>" + ("Le mot PDF apparaît dans une page HTML ordinaire. " * 8) + "</main>",
        content_type="text/html"))
    _forbid_playwright(monkeypatch)

    record = browser.acquire_public_page("https://example.org/normal", guard=lambda _url: True)

    assert post_calls == []
    assert record.extraction_method == "http:html_main"


@pytest.mark.parametrize("payload", [
    {"failed_results": [{"url": "https://example.org/rfp.pdf", "error": "Failed to fetch url"}],
     "results": []},
    {"results": [{"url": "https://evil.example/rfp.pdf", "raw_content": "texte propre " * 20}],
     "failed_results": []},
    {"results": [{"url": "https://example.org/rfp.pdf", "raw_content": ""}],
     "failed_results": []},
    {"results": [{"url": "https://example.org/rfp.pdf", "raw_content": "%PDF-1.5\nFlateDecode"}],
     "failed_results": []},
    {"results": "not-a-list", "failed_results": []},
])
def test_tavily_extract_failures_fail_closed_without_binary_prompt(monkeypatch, payload):
    url = "https://example.org/rfp.pdf"
    pdf_binary = "%PDF-1.5\n" + ("FlateDecode stream " * 3000)
    monkeypatch.setattr(browser.requests, "get", lambda *a, **k: _HttpResponse(
        pdf_binary, content_type="application/pdf"))
    monkeypatch.setattr(browser.requests, "post", lambda *a, **k: _JsonResponse(payload))
    _forbid_playwright(monkeypatch)
    _allow_tavily_extract(monkeypatch)

    record = browser.acquire_public_page(url, guard=lambda _url: True)
    view = runtime._tool_result_view("browse", {"url": url, "page": record.as_dict()})

    assert record.error is not None
    assert not browser.is_public_text_acquisition(record)
    assert len(record.main_text) < 500
    assert "%PDF-1.5" not in record.main_text
    assert "FlateDecode stream" not in view


def test_tavily_extract_malformed_json_fails_closed(monkeypatch):
    url = "https://example.org/rfp.pdf"
    monkeypatch.setattr(browser.requests, "get", lambda *a, **k: _HttpResponse(
        b"%PDF-1.5", content_type="application/pdf"))
    monkeypatch.setattr(browser.requests, "post", lambda *a, **k: _JsonResponse(json_error=ValueError("bad json")))
    _forbid_playwright(monkeypatch)
    _allow_tavily_extract(monkeypatch)

    record = browser.acquire_public_page(url, guard=lambda _url: True)

    assert record.error is not None
    assert "malformed_json" in record.error
    assert not browser.is_public_text_acquisition(record)


@pytest.mark.parametrize("key,cost_class,expected", [
    ("", "free_quota", "TAVILY_API_KEY absente"),
    ("test-key", "", "free_quota requis"),
    ("test-key", "paid", "paid bloquée"),
])
def test_tavily_extract_requires_key_and_explicit_free_quota(monkeypatch, key, cost_class, expected):
    url = "https://example.org/rfp.pdf"
    monkeypatch.setattr(browser.config, "TAVILY_API_KEY", key)
    if cost_class:
        monkeypatch.setenv("OCTOPUS_SEARCH_TAVILY_COST_CLASS", cost_class)
    else:
        monkeypatch.delenv("OCTOPUS_SEARCH_TAVILY_COST_CLASS", raising=False)
    monkeypatch.setattr(browser.requests, "get", lambda *a, **k: _HttpResponse(
        b"%PDF-1.5", content_type="application/pdf"))
    monkeypatch.setattr(browser.requests, "post", lambda *a, **k: pytest.fail("Tavily ne doit pas être appelé"))
    _forbid_playwright(monkeypatch)

    record = browser.acquire_public_page(url, guard=lambda _url: True)

    assert expected in record.error
    assert record.extraction_method == "http:document_pdf"
    assert not browser.is_public_text_acquisition(record)


@pytest.mark.parametrize("status", [401, 403, 429])
def test_blocked_http_statuses_do_not_call_tavily_extract(monkeypatch, status):
    _allow_tavily_extract(monkeypatch)
    monkeypatch.setattr(browser.requests, "get", lambda *a, **k: _HttpResponse(
        "Request blocked", status=status, content_type="text/html"))
    monkeypatch.setattr(browser.requests, "post", lambda *a, **k: pytest.fail("Tavily ne doit pas être appelé"))
    monkeypatch.setattr(browser, "new_browser", lambda *a, **k: _RenderedBlockedPage())

    record = browser.acquire_public_page("https://example.org/rfp.pdf", guard=lambda _url: True)

    assert record.error is not None or record.blocked is True
    assert record.extraction_method != "tavily:extract_basic"


def test_blocked_captcha_payload_does_not_call_tavily_extract(monkeypatch):
    _allow_tavily_extract(monkeypatch)
    monkeypatch.setattr(browser.requests, "get", lambda *a, **k: _HttpResponse(
        "Just a moment CAPTCHA verification", status=200, content_type="text/html"))
    monkeypatch.setattr(browser.requests, "post", lambda *a, **k: pytest.fail("Tavily ne doit pas être appelé"))
    monkeypatch.setattr(browser, "new_browser", lambda *a, **k: _RenderedBlockedPage())

    record = browser.acquire_public_page("https://example.org/rfp.pdf", guard=lambda _url: True)

    assert record.blocked is True
    assert record.extraction_method.startswith("playwright:")


def test_redirect_public_validated_then_tavily_extract_uses_final_url(monkeypatch):
    requested = "https://example.org/start"
    final = "https://cdn.example.org/docs/final.pdf"
    seen_by_guard = []
    responses = iter([
        _HttpResponse("", status=302, location=final),
        _HttpResponse(b"%PDF-1.5", content_type="application/pdf"),
    ])
    post_calls = []
    monkeypatch.setattr(browser.requests, "get", lambda *a, **k: next(responses))
    monkeypatch.setattr(browser.requests, "post", lambda endpoint, **kwargs: (
        post_calls.append(kwargs) or _JsonResponse({"results": [{"url": final, "raw_content": "texte final " * 20}],
                                                    "failed_results": []})
    ))
    _forbid_playwright(monkeypatch)
    _allow_tavily_extract(monkeypatch)

    record = browser.acquire_public_page(requested, guard=lambda u: seen_by_guard.append(u) or True)

    assert requested in seen_by_guard
    assert final in seen_by_guard
    assert post_calls[0]["json"]["urls"] == [final]
    assert record.requested_url == requested
    assert record.final_url == final
    assert record.extraction_method == "tavily:extract_basic"


def test_refused_redirect_does_not_call_tavily_extract(monkeypatch):
    requested = "https://example.org/start"
    final = "https://blocked.example.org/final.pdf"
    monkeypatch.setattr(browser.requests, "get", lambda *a, **k: _HttpResponse("", status=302, location=final))
    monkeypatch.setattr(browser.requests, "post", lambda *a, **k: pytest.fail("Tavily ne doit pas être appelé"))
    monkeypatch.setattr(browser, "new_browser", lambda *a, **k: _RenderedBlockedPage())

    record = browser.acquire_public_page(requested, guard=lambda u: u != final)

    assert record.extraction_method.startswith("playwright:") or record.blocked is True


def test_private_ip_and_account_context_do_not_use_public_tavily_extract(monkeypatch):
    monkeypatch.setattr(browser.requests, "post", lambda *a, **k: pytest.fail("Tavily ne doit pas être appelé"))
    with web_guard.session():
        with pytest.raises(web_guard.BrowseRefused):
            runtime._browse({"url": "http://127.0.0.1/private.pdf"})

    class AccountBrowser:
        def goto(self, url):
            self._url = url
        def url(self):
            return self._url
        def see(self, agent=None):
            return {"description": "compte", "vision_error": None}
        def snapshot(self, max_chars=6000):
            return "contenu compte"
        def stop(self):
            pass

    monkeypatch.setattr(runtime.web_guard, "check", lambda url, state: runtime.web_guard.ACCOUNT)
    monkeypatch.setattr(runtime.web_guard, "classify", lambda url: runtime.web_guard.ACCOUNT)
    monkeypatch.setattr(browser, "profile_has_cookies", lambda url: True)
    monkeypatch.setattr(browser, "acquire_public_page", lambda *a, **k: pytest.fail("pas de browse public"))
    monkeypatch.setattr(browser, "new_browser", lambda *a, **k: AccountBrowser())

    with web_guard.session():
        result = runtime._browse({"url": "https://account.example.org/file.pdf"})

    assert result["source"] == "compte connecté (lecture seule)"


def test_usable_browse_count_uses_text_acquisition_contract():
    from agents import task_handlers
    html_text = "preuve exploitable " * 20
    pdf_binary = "%PDF-1.5 FlateDecode " * 200
    tavily_text = "Request for Proposal Professional Services Data Migration " * 10

    def step(url, page):
        data = {"url": url, "page": page}
        return {"tool": "browse", "args": {"url": url}, "result_data": data,
                "browse_meta": runtime._browse_result_meta(data)}

    blocked_403 = {
        "requested_url": "https://indeed.example/job", "final_url": "https://indeed.example/job",
        "fetched_at": "2026-09-24T00:00:00+00:00", "http_status": 403,
        "content_type": "text/html", "title": "", "extraction_method": "http:html_body",
        "rendered": False, "blocked": True, "main_text": "Request blocked " * 20,
        "text_chars": len("Request blocked " * 20), "raw_chars": 500, "truncated": False,
        "error": "http_status_403",
    }
    good_html = {
        "requested_url": "https://gigradar.example/page", "final_url": "https://gigradar.example/page",
        "fetched_at": "2026-09-24T00:00:00+00:00", "http_status": 200,
        "content_type": "text/html", "title": "", "extraction_method": "http:html_main",
        "rendered": False, "blocked": False, "main_text": html_text, "text_chars": len(html_text),
        "raw_chars": len(html_text), "truncated": False, "error": None,
    }
    binary_pdf = {
        "requested_url": "https://city.example/rfp.pdf", "final_url": "https://city.example/rfp.pdf",
        "fetched_at": "2026-09-24T00:00:00+00:00", "http_status": 200,
        "content_type": "", "title": "", "extraction_method": "http:html_body",
        "rendered": False, "blocked": False, "main_text": pdf_binary, "text_chars": len(pdf_binary),
        "raw_chars": len(pdf_binary), "truncated": False, "error": None,
    }
    tavily_pdf = {
        "requested_url": "https://city.example/rfp.pdf", "final_url": "https://city.example/rfp.pdf",
        "fetched_at": "2026-09-24T00:00:00+00:00", "http_status": 200,
        "content_type": "application/pdf", "title": "", "extraction_method": "tavily:extract_basic",
        "rendered": False, "blocked": False, "main_text": tavily_text, "text_chars": len(tavily_text),
        "raw_chars": len(tavily_text), "truncated": False, "error": None,
    }

    pre = [{"steps": [
        step("https://indeed.example/job", blocked_403),
        step("https://gigradar.example/page", good_html),
        step("https://city.example/rfp.pdf", binary_pdf),
    ]}]
    post = [{"steps": [
        step("https://gigradar.example/page", good_html),
        step("https://city.example/rfp.pdf", tavily_pdf),
    ]}]

    assert task_handlers._usable_browse_urls(pre) == ["https://gigradar.example/page"]
    assert task_handlers._usable_browse_urls(post) == [
        "https://gigradar.example/page", "https://city.example/rfp.pdf"]


@pytest.mark.parametrize("status", [401, 429, 500])
def test_tavily_extract_http_errors_fail_closed(monkeypatch, status):
    url = "https://example.org/rfp.pdf"
    monkeypatch.setattr(browser.requests, "get", lambda *a, **k: _HttpResponse(
        b"%PDF-1.5", content_type="application/pdf"))
    monkeypatch.setattr(browser.requests, "post", lambda *a, **k: _JsonResponse(status=status))
    _forbid_playwright(monkeypatch)
    _allow_tavily_extract(monkeypatch)

    record = browser.acquire_public_page(url, guard=lambda _url: True)

    assert record.error is not None
    assert f"http_status_{status}" in record.error
    assert not browser.is_public_text_acquisition(record)


def test_tavily_extract_network_error_is_compact_and_redacts_key(monkeypatch):
    url = "https://example.org/rfp.pdf"
    key = "secret-test-key"
    monkeypatch.setattr(browser.requests, "get", lambda *a, **k: _HttpResponse(
        b"%PDF-1.5", content_type="application/pdf"))

    def fail_post(*args, **kwargs):
        raise TimeoutError(f"timeout with bearer {key}")

    monkeypatch.setattr(browser.requests, "post", fail_post)
    _forbid_playwright(monkeypatch)
    _allow_tavily_extract(monkeypatch, key=key)

    record = browser.acquire_public_page(url, guard=lambda _url: True)

    assert record.error is not None
    assert "TimeoutError" in record.error
    assert key not in record.error
    assert key not in record.main_text
    assert len(record.main_text) < 500
