from operations.op101.ocorrencias import (
    OP101Ocorrencias,
)


HTML_HISTORICO = """
<xml id="xmlsr">
    <rs>
        <r>
            <f0>29/06/26 11:48</f0>
            <f1>ROD</f1>
            <f2>CWB</f2>
            <f3>29/06/26 11:48</f3>
            <f4>#u#cordova|8#/u#</f4>
            <f5>73 - ENTREGA SERA REALIZADA AMANHA</f5>
            <f6>TESTE (SMS)</f6>
            <f7></f7>
            <f8></f8>
            <f9></f9>
            <f10>96 - PREVISAO DE ENTREGA ATUALIZADA</f10>
            <f11></f11>
            <f12>cordova</f12>
        </r>

        <r>
            <f0>29/06/26 11:47</f0>
            <f1>ROD</f1>
            <f2>CWB</f2>
            <f3>29/06/26 11:47</f3>
            <f4>#u#cordova|8#/u#</f4>
            <f5>47 - CTE EMITIDO</f5>
            <f6>CT-e autorizado.</f6>
            <f7></f7>
            <f8></f8>
            <f9></f9>
            <f10>80 - DOCUMENTO DE TRANSPORTE EMITIDO</f10>
            <f11></f11>
            <f12>cordova</f12>
        </r>
    </rs>
</xml>
"""


def test_listar_ocorrencias():
    ocorrencias = (
        OP101Ocorrencias.listar_ocorrencias(
            HTML_HISTORICO
        )
    )

    assert len(ocorrencias) == 2

    primeira = ocorrencias[0]

    assert primeira.codigo == "73"
    assert (
        primeira.descricao
        == "ENTREGA SERA REALIZADA AMANHA"
    )
    assert primeira.data_hora == "29/06/26 11:48"
    assert primeira.unidade == "CWB"
    assert primeira.usuario == "cordova"
    assert primeira.complemento == "TESTE (SMS)"


def test_encontrar_ocorrencia_73():
    ocorrencias = (
        OP101Ocorrencias.listar_ocorrencias(
            HTML_HISTORICO
        )
    )

    encontrada = (
        OP101Ocorrencias.encontrar_ocorrencia(
            ocorrencias,
            73,
        )
    )

    assert encontrada is not None
    assert encontrada.codigo == "73"


def test_possui_ocorrencia_73():
    ocorrencias = (
        OP101Ocorrencias.listar_ocorrencias(
            HTML_HISTORICO
        )
    )

    assert (
        OP101Ocorrencias.possui_ocorrencia_73(
            ocorrencias
        )
        is True
    )


def test_nao_possui_ocorrencia_73():
    html = """
    <xml id="xmlsr">
        <rs>
            <r>
                <f0>06/08/26 10:00</f0>
                <f1>ROD</f1>
                <f2>JOI</f2>
                <f5>47 - CTE EMITIDO</f5>
                <f6>CT-e autorizado.</f6>
                <f12>bot</f12>
            </r>
        </rs>
    </xml>
    """

    ocorrencias = (
        OP101Ocorrencias.listar_ocorrencias(
            html
        )
    )

    assert (
        OP101Ocorrencias.possui_ocorrencia_73(
            ocorrencias
        )
        is False
    )