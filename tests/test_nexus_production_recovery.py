from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from modules.local_agent.repository import LocalAgentRepository
from modules.local_agent.ssw_tasks import (
    TASK_OP001_NFD,
    TASK_OP001_TRANSPORTE,
    validate_ssw_payload,
)
from ssw.browser_transport import (
    BrowserIdentity,
    BrowserSessionTransport,
    SSWUncertainResultError,
)


IDENTITY = {
    "dominio": "ROD",
    "usuario": "employee.one",
    "unidade": "BIG",
    "session_binding": "s" * 48,
}


def test_op001_payload_preserves_original_workbook_and_authenticated_unit(monkeypatch):
    monkeypatch.setenv("NEXUS_OP001_MAX_UPLOAD_BYTES", "1024")
    content = b"synthetic workbook"
    clean = validate_ssw_payload(TASK_OP001_NFD, {
        "filename": "../entrada.xlsx",
        "workbook_base64": base64.b64encode(content).decode("ascii"),
        **IDENTITY,
    })
    assert clean["filename"] == "entrada.xlsx"
    assert base64.b64decode(clean["workbook_base64"]) == content
    assert clean["unidade"] == "BIG"


def test_op001_rejects_extra_filters_or_oversized_payload(monkeypatch):
    monkeypatch.setenv("NEXUS_OP001_MAX_UPLOAD_BYTES", "4")
    with pytest.raises(ValueError):
        validate_ssw_payload(TASK_OP001_TRANSPORTE, {
            "filename": "entrada.xlsx",
            "workbook_base64": base64.b64encode(b"12345").decode("ascii"),
            "op455_filter": "CWB",
            **IDENTITY,
        })


def test_repository_affinity_prevents_another_windows_agent_from_claiming(tmp_path):
    repository = LocalAgentRepository(tmp_path / "agent.sqlite3")
    job_id = repository.enqueue(
        task=TASK_OP001_NFD,
        payload={"safe": True},
        max_attempts=1,
        concurrency_key="ssw.session:test",
        target_agent_id="windows-one",
    )
    assert repository.claim(
        agent_id="windows-two", capabilities={TASK_OP001_NFD}, lease_seconds=60,
    ) is None
    claimed = repository.claim(
        agent_id="windows-one", capabilities={TASK_OP001_NFD}, lease_seconds=60,
    )
    assert claimed["id"] == job_id
    assert claimed["max_attempts"] == 1


def test_equal_users_with_distinct_bindings_have_isolated_operation_locks():
    first = BrowserIdentity.checked("ROD", "employee.one", "a" * 48)
    second = BrowserIdentity.checked("ROD", "employee.one", "b" * 48)
    assert first != second

    def worker(identity):
        return SimpleNamespace(
            identity=identity,
            base_url="https://tms.ssw.inf.br",
            observed_unit="CWB",
            valid=True,
            operations=__import__(
                "ssw.browser_transport", fromlist=["_OperationCoordinator"]
            )._OperationCoordinator(),
        )

    first_transport = BrowserSessionTransport(worker(first))
    second_transport = BrowserSessionTransport(worker(second))
    first_lease = first_transport.acquire_operation("OP001")
    second_lease = second_transport.acquire_operation("Documentos GCE")
    first_lease.release()
    second_lease.release()


def test_nfd_batch_stops_after_uncertain_write(monkeypatch, tmp_path):
    from operations.op001.batch import processar_planilha_nfd

    input_file = tmp_path / "entrada.xlsx"
    output_file = tmp_path / "saida.xlsx"
    pd.DataFrame([{"NFD": "1", "CNPJ": "123"}]).to_excel(input_file, index=False)

    class UncertainOP001:
        def salvar_coleta_nfd(self, **kwargs):
            raise SSWUncertainResultError("resultado incerto")

    with pytest.raises(SSWUncertainResultError):
        processar_planilha_nfd(
            op001=UncertainOP001(), input_file=input_file, output_file=output_file,
        )
    assert output_file.exists()
