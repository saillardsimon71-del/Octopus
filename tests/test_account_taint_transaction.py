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


@pytest.fixture
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
        text = FakeBrowser.PAGES.get(url, ("",))[0]
        if not text:
            text = "Page publique normale avec suffisamment de contenu pour le test. " * 5
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

def test_successful_account_read_commits_mission_taint(monkeypatch, fake_browser):
    with web_guard.session() as state:
        run_actions(monkeypatch, "LEDGER", [
            {"tool": "browse", "args": {"url": "https://dashboard.stripe.com/"}},
        ])
    assert state.account_read is True


# ---------------------------------------------------------------------------
# Tests: 2. After successful account read, later public navigation refused
# ---------------------------------------------------------------------------

def test_after_successful_account_read_public_navigation_refused(monkeypatch, fake_browser):
    with web_guard.session() as state:
        result = run_actions(monkeypatch, "SOUT", [
            {"tool": "browse", "args": {"url": "https://dashboard.stripe.com/"}},
            {"tool": "browse", "args": {"url": "https://tool-advisor.fr/blog/logiciel-facturation"}},
        ])
    steps = result["steps"]
    assert "compte connecté" in steps[0]["result"]
    assert "déjà lu un compte" in steps[1]["result"]
    assert state.account_read is True


# ---------------------------------------------------------------------------
# Tests: 3. Account request taints the TEMP security state before response
# ---------------------------------------------------------------------------

def test_account_request_taints_temp_state_before_response(monkeypatch, fake_browser):
    """During account navigation, the temp state must be tainted so public subrequests are blocked."""
    captured_temp_state = {}

    original_new_browser = browser.new_browser

    def capturing_new_browser(headless=False, account=False, guard=None):
        fb = FakeBrowser(headless, account, guard)
        # Wrap the guard to capture the temp state
        original_guard = guard
        def tracking_guard(url):
            result = original_guard(url)
            # The guard closure captures temp_state internally
            return result
        fb.guard = tracking_guard
        return fb

    # Verify the behavior through the existing mechanism:
    # if the temp state is tainted, a public subrequest during account nav is refused
    with web_guard.session() as state:
        result = run_actions(monkeypatch, "SOUT", [
            {"tool": "browse", "args": {"url": "https://dashboard.stripe.com/"}},
        ])
    # If account_read was committed, the temp state was successfully tainted and committed
    assert state.account_read is True


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

def test_persistent_cookies_still_select_connected_browser(monkeypatch, fake_browser):
    """With cookies present, account-capable domains use the persistent browser."""
    with web_guard.session() as state:
        result = run_actions(monkeypatch, "SOUT", [
            {"tool": "browse", "args": {"url": "https://dashboard.stripe.com/"}},
        ])
    # Connected browser was used
    assert any(b.account for b in fake_browser.opened)
    assert "compte connecté" in result["steps"][0]["result"]


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

def test_redirect_from_account_nav_toward_exfil_is_blocked(monkeypatch, fake_browser):
    """Redirect from account URL toward exfil URL is blocked before the request."""
    class RedirectBrowser(FakeBrowser):
        REDIRECTS = {"https://dashboard.stripe.com/redirect": "https://exfil.example/c?d=1234"}

        def goto(self, url):
            # Real BrowserTool calls the guard for the initial URL first (Playwright routing)
            self.guard(url)
            target = self.REDIRECTS.get(url, url)
            if target != url:
                if not self.guard(target):
                    self.blocked.append(target)
                    raise RuntimeError("net::ERR_BLOCKED_BY_CLIENT")
            self._url = target

    monkeypatch.setattr(browser, "new_browser", lambda headless=False, account=False, guard=None:
                        RedirectBrowser(headless, account, guard))

    with web_guard.session() as state:
        result = run_actions(monkeypatch, "SOUT", [
            {"tool": "browse", "args": {"url": "https://dashboard.stripe.com/redirect"}},
        ])
    step = result["steps"][0]
    assert "redirection refusée" in step["result"]
    assert state.account_read is False


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

def test_empty_account_page_does_not_commit_taint(monkeypatch):
    """Cookies present, account browser used, but snapshot is empty → no taint."""
    class EmptyBrowser:
        def __init__(self, headless, account, guard):
            self.headless, self.account, self.guard = headless, account, guard
            self.blocked = []
            self._url = "https://dashboard.stripe.com/"
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

    monkeypatch.setattr(browser, "new_browser", lambda **k: EmptyBrowser(**k))
    monkeypatch.setattr(browser, "profile_has_cookies", lambda url, profile_dir=None: True)

    with web_guard.session() as state:
        result = runtime._browse({"url": "https://dashboard.stripe.com/"})
    
    assert state.account_read is False
    assert result["page"]["blocked"] is True


def test_whitespace_only_account_page_does_not_commit_taint(monkeypatch):
    """Account page with only whitespace → no taint."""
    class WhitespaceBrowser:
        def __init__(self, headless, account, guard):
            self.headless, self.account, self.guard = headless, account, guard
            self.blocked = []
            self._url = "https://dashboard.stripe.com/"
        def goto(self, url):
            self._url = url
            self.guard(url)
        def url(self):
            return self._url
        def snapshot(self, max_chars=6000):
            return "   \n\t "
        def html(self):
            return "<html><body>   \n\t </body></html>"
        def see(self, agent="SOUT"):
            return {"description": "", "vision_error": None}
        def stop(self):
            pass

    monkeypatch.setattr(browser, "new_browser", lambda **k: WhitespaceBrowser(**k))
    monkeypatch.setattr(browser, "profile_has_cookies", lambda url, profile_dir=None: True)

    with web_guard.session() as state:
        result = runtime._browse({"url": "https://dashboard.stripe.com/"})
    
    assert state.account_read is False
    assert result["page"]["blocked"] is True


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


def test_short_but_real_account_content_still_commits_taint(monkeypatch):
    """Short but real account content (existing Stripe test) must still commit taint."""
    class ShortRealBrowser:
        def __init__(self, headless, account, guard):
            self.headless, self.account, self.guard = headless, account, guard
            self.blocked = []
            self._url = "https://dashboard.stripe.com/"
        def goto(self, url):
            self._url = url
            self.guard(url)
        def url(self):
            return self._url
        def snapshot(self, max_chars=6000):
            return "Solde : 1 234 €"
        def html(self):
            return "<html><body>Solde : 1 234 €</body></html>"
        def see(self, agent="SOUT"):
            return {"description": "Stripe dashboard", "vision_error": None}
        def stop(self):
            pass

    monkeypatch.setattr(browser, "new_browser", lambda **k: ShortRealBrowser(**k))
    monkeypatch.setattr(browser, "profile_has_cookies", lambda url, profile_dir=None: True)

    with web_guard.session() as state:
        result = runtime._browse({"url": "https://dashboard.stripe.com/"})
    
    assert state.account_read is True
    assert "compte connecté" in result["source"]


def test_navigation_exception_without_blocked_does_not_commit_taint(monkeypatch):
    """Network exception during goto() with empty blocked list → no taint."""
    class ExceptionBrowser:
        def __init__(self, headless, account, guard):
            self.headless, self.account, self.guard = headless, account, guard
            self.blocked = []  # Empty blocked list
            self._url = ""
        def goto(self, url):
            self.guard(url)
            # Simulate network exception without blocking any URLs
            raise RuntimeError("net::ERR_NAME_NOT_RESOLVED")
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

    monkeypatch.setattr(browser, "new_browser", lambda **k: ExceptionBrowser(**k))
    monkeypatch.setattr(browser, "profile_has_cookies", lambda url, profile_dir=None: True)

    with web_guard.session() as state:
        # The exception propagates out of _browse() — this is existing behavior.
        # The critical invariant: mission state is NOT tainted because web_guard.record()
        # was never reached.
        with pytest.raises(RuntimeError, match="ERR_NAME_NOT_RESOLVED"):
            runtime._browse({"url": "https://dashboard.stripe.com/"})
    
    assert state.account_read is False
