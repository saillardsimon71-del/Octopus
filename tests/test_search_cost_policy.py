import pytest

from agents import search


@pytest.mark.parametrize("provider,key_name,cost_env", [
    ("brave", "BRAVE_API_KEY", "OCTOPUS_SEARCH_BRAVE_COST_CLASS"),
    ("tavily", "TAVILY_API_KEY", "OCTOPUS_SEARCH_TAVILY_COST_CLASS"),
])
def test_search_api_requires_explicit_free_quota(monkeypatch, provider, key_name, cost_env):
    monkeypatch.setattr(search.config, key_name, "secret")
    monkeypatch.delenv(cost_env, raising=False)
    monkeypatch.setattr(search.requests, "get", lambda *a, **k: pytest.fail("aucun appel réseau attendu"))
    monkeypatch.setattr(search.requests, "post", lambda *a, **k: pytest.fail("aucun appel réseau attendu"))

    with pytest.raises(RuntimeError, match="cost class"):
        getattr(search, f"_{provider}_items")("test", 1)


@pytest.mark.parametrize("provider,key_name,cost_env", [
    ("brave", "BRAVE_API_KEY", "OCTOPUS_SEARCH_BRAVE_COST_CLASS"),
    ("tavily", "TAVILY_API_KEY", "OCTOPUS_SEARCH_TAVILY_COST_CLASS"),
])
def test_search_api_paid_class_is_blocked(monkeypatch, provider, key_name, cost_env):
    monkeypatch.setattr(search.config, key_name, "secret")
    monkeypatch.setenv(cost_env, "paid")
    monkeypatch.setattr(search.requests, "get", lambda *a, **k: pytest.fail("aucun appel réseau attendu"))
    monkeypatch.setattr(search.requests, "post", lambda *a, **k: pytest.fail("aucun appel réseau attendu"))
    with pytest.raises(RuntimeError, match="paid.*bloqué"):
        getattr(search, f"_{provider}_items")("test", 1)
