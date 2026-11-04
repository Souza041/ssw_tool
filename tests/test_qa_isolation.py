import importlib
import secrets
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from web.qa_mode import qa_data_dir


def test_qa_exige_diretorios_privados_absolutos(monkeypatch, tmp_path):
    project = tmp_path / "source"
    project.mkdir()
    monkeypatch.setenv("SSW_QA_DATA_DIR", str(project))
    monkeypatch.setenv("WHIRLPOOL_DATA_DIR", str(project / "whirlpool"))
    with pytest.raises(RuntimeError):
        qa_data_dir(project)
    monkeypatch.setenv("SSW_QA_DATA_DIR", str(tmp_path / "qa"))
    monkeypatch.setenv("WHIRLPOOL_DATA_DIR", str(tmp_path / "other"))
    with pytest.raises(RuntimeError):
        qa_data_dir(project)


def test_qa_startup_e_rotas_operacionais_bloqueados(monkeypatch, tmp_path):
    qa_root = tmp_path / "qa"
    monkeypatch.setenv("SSW_QA_MODE", "true")
    monkeypatch.setenv("SSW_QA_DATA_DIR", str(qa_root))
    monkeypatch.setenv("WHIRLPOOL_DATA_DIR", str(qa_root / "whirlpool"))
    monkeypatch.setenv("SSW_SESSION_SECRET_KEY", secrets.token_urlsafe(48))
    monkeypatch.setenv("SSW_ENVIRONMENT", "test")
    sys.modules.pop("web.main", None)
    main = importlib.import_module("web.main")
    assert not hasattr(main, "iniciar_scheduler_ocorrencia_73")
    try:
        main.startup_event()
        assert not main.app.routes or all(
            getattr(route, "path", "") not in (
                "/login", "/op001", "/whirlpool/emails", "/documentos-lg",
            )
            for route in main.app.routes
        )
        async def forbidden_call_next(_request):
            pytest.fail("Handler operacional executado em QA")

        for path in (
            "/login", "/op001", "/api/metricas/refresh",
            "/whirlpool/emails/qualquer/portal-dry-run",
            "/downloads/qualquer", "/oc73/observabilidade", "/documentos-lg/executar",
        ):
            request = SimpleNamespace(url=SimpleNamespace(path=path))
            coroutine = main.bloquear_operacoes_qa(request, forbidden_call_next)
            try:
                coroutine.send(None)
            except StopIteration as completed:
                response = completed.value
            else:
                pytest.fail("Gate QA aguardou um handler operacional")
            assert response.status_code == 503
        assert all((qa_root / name).is_dir() for name in ("downloads", "uploads", "temp"))
    finally:
        sys.modules.pop("web.main", None)


def test_modo_normal_preserva_rotas_scheduler_e_downloads(monkeypatch):
    monkeypatch.setenv("SSW_QA_MODE", "false")
    monkeypatch.setenv("SSW_SESSION_SECRET_KEY", secrets.token_urlsafe(48))
    monkeypatch.setenv("SSW_ENVIRONMENT", "test")
    sys.modules.pop("web.main", None)
    try:
        main = importlib.import_module("web.main")
        paths = {getattr(route, "path", "") for route in main.app.routes}
        assert {"/login", "/op001", "/whirlpool/emails", "/documentos-lg"} <= paths
        assert callable(main.iniciar_scheduler_ocorrencia_73)
        downloads = next(route for route in main.app.routes if route.path == "/downloads")
        assert Path(downloads.app.directory) == main.BASE_DIR / "downloads"
    finally:
        sys.modules.pop("web.main", None)
