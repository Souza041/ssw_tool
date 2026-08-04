from operations.op101.ocorrencias import (
    OP101Ocorrencias,
)


def test_extrair_dados_ctrc():
    html = """
    <input
        type=hidden
        name=seq_ctrc
        id=seq_ctrc
        value="9676763"
    >

    <input
        type=hidden
        name=local
        id=local
        value="Q"
    >

    <input
        type=hidden
        name=FAMILIA
        id=FAMILIA
        value="ROD"
    >
    """

    dados = OP101Ocorrencias.extrair_dados_ctrc(
        html
    )

    assert dados["seq_ctrc"] == "9676763"
    assert dados["local"] == "Q"
    assert dados["familia"] == "ROD"


def test_extrair_seq_ctrc_do_javascript():
    html = """
    <a
        href="#"
        onclick="
            ajaxEnvia(
                '',
                1,
                'ssw0767?act=ATALHO_IMP&seq_ctrc=1234567&FAMILIA=ROD'
            );
        "
    >
        DACTE
    </a>
    """

    dados = OP101Ocorrencias.extrair_dados_ctrc(
        html
    )

    assert dados["seq_ctrc"] == "1234567"
    assert dados["familia"] == "ROD"