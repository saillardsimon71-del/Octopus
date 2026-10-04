import json
from types import SimpleNamespace

import pytest

from agents import agent_browser, config, runtime, web_guard
from octopus import journal, mandates, resources


@pytest.mark.parametrize('service', ['TikTok', 'LinkedIn', 'Netlify', 'OVH Mail', 'X', 'YouTube'])
def test_simple_account_needs_only_service_and_optional_name(service):
    first = resources.add_account(service, actor='human')
    second = resources.add_account(service, label='Mail pro' if service == 'OVH Mail' else 'Mon compte', actor='human')
    assert first['key'] != second['key']
    assert first['business'] is None and 'businesses' not in first['web_account']
    assert first['label'] == service and second['label'] in ('Mail pro', 'Mon compte')
    assert first['web_account']['session_status'] == 'connection_required'
    assert first['access'] == 'none'
    assert first['locator'].startswith('https://') and first['web_account']['domains']
    assert not journal.query('SELECT * FROM operational_mandates')
    assert not journal.query('SELECT * FROM llm_calls')


def test_global_account_visible_and_usable_with_each_activity_mandate():
    resource = resources.add_account('LinkedIn', label='LinkedIn', actor='human')
    key = resource['key']
    resources.set_account_session(key, 'connected')
    for business in ('first', 'second'):
        with journal.run(business, 'fixture'):
            visible = runtime._resources_status({})['resources']
        assert key in [r['key'] for r in visible]
        assert not mandates.account_authority(business, key, 'read')
        mid = mandates.grant(business, 'Lecture', 'owned_account', ['read'], actor='human', resource_keys=[key])
        assert mandates.account_authority(business, key, 'read')['id'] == mid
        assert not mandates.account_authority(business, key, 'publish')
    assert resources.get(key)['business'] is None
    assert not mandates.account_authority('third', key, 'read')


def test_advanced_unknown_service_and_human_only_configuration():
    resource = resources.add_account('Autre', label='Service privé', actor='human',
        provider='Unknown', url='https://service.example/login', domains=['service.example'],
        verify_url='https://service.example/dashboard', authenticated_text='Déconnexion')
    assert resource['web_account']['verify_url'] == 'https://service.example/dashboard'
    with pytest.raises(PermissionError):
        resources.add_account('TikTok', actor='agent')
    with pytest.raises(resources.ResourceError):
        resources.add_account('Autre', actor='human', url='http://service.example/')
    with pytest.raises(resources.ResourceError, match='déjà utilisé'):
        resources.add_account('Netlify', actor='human', key=resource['key'])
    assert resources.get(resource['key'])['web_account']['provider'] == 'Unknown'


def test_backend_binary_shared_across_dataroots_without_install(monkeypatch, tmp_path):
    monkeypatch.delenv('OCTOPUS_AGENT_BROWSER', raising=False)
    monkeypatch.setattr(agent_browser.shutil, 'which', lambda _: None)
    monkeypatch.setattr(config, 'PROJECT_ROOT', tmp_path / 'code')
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path / 'activity/agents/data')
    binary = config.PROJECT_ROOT / 'agents/data/bin' / agent_browser.binary_name()
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'installed backend')
    assert agent_browser.find_binary() == str(binary)
    assert not agent_browser.install_dir().exists()
    monkeypatch.setenv('OCTOPUS_AGENT_BROWSER', str(tmp_path / 'missing'))
    assert agent_browser.find_binary() is None


@pytest.mark.parametrize('error,reason', [(agent_browser.BackendUnavailable, 'backend_unavailable'),
                                        (RuntimeError, 'backend_error'),
                                        (resources.ResourceError, 'backend_unavailable')])
def test_netlify_backend_failure_preserves_existing_profile_and_evidence(monkeypatch, error, reason):
    resources.configure_account('netlify', actor='human', provider='Netlify', label='Netlify SiteQuiVend',
        url='https://app.netlify.com/', domains=['app.netlify.com'], businesses=[])
    account = resources.get('netlify')['web_account']
    resources._write('netlify', {'web_account': json.dumps(dict(account, browser_kind='chrome_stable'))})
    verification = {'method': 'human_hint', 'state': 'authenticated'}
    resources.set_account_session('netlify', 'connected', verification=verification)
    profile = resources.account_profile('netlify')
    profile.mkdir(parents=True)
    sentinel = profile / 'session-sentinel'
    sentinel.write_bytes(b'preserved')
    monkeypatch.setattr(resources, '_launch_human_browser', lambda *a: pytest.fail('no reconnect'))
    monkeypatch.setattr(web_guard, 'GuardProxy', lambda guard: SimpleNamespace(
        start=lambda: SimpleNamespace(url='http://127.0.0.1:1', stop=lambda: None)))
    def missing(*args, **kwargs):
        raise error('never expose password-cookie-OTP')
    if error is resources.ResourceError:
        monkeypatch.setattr(resources, 'stable_chrome_executable', missing)
    else:
        monkeypatch.setattr(resources, 'account_browser_options', lambda _: {})
    assert resources.verify_account_connection('netlify', actor='human', session_factory=missing) is False
    result = resources.get('netlify')
    assert result['last_check_detail'] == reason
    assert result['web_account']['session_status'] == 'unavailable'
    assert result['web_account']['verification'] == verification
    assert not mandates.account_authority('first', 'netlify', 'read')
    assert resources.account_profile('netlify') == profile and sentinel.read_bytes() == b'preserved'
    assert 'never expose' not in str(journal.query('SELECT * FROM resources'))


@pytest.mark.parametrize('description', ['Billing', 'Retrait', 'Password', '2FA', 'Créer un compte', 'Transfer ownership'])
def test_global_resources_do_not_broaden_sensitive_permissions(description):
    resource = resources.add_account('Netlify', actor='human')
    resources.set_account_session(resource['key'], 'connected')
    mandates.grant('first', 'Actions', 'owned_account', ['read', 'edit', 'publish', 'contact'],
        actor='human', resource_keys=[resource['key']])
    assert not mandates.authorize({'id': -1, 'business': 'first', 'status': 'active', 'access': 'act'},
        'edit', description=description)['allowed']
