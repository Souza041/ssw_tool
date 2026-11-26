"""Resolve PAC through WinHTTP for the Nexus endpoint, never evaluate JS ourselves."""
import ctypes as C
from ctypes import wintypes as W
from urllib.parse import urlsplit
import os


def proxy_url(value):
    value = value.strip()
    if '://' not in value:
        value = 'http://' + value
    p = urlsplit(value)
    if (p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password
            or p.path not in ('', '/') or p.query or p.fragment or any(c.isspace() for c in value)):
        raise ValueError('Proxy Nexus inválido ou não suportado.')
    _ = p.port
    return value


def select_proxy(value):
    # WinHTTP may return protocol-specific mappings. Do not silently use DIRECT
    # as a fallback when the selected proxy is unavailable.
    entries = [x.strip() for x in value.split(';') if x.strip()]
    specific = [x.split('=', 1)[1] for x in entries if x.lower().startswith('https=')]
    generic = [x for x in entries if '=' not in x]
    choices = specific or generic
    if not choices:
        raise RuntimeError('PAC sem proxy HTTPS suportado.')
    return proxy_url(choices[0])


def resolve_pac(url):
    """None = no automatic policy; empty string = explicit DIRECT decision."""
    if os.name != 'nt':
        return None
    class IE(C.Structure):
        _fields_ = [('auto', W.BOOL), ('pac', C.c_void_p), ('proxy', C.c_void_p), ('bypass', C.c_void_p)]
    class Options(C.Structure):
        _fields_ = [('flags', W.DWORD), ('detect', W.DWORD), ('pac', W.LPCWSTR),
                    ('reserved', C.c_void_p), ('reserved_flags', W.DWORD), ('auto_logon', W.BOOL)]
    class Info(C.Structure):
        _fields_ = [('access', W.DWORD), ('proxy', C.c_void_p), ('bypass', C.c_void_p)]
    api = C.WinDLL('winhttp', use_last_error=True)
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.GlobalFree.argtypes = [C.c_void_p]
    kernel.GlobalFree.restype = C.c_void_p
    api.WinHttpGetIEProxyConfigForCurrentUser.argtypes = [C.POINTER(IE)]
    api.WinHttpGetIEProxyConfigForCurrentUser.restype = W.BOOL
    api.WinHttpOpen.argtypes = [W.LPCWSTR, W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD]
    api.WinHttpOpen.restype = C.c_void_p
    api.WinHttpCloseHandle.argtypes = [C.c_void_p]
    api.WinHttpGetProxyForUrl.argtypes = [C.c_void_p, W.LPCWSTR, C.POINTER(Options), C.POINTER(Info)]
    api.WinHttpGetProxyForUrl.restype = W.BOOL
    ie = IE()
    info = Info()
    handle = None
    try:
        if not api.WinHttpGetIEProxyConfigForCurrentUser(C.byref(ie)):
            raise RuntimeError('Não foi possível consultar a política de proxy Windows.')
        if not ie.pac and not ie.auto:
            return None
        pac = C.wstring_at(ie.pac) if ie.pac else None
        opts = Options(2 if pac else 1, 0 if pac else 3, pac, None, 0, False)
        handle = api.WinHttpOpen('NexusAgent', 1, None, None, 0)
        if not handle or not api.WinHttpGetProxyForUrl(handle, url, C.byref(opts), C.byref(info)):
            raise RuntimeError('Falha na resolução PAC/WPAD; consulte a TI ou configure proxy manual.')
        if info.access == 1:
            return ''
        if info.access != 3 or not info.proxy:
            raise RuntimeError('Resposta PAC não suportada.')
        return select_proxy(C.wstring_at(info.proxy))
    finally:
        if handle:
            api.WinHttpCloseHandle(handle)
        for ptr in (ie.pac, ie.proxy, ie.bypass, info.proxy, info.bypass):
            if ptr:
                kernel.GlobalFree(ptr)
