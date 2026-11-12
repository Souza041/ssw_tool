from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_admin_dashboard_contains_dependency_health_components():
    template = (ROOT / "web/templates/admin_observabilidade.html").read_text(encoding="utf-8")
    for text in (
        "Dependências Externas",
        "<th>24h</th>",
        "<th>7 dias</th>",
        "Última verificação",
        "Dados desatualizados",
        "Incidentes recentes",
    ):
        assert text in template
    assert "@media" in template
    assert "var(--" in template


def test_detail_page_uses_existing_nexus_shell_and_sanitized_fields():
    template = (ROOT / "web/templates/health_dependency_detail.html").read_text(encoding="utf-8")
    assert "base.html" in template
    assert "Diagnóstico técnico sanitizado" in template
    assert "dependency.password" not in template
    assert "dependency.token" not in template
    assert "dependency.credentials" not in template


def test_existing_fastapi_app_mounts_health_center_router_once():
    source = (ROOT / "web/main.py").read_text(encoding="utf-8")
    assert source.count("include_router(health_center_router)") == 1
