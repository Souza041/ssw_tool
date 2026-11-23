"""Inventário estático de dependências; não habilita nem executa automações."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AutomationDependency:
    identifier: str
    routes: tuple[str, ...]
    requires_ssw: bool
    delegated_to_windows: bool
    identity_mode: str
    additional_dependencies: tuple[str, ...] = ()


CATALOG = (
    AutomationDependency(
        "nexus.login",
        ("/login",),
        True,
        True,
        "employee_browser_session",
        ("interactive_companion", "recaptcha_v2_human_interaction"),
    ),
    AutomationDependency(
        "documentos.gce",
        ("/documentos/executar", "scripts.processar_documentos_diario"),
        True,
        True,
        "configured_windows_identity",
        ("portal_gce", "mysql"),
    ),
    AutomationDependency(
        "metricas.op455",
        ("/metricas", "/api/metricas/refresh"),
        True,
        True,
        "employee_browser_session",
        ("interactive_companion", "mysql_production_only"),
    ),
    AutomationDependency(
        "manual.ssw.reports",
        ("/op455", "/op150", "/op103", "/op488", "/op535", "/op101-comprovantes"),
        True,
        False,
        "employee_http_session",
    ),
    AutomationDependency(
        "manual.ssw.transactions",
        ("/op001", "/op001-transporte", "/debitos", "/incidentes-op930"),
        True,
        False,
        "employee_http_session",
    ),
    AutomationDependency(
        "documentos.lg",
        ("/documentos-lg",),
        True,
        False,
        "configured_ssw_identity",
        ("mysql",),
    ),
    AutomationDependency(
        "oc73",
        ("/ocorrencia-73",),
        True,
        False,
        "disabled",
        ("mysql",),
    ),
    AutomationDependency(
        "documentos.dashboard",
        ("/documentos", "/documentos/metricas"),
        False,
        False,
        "nexus_authorization_only",
        ("mysql",),
    ),
    AutomationDependency(
        "health_center",
        ("/admin/observabilidade", "/admin/health-center"),
        False,
        False,
        "nexus_admin_only",
        ("mysql",),
    ),
    AutomationDependency(
        "incidentes.history",
        ("/incidentes/consulta",),
        False,
        False,
        "nexus_authorization_only",
        ("mysql",),
    ),
    AutomationDependency(
        "carrier_lg.renamer",
        ("/renomeador",),
        False,
        False,
        "nexus_authorization_only",
        ("local_files",),
    ),
    AutomationDependency(
        "whirlpool.portal",
        ("/whirlpool",),
        False,
        False,
        "external_portal_identity",
        ("whirlpool_portal",),
    ),
    AutomationDependency(
        "lg_pod.portal",
        ("/lg-pod",),
        False,
        False,
        "external_portal_identity",
        ("lg_portal", "mysql"),
    ),
    AutomationDependency(
        "local_agent.ping",
        ("system.ping",),
        False,
        True,
        "agent_identity",
    ),
)


def ssw_dependent() -> tuple[AutomationDependency, ...]:
    return tuple(item for item in CATALOG if item.requires_ssw)


def ssw_independent() -> tuple[AutomationDependency, ...]:
    return tuple(item for item in CATALOG if not item.requires_ssw)

