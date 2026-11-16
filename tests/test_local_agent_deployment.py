from pathlib import Path

import pytest

from modules.local_agent.private_env import (
    PrivateEnvironmentError,
    load_private_environment,
)


ROOT = Path(__file__).resolve().parents[1]


def test_private_environment_must_be_outside_repository(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ENABLED", "sentinel")
    external = tmp_path / "local-agent.env"
    external.write_text("NEXUS_LOCAL_AGENT_ENABLED=false\n", encoding="utf-8")
    load_private_environment(external, project_root=ROOT)
    assert __import__("os").environ["NEXUS_LOCAL_AGENT_ENABLED"] == "false"

    synthetic_root = tmp_path / "checkout"
    synthetic_root.mkdir()
    inside = synthetic_root / "private-env-test.env"
    inside.write_text("NEXUS_LOCAL_AGENT_ENABLED=false\n", encoding="utf-8")
    with pytest.raises(PrivateEnvironmentError, match="fora do repositório"):
        load_private_environment(inside, project_root=synthetic_root)

    unrelated = tmp_path / "unrelated.env"
    unrelated.write_text("OCORRENCIA_73_ENABLED=true\n", encoding="utf-8")
    with pytest.raises(PrivateEnvironmentError, match="somente variáveis"):
        load_private_environment(unrelated, project_root=ROOT)


def test_deployment_templates_keep_both_gates_disabled():
    ubuntu = (ROOT / "deploy/local_agent/local-agent.env.example").read_text(
        encoding="utf-8"
    )
    project = (ROOT / ".env.example").read_text(encoding="utf-8")
    for content in (ubuntu, project):
        assert "NEXUS_LOCAL_AGENT_ENABLED=false" in content
        assert "NEXUS_LOCAL_AGENT_BOOTSTRAP_ENABLED=false" in content
        assert "NEXUS_LOCAL_AGENT_DB=/var/lib/nexus/local-agent.sqlite3" in content


def test_installers_do_not_restart_or_start_services_implicitly():
    ubuntu = (ROOT / "deploy/local_agent/install_ubuntu.sh").read_text(
        encoding="utf-8"
    ).casefold()
    windows = (ROOT / "deploy/local_agent/install_windows_agent.ps1").read_text(
        encoding="utf-8"
    ).casefold()
    assert "supervisorctl restart" not in ubuntu
    assert "supervisorctl update" not in ubuntu
    assert "start-scheduledtask" not in windows
    assert "-restartcount 999" in windows
    assert "s-1-5-19" in windows


def test_executor_contains_no_arbitrary_shell_primitive():
    executor = (ROOT / "modules/local_agent/executor.py").read_text(encoding="utf-8")
    forbidden = ("subprocess", "os.system", "shell=true", "popen(", "eval(", "exec(")
    assert not any(item in executor.casefold() for item in forbidden)
