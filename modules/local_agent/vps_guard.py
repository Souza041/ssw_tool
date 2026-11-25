"""Bloqueio opt-in de operações SSW que ainda dependem de sessão interativa."""

from modules.local_agent.delegation import delegation_enabled


VPS_SSW_DIRECT_POSTS = frozenset({
    "/op455", "/op150", "/op103", "/op488", "/op535", "/debitos",
    "/op001", "/op001-transporte", "/op101-comprovantes",
    "/incidentes-op930", "/documentos-lg/executar",
})

DELEGATED_ROUTE_TASKS = {
    "/op001": "ssw.op001.nfd",
    "/op001-transporte": "ssw.op001.transporte",
}


def direct_ssw_post_blocked(method: str, path: str) -> bool:
    delegated_task = DELEGATED_ROUTE_TASKS.get(path)
    if delegated_task and delegation_enabled(delegated_task):
        return False
    return (
        str(method).upper() == "POST"
        and path in VPS_SSW_DIRECT_POSTS
        and delegation_enabled()
    )
