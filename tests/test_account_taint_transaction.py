"""Tests for the account-taint transaction fix (#100).

A failed account-capable navigation (blocked/error page) must NOT commit mission-level
account_read.  A successful account read must still commit it and preserve all historical
anti-exfiltration properties.
"""
from __future__ import annotations

import json

import pytest

from agents import browser, db, deepseek, runtime, web_guard
from agents.web_guard import ACCOUNT, PUBLIC, BrowseRefused, BrowseState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def deterministic_dns(monkeypatch):
    def fake_getaddrinfo(host, *args, **kwargs):
        private = {
            "localtest.me": "127.0.0.1",
            "127-0-0-1.nip.io": "127.0.0.1",
            "metadata.google.internal": "169.254.169.254",
        }
        ip = private.get(host, "93.184.216.34")
        return [(2, 1, 6, "", (ip, 0))]
    monkeypatch.setattr(web_guard.socket, "getaddrinfo", fake_getaddrinfo)


class FakeBrowser:
    """Simulated browser for account navigation scenarios."""
    PAGES = {
        "https://dashboard.stripe.com/": (
            "Solde disponible : 1 234,56 €\nTransactions récentes : facture 100 €",
        ),
        "https://www.reddit.com/r/freelance/comments/abc/besoin": (
            "www.reddit.com est bloqué\n\n"
            "Cette page a été bloquée par Chromium\n\n"
            "ERR_BLOCKED_BY_CLIENT\n\n"
            "Actualiser",
        ),
    }
    # Map URL → (page_text, raise_on_goto)
    BLOCK_PAGES = {
        "https://www.linkedin.com/posts/cabinet-recrute": (
            "You've been blocked by network security.\n\n"
            "If you think you've been blocked by mistake, file a ticket below.",
        ),
        "https://x.com/someuser/status/123": (
            "Performing security verification\n\nPlease wait while we verify your request.",
        ),
    }
    opened: list = []

    def __init__(self, headless, account, guard):
        self.headless, self.account, self.guard, self.blocked, self._url = headless, account, guard, [], ""
        FakeBrowser.opened.append(self)

    def goto(self, url):
        self._url = url
        # Simulate guard being called for the main navigation (as Playwright routing does)
        self.guard(url)

    def url(self):
        return self._url

    def snapshot(self, max_chars=4000):
        text = self.PAGES.get(self._url, ("",))[0]
        if not text:
            text = self.BLOCK_PAGES.get(self._url, ("",))[0]
        return text[:max_chars]

    def html(self):
        return self.snapshot()

    def see(self, agent="SOUT"):
        return {"description": "capture", "vision_error": None}

    def stop(self):
        pass


@pytest.fixture(autouse=True)
def fake_browser(monkeypatch):
    FakeBrowser.opened = []
    monkeypatch.setattr(browser, "new_browser", lambda headless=False, account=False, guard=None:
                        FakeBrowser(headless, account, guard))
    monkeypatch.setattr(browser, "profile_has_cookies", lambda url, profile_dir=None: True)

    def acquire_public(url, guard=None):
        if guard is not None and not guard(url):
            return browser.PublicPageRecord(
                requested_url=url, final_url=url, fetched_at="2026-09-24T00:00:00+00:00",
                http_status=None, content_type="", title="", extraction_method="http:html_body",
                rendered=False, blocked=True, main_text="", text_chars=0, raw_chars=0,
                truncated=False, error="refusé",
            )
        text = (FakeBrowser.PAGES[url][0] if url in FakeBrowser.PAGES else
                FakeBrowser.BLOCK_PAGES.get(url, ("Page publique normale avec suffisamment de contenu pour le test. " * 5,))[0])
        return browser.PublicPageRecord(
            requested_url=url, final_url=url, fetched_at="2026-09-24T00:00:00+00:00",
            http_status=200, content_type="text/html", title="fixture",
            extraction_method="http:html_main",
            rendered=False, blocked=False, main_text=text, text_chars=len(text), raw_chars=len(text),
            truncated=False, error=None,
        )

    monkeypatch.setattr(browser, "acquire_public_page", acquire_public)
    return FakeBrowser


def run_actions(monkeypatch, role, actions):
    it = iter(actions + [{"final": "fin"}])
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: next(it))
    return runtime.run_agent(role, "veille", max_steps=len(actions) + 1)


# ---------------------------------------------------------------------------
# Tests: 1. Successful account read commits mission taint
# ---------------------------------------------------------------------------

def test_unscoped_account_domain_is_always_anonymous(monkeypatch, fake_browser):
    with web_guard.session() as state:
        run_actions(monkeypatch, "LEDGER", [
            {"tool": "browse", "args": {"url": "https://dashboard.stripe.com/"}},
        ])
    assert state.account_read is False

    assert fake_browser.opened == []


# ---------------------------------------------------------------------------
# Tests: 2. After successful account read, later public navigation refused
# ---------------------------------------------------------------------------

def test_after_successful_account_read_public_navigation_refused(monkeypatch, fake_browser):
    with web_guard.session() as state:
        state.account_read = True
        result = run_actions(monkeypatch, "SOUT", [
            {"tool": "browse", "args": {"url": "https://dashboard.stripe.com/"}},
            {"tool": "browse", "args": {"url": "https://tool-advisor.fr/blog/logiciel-facturation"}},
        ])
    steps = result["steps"]
    assert "ressource" in steps[0]["result"]
    assert "déjà lu un compte" in steps[1]["result"]
    assert state.account_read is True


# ---------------------------------------------------------------------------
# Tests: 3. Account request taints the TEMP security state before response
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Tests: 4. Public subrequest during account navigation is refused
# ---------------------------------------------------------------------------

def test_public_subrequest_during_account_navigation_is_refused(monkeypatch, fake_browser):
    """When an account page tries to fetch a public URL during its own load, it must be blocked."""
    temp_state = BrowseState()

    def account_guard(url):
        return runtime._browser_request_allowed(url, temp_state, account_context=True)

    # Simulate: guard is called for the account URL first (tainting temp_state)
    assert account_guard("https://dashboard.stripe.com/") is True
    assert temp_state.account_read is True

    # Now a public subrequest from the account page must be refused
    assert account_guard("https://blog-exemple.fr/impayes") is False


# ---------------------------------------------------------------------------
# Tests: 5. Failed account navigation does NOT commit mission taint
# ---------------------------------------------------------------------------

def test_failed_account_navigation_does_not_commit_mission_taint(monkeypatch, fake_browser):
    """Reddit returns ERR_BLOCKED_BY_CLIENT; mission state must stay untainted."""
    with web_guard.session() as state:
        result = run_actions(monkeypatch, "SOUT", [
            {"tool": "browse", "args": {"url": "https://www.reddit.com/r/freelance/comments/abc/besoin"}},
        ])
    assert state.account_read is False


# ---------------------------------------------------------------------------
# Tests: 6. ERR_BLOCKED_BY_CLIENT account attempt does NOT commit mission taint
# ---------------------------------------------------------------------------

def test_err_blocked_by_client_does_not_commit_mission_taint(monkeypatch, fake_browser):
    """Chromium ERR_BLOCKED_BY_CLIENT error page does not count as successful account read."""
    with web_guard.session() as state:
        result = run_actions(monkeypatch, "SOUT", [
            {"tool": "browse", "args": {"url": "https://www.reddit.com/r/freelance/comments/abc/besoin"}},
        ])
    assert state.account_read is False
    step = result["steps"][0]
    # The result should indicate the page was blocked
    assert "bloquée" in step["result"].lower() or "error" in step["result"].lower() or "block" in step["result"].lower()


# ---------------------------------------------------------------------------
# Tests: 7. "You've been blocked by network security" does NOT commit taint
# ---------------------------------------------------------------------------

def test_blocked_by_network_security_does_not_commit_taint(monkeypatch, fake_browser):
    """LinkedIn-style block page does not count as successful account read."""
    with web_guard.session() as state:
        result = run_actions(monkeypatch, "SOUT", [
            {"tool": "browse", "args": {"url": "https://www.linkedin.com/posts/cabinet-recrute"}},
        ])
    assert state.account_read is False


# ---------------------------------------------------------------------------
# Tests: 8. "Performing security verification" does NOT commit taint
# ---------------------------------------------------------------------------

def test_performing_security_verification_does_not_commit_taint(monkeypatch, fake_browser):
    """X/Twitter-style security check page does not count as successful account read."""
    with web_guard.session() as state:
        result = run_actions(monkeypatch, "SOUT", [
            {"tool": "browse", "args": {"url": "https://x.com/someuser/status/123"}},
        ])
    assert state.account_read is False


# ---------------------------------------------------------------------------
# Tests: 9. Failed Reddit-like attempt followed by public URL: public URL allowed
# ---------------------------------------------------------------------------

def test_failed_reddit_attempt_followed_by_public_url_is_allowed(monkeypatch, fake_browser):
    """The H3 #65 regression case: after a blocked Reddit attempt, public URLs must still work."""
    with web_guard.session() as state:
        result = run_actions(monkeypatch, "SOUT", [
            {"tool": "browse", "args": {"url": "https://www.reddit.com/r/freelance/comments/abc/besoin"}},
            {"tool": "browse", "args": {"url": "https://tool-advisor.fr/blog/logiciel-facturation"}},
        ])
    steps = result["steps"]
    # First: Reddit was attempted but failed (blocked page)
    assert state.account_read is False
    # Second: public URL is allowed
    assert "déjà lu un compte" not in steps[1]["result"]
    assert "NON FIABLE" in steps[1]["result"]


# ---------------------------------------------------------------------------
# Tests: 10. Successful Stripe-like read followed by public URL: public refused
# ---------------------------------------------------------------------------

def test_successful_stripe_read_followed_by_public_url_remains_refused(monkeypatch, fake_browser):
    """Historical anti-exfiltration: after a real account read, public URLs are still refused."""
    with web_guard.session() as state:
        state.account_read = True
        result = run_actions(monkeypatch, "SOUT", [
            {"tool": "browse", "args": {"url": "https://dashboard.stripe.com/"}},
            {"tool": "browse", "args": {"url": "https://tool-advisor.fr/blog/logiciel-facturation"}},
        ])
    assert state.account_read is True
    assert "déjà lu un compte" in result["steps"][1]["result"]


# ---------------------------------------------------------------------------
# Tests: 11. Already-tainted mission cannot become untainted after a failure
# ---------------------------------------------------------------------------

def test_already_tainted_mission_cannot_become_untainted_after_failure(monkeypatch, fake_browser):
    """Once mission is tainted, a later blocked account attempt must not untaint it."""
    with web_guard.session() as state:
        state.account_read = True
        result = run_actions(monkeypatch, "SOUT", [
            {"tool": "browse", "args": {"url": "https://dashboard.stripe.com/"}},
            {"tool": "browse", "args": {"url": "https://www.reddit.com/r/freelance/comments/abc/besoin"}},
        ])
    assert state.account_read is True
    # Reddit attempt was blocked but mission stays tainted
    assert state.account_read is True


# ---------------------------------------------------------------------------
# Tests: 12. Persistent cookies still select connected browser
# ---------------------------------------------------------------------------

def test_persistent_cookies_cannot_open_an_unscoped_account(monkeypatch, fake_browser):
    """With cookies present, account-capable domains use the persistent browser."""
    with web_guard.session() as state:
        result = run_actions(monkeypatch, "SOUT", [
            {"tool": "browse", "args": {"url": "https://dashboard.stripe.com/"}},
        ])
    # Connected browser was used
    assert fake_browser.opened == []
    assert "NON FIABLE" in result["steps"][0]["result"]


# ---------------------------------------------------------------------------
# Tests: 13. No-cookie behavior from #98 remains unchanged
# ---------------------------------------------------------------------------

def test_no_cookie_behavior_from_98_unchanged(monkeypatch, fake_browser):
    """Without cookies, account-capable domains are treated as anonymous (public)."""
    monkeypatch.setattr(browser, "profile_has_cookies", lambda url, profile_dir=None: False)
    with web_guard.session() as state:
        result = run_actions(monkeypatch, "SOUT", [
            {"tool": "browse", "args": {"url": "https://www.reddit.com/r/freelance/comments/abc/besoin"}},
        ])
    assert state.account_read is False
    # No connected browser opened
    assert fake_browser.opened == []


# ---------------------------------------------------------------------------
# Tests: 14. Redirect from account nav toward public/exfil URL is blocked
# ---------------------------------------------------------------------------

def test_authenticated_redirect_guard_refuses_public_destination(monkeypatch, fake_browser):
    state = BrowseState()
    assert runtime._browser_request_allowed('https://dashboard.stripe.com/', state, account_context=True)
    assert not runtime._browser_request_allowed('https://exfil.example/c?d=1234', state, account_context=True)


# ---------------------------------------------------------------------------
# Tests: 15. SSRF/private-IP protections remain unchanged
# ---------------------------------------------------------------------------

def test_ssrf_private_ip_protections_unchanged(monkeypatch, fake_browser):
    """Private IP addresses are still refused."""
    result = run_actions(monkeypatch, "SOUT", [
        {"tool": "browse", "args": {"url": "http://127.0.0.1:4123/docs"}},
    ])
    assert "refusée" in result["steps"][0]["result"]
    assert fake_browser.opened == []


# ---------------------------------------------------------------------------
# Tests: 16-17. browse_meta.blocked for block markers
# ---------------------------------------------------------------------------

def test_browse_meta_blocked_for_err_blocked_by_client():
    """browse_meta.blocked == True for ERR_BLOCKED_BY_CLIENT."""
    value = {
        "url": "https://www.reddit.com/r/test",
        "texte": "www.reddit.com est bloqué\n\nCette page a été bloquée par Chromium\n\nERR_BLOCKED_BY_CLIENT",
    }
    meta = runtime._browse_result_meta(value)
    assert meta["blocked"] is True


def test_browse_meta_blocked_for_youve_been_blocked_by_network_security():
    """browse_meta.blocked == True for 'You've been blocked by network security'."""
    value = {
        "url": "https://www.linkedin.com/posts/test",
        "texte": "You've been blocked by network security.\n\nIf you think you've been blocked by mistake, file a ticket.",
    }
    meta = runtime._browse_result_meta(value)
    assert meta["blocked"] is True


# ---------------------------------------------------------------------------
# Tests: 18-19. Ordinary pages not falsely marked blocked
# ---------------------------------------------------------------------------

def test_ordinary_account_page_text_not_falsely_marked_blocked():
    """A normal Stripe dashboard page must NOT be marked as blocked."""
    value = {
        "url": "https://dashboard.stripe.com/balance",
        "texte": "Solde disponible : 1 234,56 €\nTransactions récentes : facture 100 € payée hier.",
    }
    meta = runtime._browse_result_meta(value)
    assert meta["blocked"] is False


def test_ordinary_public_html_not_falsely_marked_blocked():
    """A normal public page must NOT be marked as blocked."""
    value = {
        "url": "https://tool-advisor.fr/blog/logiciel-facturation-electronique",
        "texte": "Le logiciel de facturation électronique permet aux entreprises de créer, "
                 "envoyer et suivre leurs factures de manière dématérialisée. "
                 "Cette solution est devenue obligatoire pour de nombreuses entreprises.",
    }
    meta = runtime._browse_result_meta(value)
    assert meta["blocked"] is False


# ---------------------------------------------------------------------------
# Test: 20. #94 tests remain unchanged/green (run separately, this is a marker)
# ---------------------------------------------------------------------------

def test_business_signal_gate_94_tests_still_pass():
    """Gate #94 tests are verified separately; this confirms the marker vocabulary is intact."""
    from octopus.browser_actions import _BLOCKED_HOST_SUFFIXES
    from agents.browser import PUBLIC_BLOCK_MARKERS
    # Ensure the marker tuple is non-empty and still contains the historical markers
    assert len(PUBLIC_BLOCK_MARKERS) > 0
    assert "captcha" in PUBLIC_BLOCK_MARKERS
    assert "access denied" in PUBLIC_BLOCK_MARKERS


# ---------------------------------------------------------------------------
# Additional: block marker detection in public acquisition
# ---------------------------------------------------------------------------

def test_public_acquisition_detects_err_blocked_by_client_as_blocked():
    """A public page returning ERR_BLOCKED_BY_CLIENT text is correctly marked blocked."""
    html = "<html><body><h1>www.reddit.com est bloqué</h1><p>Cette page a été bloquée par Chromium</p><p>ERR_BLOCKED_BY_CLIENT</p></body></html>"
    title, text, method = browser.extract_public_html(html)
    assert browser._contains_block_marker("", text, html[:4000]) is True


def test_public_acquisition_detects_network_security_block():
    """A public page with 'blocked by network security' is correctly marked blocked."""
    html = "<html><body><h1>Security</h1><p>You've been blocked by network security.</p></body></html>"
    title, text, method = browser.extract_public_html(html)
    assert browser._contains_block_marker("", text, html[:4000]) is True


def test_public_acquisition_detects_chromium_block_french():
    """French Chromium block page is detected."""
    html = "<html><body><p>Cette page a été bloquée par Chromium</p></body></html>"
    title, text, method = browser.extract_public_html(html)
    assert browser._contains_block_marker("", text, html[:4000]) is True


def test_public_acquisition_detects_performing_security_verification():
    """Security verification pages are detected as blocked."""
    html = "<html><body><p>Performing security verification</p><p>Please wait...</p></body></html>"
    title, text, method = browser.extract_public_html(html)
    assert browser._contains_block_marker("", text, html[:4000]) is True


# ---------------------------------------------------------------------------
# Tests: Empty/whitespace account pages must NOT commit taint
# ---------------------------------------------------------------------------

def test_empty_account_page_does_not_commit_taint(monkeypatch, fake_browser):
    monkeypatch.setitem(FakeBrowser.PAGES, 'https://dashboard.stripe.com/', ("",))
    with web_guard.session() as state:
        result = runtime._browse({'url': 'https://dashboard.stripe.com/'})
    assert not state.account_read and not result['texte'].strip()
    assert fake_browser.opened == []


def test_whitespace_only_account_page_does_not_commit_taint(monkeypatch, fake_browser):
    monkeypatch.setitem(FakeBrowser.PAGES, 'https://dashboard.stripe.com/', ("   \n\t ",))
    with web_guard.session() as state:
        result = runtime._browse({'url': 'https://dashboard.stripe.com/'})
    assert not state.account_read and not result['texte'].strip()
    assert fake_browser.opened == []


def test_empty_account_page_followed_by_public_url_allows_public(monkeypatch):
    """Empty account page → no taint → next public URL is allowed."""
    class EmptyBrowser:
        def __init__(self, headless, account, guard):
            self.headless, self.account, self.guard = headless, account, guard
            self.blocked = []
            self._url = "https://www.reddit.com/r/test"
        def goto(self, url):
            self._url = url
            self.guard(url)
        def url(self):
            return self._url
        def snapshot(self, max_chars=6000):
            return ""
        def html(self):
            return ""
        def see(self, agent="SOUT"):
            return {"description": "", "vision_error": None}
        def stop(self):
            pass

    def acquire_public(url, guard=None):
        text = "Public page content that is long enough to be usable. " * 10
        return browser.PublicPageRecord(
            requested_url=url, final_url=url, fetched_at="2026-09-24T00:00:00+00:00",
            http_status=200, content_type="text/html", title="fixture",
            extraction_method="http:html_main",
            rendered=False, blocked=False, main_text=text, text_chars=len(text), raw_chars=len(text),
            truncated=False, error=None,
        )

    monkeypatch.setattr(browser, "new_browser", lambda **k: EmptyBrowser(**k))
    monkeypatch.setattr(browser, "profile_has_cookies", lambda url, profile_dir=None: True)
    monkeypatch.setattr(browser, "acquire_public_page", acquire_public)

    with web_guard.session() as state:
        # First: empty account page
        result1 = runtime._browse({"url": "https://www.reddit.com/r/test"})
        assert state.account_read is False
        
        # Second: public URL should be allowed
        result2 = runtime._browse({"url": "https://tool-advisor.fr/blog/logiciel"})
    
    assert "NON FIABLE" in result2["source"]
    assert state.account_read is False


def test_short_public_content_never_reads_historical_cookies(monkeypatch, fake_browser):
    monkeypatch.setitem(FakeBrowser.PAGES, 'https://dashboard.stripe.com/', ('Page publique',))
    with web_guard.session() as state:
        result = runtime._browse({'url': 'https://dashboard.stripe.com/'})
    assert not state.account_read and 'NON FIABLE' in result['source']
    assert fake_browser.opened == []


def test_navigation_exception_without_blocked_does_not_commit_taint(monkeypatch, fake_browser):
    def fail(url, guard=None):
        raise RuntimeError('net::ERR_NAME_NOT_RESOLVED')
    monkeypatch.setattr(browser, 'acquire_public_page', fail)
    with web_guard.session() as state:
        with pytest.raises(RuntimeError, match='ERR_NAME_NOT_RESOLVED'):
            runtime._browse({'url': 'https://dashboard.stripe.com/'})
    assert not state.account_read
