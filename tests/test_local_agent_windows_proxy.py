from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from modules.local_agent import windows_app, windows_proxy, routes

@pytest.mark.parametrize('value,expected', [('proxy:3128','http://proxy:3128'), ('http=a:80;https=b:3128','http://b:3128')])
def test_proxy_selection(value, expected):
    assert windows_proxy.select_proxy(value) == expected

@pytest.mark.parametrize('value', ['http://user:pass@proxy:3128', 'socks://proxy:1080', 'http://proxy/path', 'http=a:80'])
def test_invalid_proxy(value):
    with pytest.raises((ValueError, RuntimeError)):
        windows_proxy.select_proxy(value)

@pytest.mark.parametrize('resolved', ['', 'http://proxy:3128'])
def test_pac_policy(monkeypatch, tmp_path, resolved):
    monkeypatch.setattr(windows_app, 'default_config_path', lambda: tmp_path/'config.json')
    monkeypatch.setattr(windows_proxy, 'resolve_pac', lambda url: resolved)
    monkeypatch.setenv('HTTPS_PROXY', 'http://other:80')
    with windows_app._proxy_session() as session:
        assert not session.trust_env
        assert session.proxies == ({'http':resolved,'https':resolved} if resolved else {})

def test_manual_override(monkeypatch, tmp_path):
    monkeypatch.setattr(windows_app, 'default_config_path', lambda: tmp_path/'config.json')
    (tmp_path/'nexus-proxy.txt').write_text('http://manual:3128')
    def fail(url):
        raise AssertionError('PAC should not run')
    monkeypatch.setattr(windows_proxy, 'resolve_pac', fail)
    with windows_app._proxy_session() as session:
        assert session.proxies['https'] == 'http://manual:3128'
        assert not session.trust_env

def test_pac_failure_does_not_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(windows_app, 'default_config_path', lambda: tmp_path/'config.json')
    def fail(url):
        raise RuntimeError('PAC unavailable')
    monkeypatch.setattr(windows_proxy, 'resolve_pac', fail)
    with pytest.raises(RuntimeError, match='PAC unavailable'):
        windows_app._proxy_session()

@pytest.mark.parametrize('client,admin,status', [(None,False,401),(object(),False,403)])
def test_manager_requires_admin(monkeypatch, client, admin, status):
    monkeypatch.setattr(routes, 'obter_client', lambda request: client)
    monkeypatch.setattr(routes, 'is_admin', lambda request: admin)
    request = SimpleNamespace(session={})
    for call in (lambda: routes.agent_manager(request), lambda: routes.agent_manager_decision(request,'ref','approve','csrf')):
        with pytest.raises(HTTPException) as error:
            call()
        assert error.value.status_code == status

def test_manager_rejects_invalid_csrf(monkeypatch):
    monkeypatch.setattr(routes, '_admin', lambda request: (object(),object()))
    with pytest.raises(HTTPException) as error:
        routes.agent_manager_decision(SimpleNamespace(session={'local_agent_csrf':'correct'}),'ref','approve','wrong')
    assert error.value.status_code == 403
