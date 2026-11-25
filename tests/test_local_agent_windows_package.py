import os
from pathlib import Path

import pytest

from modules.local_agent.windows_credentials import protect_token, unprotect_token


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != "nt", reason="DPAPI é exclusivo do Windows")
def test_dpapi_roundtrip_does_not_store_plaintext():
    token = "individual-agent-token-with-more-than-thirty-two-characters"
    encrypted = protect_token(token)
    assert token not in encrypted
    assert unprotect_token(encrypted) == token


def test_packaging_is_per_user_hidden_and_contains_no_embedded_credentials():
    setup = (ROOT / "deploy/local_agent/NexusAgentSetup.iss").read_text(encoding="utf-8")
    spec = (ROOT / "deploy/local_agent/NexusAgent.spec").read_text(encoding="utf-8")
    build = (ROOT / "scripts/build_windows_agent.ps1").read_text(encoding="utf-8")
    combined = (setup + spec + build).casefold()
    assert "privilegesrequired=lowest" in combined
    assert "{userstartup}" in combined
    assert "console=false" in combined
    assert "playwright-browsers" in combined
    assert "nexus_local_agent_token=" not in combined
    assert "proxy_pass=" not in combined
    assert "ssw_password=" not in combined
