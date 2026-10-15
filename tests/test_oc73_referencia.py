from modules.ocorrencia_73.referencia import (
    referencia_valida,
    ocorrencia_confirma_referencia,
)

REF = "OC73-A1B2C3D4E5F60718293A"


def test_referencia_global_valida():
    assert referencia_valida(REF)


def test_referencia_incompleta_bloqueada():
    assert not referencia_valida("OC73-123")


def test_referencia_minuscula_bloqueada():
    assert not referencia_valida(REF.lower())


def test_historico_com_referencia_minuscula_nao_confirma():
    assert not ocorrencia_confirma_referencia(
        {
            "codigo": "73",
            "complemento": f"BOT OC73 | REF:{REF.lower()}",
        },
        REF,
    )


def test_referencia_legada_bloqueada():
    assert not referencia_valida(None)


def test_confirmacao_exata():
    assert ocorrencia_confirma_referencia(
        {
            "codigo": "73",
            "complemento": f"BOT OC73 | REF:{REF}",
        },
        REF,
    )


def test_codigo_diferente_bloqueado():
    assert not ocorrencia_confirma_referencia(
        {
            "codigo": "63",
            "complemento": f"REF:{REF}",
        },
        REF,
    )


def test_referencia_sem_marcador_bloqueada():
    assert not ocorrencia_confirma_referencia(
        {
            "codigo": "73",
            "complemento": REF,
        },
        REF,
    )


def test_referencia_parcial_bloqueada():
    assert not ocorrencia_confirma_referencia(
        {
            "codigo": "73",
            "complemento": f"REF:{REF}999",
        },
        REF,
    )


def test_referencia_diferente_bloqueada():
    outra = "OC73-FFFFFFFFFFFFFFFFFFFF"

    assert not ocorrencia_confirma_referencia(
        {
            "codigo": "73",
            "complemento": f"REF:{outra}",
        },
        REF,
    )
