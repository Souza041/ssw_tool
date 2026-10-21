import pytest

from operations.op101.ocorrencias import OP101Ocorrencias


def linha(data, codigo):
    return (
        "<r>"
        f"<f0>{data}</f0>"
        f"<f5>{codigo}</f5>"
        "</r>"
    )


def interpretar(xml, *, estrito=True):
    return OP101Ocorrencias.listar_ocorrencias(
        xml,
        estrito=estrito,
    )


def test_historico_valido():
    xml = (
        linha("20/10/26 08:00", "47 - Em transito")
        + linha("20/10/26 09:00", "63 - Teste")
    )

    historico = interpretar(xml)

    assert [item.codigo for item in historico] == [
        "47",
        "63",
    ]


def test_codigo_inesperado_bloqueado():
    xml = (
        linha("20/10/26 09:00", "63 - Teste")
        + linha("20/10/26 10:00", "OC 77 - Teste")
    )

    with pytest.raises(ValueError):
        interpretar(xml)


def test_historico_vazio_bloqueado():
    with pytest.raises(ValueError):
        interpretar("")


def test_xml_truncado_bloqueado():
    xml = (
        linha("20/10/26 09:00", "63 - Teste")
        + "<r><f0>20/10/26 10:00</f0>"
        "<f5>77 - Teste</f5>"
    )

    with pytest.raises(ValueError):
        interpretar(xml)


def test_comportamento_legado_preservado():
    xml = (
        linha("20/10/26 09:00", "63 - Teste")
        + linha("20/10/26 10:00", "OC 77 - Teste")
    )

    historico = interpretar(xml, estrito=False)

    assert len(historico) == 1
    assert historico[0].codigo == "63"


def test_linha_sem_f5_bloqueada():
    xml = (
        linha("20/10/26 09:00", "63 - Teste")
        + "<r><f0>20/10/26 10:00</f0></r>"
    )

    with pytest.raises(
        ValueError,
        match="registro sem codigo",
    ):
        interpretar(xml)

def test_campo_f5_truncado_nao_pode_autorizar():
    xml = (
        linha("20/10/26 09:00", "63 - Teste")
        + "<r><f0>20/10/26 10:00</f0>"
        "<f5>77 - Teste</r>"
    )

    with pytest.raises(ValueError):
        interpretar(xml)