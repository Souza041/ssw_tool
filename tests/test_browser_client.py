from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from ssw.browser_client import BrowserSSWClient


def build(status=200):
    raw=Mock(status=status,url='https://tms.ssw.inf.br/bin/ssw0053',headers={'content-type':'text/html'},body=Mock(return_value=b'<form>'))
    fetch=Mock(return_value=raw)
    page=SimpleNamespace(url='https://tms.ssw.inf.br/bin/menu01',context=SimpleNamespace(request=SimpleNamespace(fetch=fetch)))
    return BrowserSSWClient(page,base_url='https://tms.ssw.inf.br',usuario='test',unidade='CWB'),fetch,raw


def test_open():
    client,fetch,raw=build(); assert client.open_option('455').text=='<form>'
    assert fetch.call_count==2 and raw.dispose.call_count==2
    assert fetch.call_args.kwargs['max_redirects']==0


def test_reject():
    client,fetch,_=build()
    with pytest.raises(ValueError): client.open_option('101')
    with pytest.raises(ValueError): client.post('/bin/ssw0230',{'act':'E1'})
    fetch.assert_not_called()


def test_redirect():
    client,fetch,raw=build(302)
    with pytest.raises(RuntimeError): client.open_option('455')
    fetch.assert_called_once(); raw.dispose.assert_called_once()
