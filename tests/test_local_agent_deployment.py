from pathlib import Path

import pytest

from modules.local_agent.private_env import (
    PrivateEnvironmentError,
    load_private_environment,
)
from modules.local_agent.provisioning import (
    ProvisioningError,
    atomic_private_write,
    merge_env,
    provision_values,
)


ROOT = Path(__file__).resolve().parents[1]


def test_private_environment_must_be_outside_repository(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_LOCAL_AGENT_ENABLED", "sentinel")
    external = tmp_path / "local-agent.env"
    external.write_text("NEXUS_LOCAL_AGENT_ENABLED=false\n", encoding="utf-8")
    external.chmod(0o600)
    load_private_environment(external, project_root=ROOT)
    assert __import__("os").environ["NEXUS_LOCAL_AGENT_ENABLED"] == "false"

    synthetic_root = tmp_path / "checkout"
    synthetic_root.mkdir()
    inside = synthetic_root / "private-env-test.env"
    inside.write_text("NEXUS_LOCAL_AGENT_ENABLED=false\n", encoding="utf-8")
    inside.chmod(0o600)
    with pytest.raises(PrivateEnvironmentError, match="fora do repositório"):
        load_private_environment(inside, project_root=synthetic_root)

    unrelated = tmp_path / "unrelated.env"
    unrelated.write_text("OCORRENCIA_73_ENABLED=true\n", encoding="utf-8")
    unrelated.chmod(0o600)
    with pytest.raises(PrivateEnvironmentError, match="fora da allowlist"):
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
        assert "NEXUS_SSW_DELEGATION_ENABLED=false" in content


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
    assert "$apply -and $registertask" in windows
    assert "nexus_local_agent_ssw_tasks" in windows
    windows_diagnostic = (ROOT / "deploy/local_agent/diagnose_windows_agent.ps1").read_text(
        encoding="utf-8"
    ).casefold()
    assert "estado esperado antes da homologação --once" in windows_diagnostic


def test_ubuntu_diagnostic_uses_real_contract_and_sanitizes_supervisor_failure():
    diagnostic = (ROOT / "deploy/local_agent/diagnose_ubuntu.sh").read_text(
        encoding="utf-8"
    )
    assert 'PYTHON_BIN="${NEXUS_PYTHON_BIN:-/home/automacao/venv/bin/python}"' in diagnostic
    assert "supervisorctl status ssw_web" in diagnostic
    assert "supervisor_output" in diagnostic
    assert 'echo "$supervisor_output"' not in diagnostic
    assert "status nexus" not in diagnostic


def test_provisioning_is_idempotent_secure_and_ping_only(tmp_path, monkeypatch):
    generated = iter(("bootstrap-secret-" + "a" * 40, "agent-token-" + "b" * 40))
    monkeypatch.setattr("modules.local_agent.provisioning._new_secret", lambda: next(generated))
    initial = {
        "NEXUS_LOCAL_AGENT_ENABLED": "false",
        "NEXUS_LOCAL_AGENT_BOOTSTRAP_ENABLED": "false",
        "NEXUS_LOCAL_AGENT_TOKENS_JSON": "{}",
    }
    server, windows = provision_values(
        initial,
        agent_id="windows-corporativo-01",
        server_url="https://nexus.example.test",
    )
    repeated, repeated_windows = provision_values(
        {**initial, **server},
        agent_id="windows-corporativo-01",
        server_url="https://nexus.example.test",
    )
    assert repeated["NEXUS_LOCAL_AGENT_BOOTSTRAP_SECRET"] == server["NEXUS_LOCAL_AGENT_BOOTSTRAP_SECRET"]
    assert repeated_windows["NEXUS_LOCAL_AGENT_TOKEN"] == windows["NEXUS_LOCAL_AGENT_TOKEN"]
    assert server["NEXUS_LOCAL_AGENT_ALLOWED_TASKS"] == "system.ping"
    assert "system.ping" in server["NEXUS_LOCAL_AGENT_AGENT_TASKS_JSON"]
    assert server["NEXUS_LOCAL_AGENT_ENABLED"] == "false"
    assert server["NEXUS_LOCAL_AGENT_BOOTSTRAP_ENABLED"] == "false"

    destination = tmp_path / "private.env"
    atomic_private_write(destination, merge_env("# preserved\n", server))
    assert destination.read_text(encoding="utf-8").startswith("# preserved")
    if __import__("os").name != "nt":
        assert destination.stat().st_mode & 0o777 == 0o600


def test_provisioning_refuses_enabled_flags_and_unsafe_url():
    with pytest.raises(ProvisioningError, match="Desative"):
        provision_values(
            {"NEXUS_LOCAL_AGENT_ENABLED": "true"},
            agent_id="windows-corporativo-01",
            server_url="https://nexus.example.test",
        )
    with pytest.raises(ProvisioningError, match="HTTPS"):
        provision_values(
            {},
            agent_id="windows-corporativo-01",
            server_url="http://nexus.example.test",
        )


def test_executor_contains_no_arbitrary_shell_primitive():
    executor = (ROOT / "modules/local_agent/executor.py").read_text(encoding="utf-8")
    forbidden = ("subprocess", "os.system", "shell=true", "popen(", "eval(", "exec(")
    assert not any(item in executor.casefold() for item in forbidden)
