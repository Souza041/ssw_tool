"""Separação estrutural entre instruções sem código e ocorrências reais."""
import re
import unittest
from operations.op101.ocorrencias import OP101Ocorrencias

TEXTOS = (
    "Manifesto JOI140349-4 encerrado por nao ser possivel o cancelamento. "
    "Mensagem recebida do SEFAZ: 219 - Rejeicao: Circulacao do MDF-e verificada",
    "Manifesto 35261102141029000985580010000653671009190155 cancelado.",
    "Texto administrativo novo com os números 63, 64 e 73.",
)


def instrucao(texto=TEXTOS[0]):
    valores = ["10/11/26 22:14", "ROD", "JOI", "10/11/26 22:14", "teste", "", texto,
               "", "", "", "", "", "teste"]
    return "<r>" + "".join(f"<f{i}>{v}</f{i}>" for i,v in enumerate(valores)) + "</r>"


def oc(codigo="63", data="10/11/26 23:44"):
    return f"<r><f0>{data}</f0><f5>{codigo} - TESTE</f5></r>"


def ler(xml, **kwargs):
    return OP101Ocorrencias.listar_ocorrencias(xml, estrito=True, **kwargs)


class InstrucoesEstruturais(unittest.TestCase):
    def test_joi880685_encerramento_sefaz_nao_esconde_oc63(self):
        eventos = ler(oc() + oc("64", "10/11/26 22:28") + instrucao())
        self.assertEqual([e.codigo for e in eventos], ["63", "64"])
        self.assertEqual(eventos[0].data_hora, "10/11/26 23:44")

    def test_cancelamento_numerico_nao_esconde_oc64(self):
        self.assertEqual([e.codigo for e in ler(oc("64") + instrucao(TEXTOS[1]))], ["64"])

    def test_bhz617879_preserva_oc73_antiga(self):
        eventos = ler(oc("64") + instrucao(TEXTOS[1]) + oc("73", "09/11/26 23:55"))
        self.assertEqual([e.codigo for e in eventos], ["64", "73"])
        self.assertIsNotNone(OP101Ocorrencias.encontrar_ocorrencia(eventos, "73"))

    def test_diagnostico_separado_nao_cria_ocs(self):
        diagnostico = []
        eventos = ler("".join(instrucao(t) for t in TEXTOS) + oc(), diagnostico_instrucoes=diagnostico)
        self.assertEqual([e.codigo for e in eventos], ["63"])
        self.assertEqual(len(diagnostico), len(TEXTOS))
        self.assertTrue(all(d["tipo"] == "instrucao_sem_ocorrencia" for d in diagnostico))
        self.assertEqual(diagnostico[0]["texto"], TEXTOS[0])

    def test_instrucao_recente_nao_muda_data_da_oc(self):
        eventos = ler(instrucao().replace("22:14", "23:50") + oc())
        self.assertEqual(eventos[0].data_hora, "10/11/26 23:44")

    def test_so_instrucoes_continua_invalido(self):
        with self.assertRaises(ValueError):
            ler(instrucao())

    def test_codigos_preenchidos_ilegiveis_nao_viram_instrucao(self):
        for tag, codigos in ((5, ("OC63", "OC73 - TESTE", "?", "<b>73</b>", "<b></b>")),
                             (10, ("73", "84", "?", "<b></b>"))):
            for codigo in codigos:
                with self.subTest(tag=tag,codigo=codigo), self.assertRaises(ValueError):
                    ler(instrucao().replace(f"<f{tag}></f{tag}>", f"<f{tag}>{codigo}</f{tag}>") + oc())

    def test_campos_ausentes_duplicados_ou_truncados_bloqueiam(self):
        for indice in (0, 3, 5, 6, 10):
            padrao = rf"<f{indice}>.*?</f{indice}>"
            base = instrucao()
            tag = re.search(padrao, base).group(0)
            for xml in (re.sub(padrao, "", base), base.replace(tag, tag*2),
                        base.replace(f"</f{indice}>", "", 1)):
                with self.subTest(indice=indice, xml=xml), self.assertRaises(ValueError):
                    ler(xml + oc())

    def test_tags_aninhadas_ou_abertas_extras_bloqueiam(self):
        for xml in (instrucao().replace("<f5></f5>", "<f5><f8></f8></f5>"),
                    instrucao().replace("<f5></f5>", "<f5><f5></f5>")):
            with self.subTest(xml=xml), self.assertRaises(ValueError):
                ler(xml + oc())

    def test_data_invalida_divergente_ou_sem_texto_bloqueiam(self):
        for xml in (instrucao().replace("10/11/26 22:14", "DATA INVALIDA"),
                    instrucao().replace("<f3>10/11/26 22:14</f3>", "<f3>10/11/26 22:15</f3>"),
                    re.sub(r"<f6>.*?</f6>", "<f6></f6>", instrucao())):
            with self.subTest(xml=xml), self.assertRaises(ValueError):
                ler(xml + oc())

    def test_historico_truncado_bloqueia(self):
        with self.assertRaises(ValueError):
            ler(instrucao().replace("</r>", "") + oc())

    def test_todas_as_ocs_reais_sao_preservadas(self):
        for codigo in ("46", "63", "64", "73", "77"):
            with self.subTest(codigo=codigo):
                self.assertEqual([e.codigo for e in ler(instrucao() + oc(codigo))], [codigo])
