"""Regressões dos históricos CWB493612 e BHZ615193, sem rede ou MySQL."""
import re
import unittest

from operations.op101.ocorrencias import OP101Ocorrencias

CAPTURA = (
    "SSWBAR - Volume NR 000802279749 (01/01) CAPTURADO EM CARREGAMENTO "
    "DE MANIFESTO PLACA PROV AUJ0915"
)
ETIQUETAS = "Etiquetas de identifica&ccedil;&atilde;o de volumes foram reimpressas"


def instrucao(texto=CAPTURA, data="07/11/26 13:57"):
    return (
        f"<r><f0>{data}</f0><f1>ROD</f1><f2>CWB</f2><f3>{data}</f3>"
        f"<f4>#u#teste|8#/u#</f4><f5></f5><f6>{texto}</f6>"
        "<f7><!--Detalhe--><a href=# class=sra "
        "onclick=showmsg('Detalhe:&nbsp;Unidade&nbsp;destino&nbsp;do&nbsp;CTRC&nbsp;"
        "n&amp;atilde;o&nbsp;est&amp;aacute;&nbsp;nos&nbsp;filtros.&nbsp;Unidades:&nbsp;FOZ.'"
        ".replace(/&nbsp;/g,String.fromCharCode(32)),0);return(false);>"
        "<u>Detalhe</u></a></f7><f8></f8><f9></f9><f10></f10>"
        "<f11></f11><f12>teste</f12></r>"
    )


def oc(codigo, data="09/11/26 16:03"):
    return f"<r><f0>{data}</f0><f5>{codigo} - TESTE</f5></r>"


def ler(xml):
    return OP101Ocorrencias.listar_ocorrencias(xml, estrito=True)


class InstrucoesVolumes(unittest.TestCase):
    def test_cwb493612_com_historico_completo_preserva_oc64(self):
        xml = (instrucao("Manifesto JOI140309-5 cancelado.", "09/11/26 23:30")
               + oc("64")
               + instrucao("DACTE conferido na saida do Manifesto CWB237387-4")
               + instrucao(CAPTURA)
               + instrucao("GERADO COM DADOS IMPORTADOS ATRAVES DA OPCAO 006 (VIA EDI) (U)."))
        self.assertEqual([r.codigo for r in ler(xml)], ["64"])

    def test_bhz615193_com_historico_completo_preserva_oc63(self):
        xml = (instrucao(ETIQUETAS, "09/11/26 07:35") + oc("63")
               + instrucao("Manifesto GRU151360-5 cancelado.")
               + instrucao("- OPC 034 MANIFESTO GRU151360-5 - DACTE CAPTURADO.")
               + instrucao("CTRC APONTADO PARA MANIFESTO NA UNIDADE: EXT. PLACA: BYG5215."))
        self.assertEqual([r.codigo for r in ler(xml)], ["63"])

    def test_instrucao_mais_recente_nao_troca_data_da_oc(self):
        for texto in (CAPTURA, ETIQUETAS):
            with self.subTest(texto=texto):
                eventos = ler(instrucao(texto, "09/11/26 23:30") + oc("64"))
                self.assertEqual(len(eventos), 1)
                self.assertEqual(eventos[0].data_hora, "09/11/26 16:03")

    def test_oc73_sempre_preservada_para_bloquear_duplicidade(self):
        for texto in (CAPTURA, ETIQUETAS):
            with self.subTest(texto=texto):
                eventos = ler(instrucao(texto) + oc("64") + oc("73", "08/11/26 23:55"))
                self.assertEqual([r.codigo for r in eventos], ["64", "73"])
                self.assertIsNotNone(OP101Ocorrencias.encontrar_ocorrencia(eventos, "73"))

    def test_oc_real_com_complemento_administrativo_nao_e_ignorada(self):
        for texto in (CAPTURA, ETIQUETAS):
            with self.subTest(texto=texto):
                xml = instrucao(texto).replace("<f5></f5>", "<f5>73 - ENTREGA AMANHA</f5>")
                self.assertEqual([r.codigo for r in ler(xml)], ["73"])

    def test_todas_as_ocs_reais_continuam_no_historico(self):
        for codigo in ("46", "63", "64", "73", "77"):
            with self.subTest(codigo=codigo):
                self.assertEqual([r.codigo for r in ler(instrucao() + oc(codigo))], [codigo])

    def test_unicode_e_mercosul(self):
        for texto in ("Etiquetas de identificação de volumes foram reimpressas",
                      CAPTURA.replace("AUJ0915", "AUJ0A15")):
            with self.subTest(texto=texto):
                self.assertEqual([r.codigo for r in ler(instrucao(texto) + oc("63"))], ["63"])

    def test_apenas_instrucoes_continua_bloqueado(self):
        with self.assertRaisesRegex(ValueError, "vazio ou sem ocorrencias"):
            ler(instrucao(CAPTURA) + instrucao(ETIQUETAS))

    def test_textos_genericos_e_variantes_nao_observadas_bloqueiam(self):
        for texto in ("SSWBAR", "Etiquetas reimpressas", CAPTURA + " OUTRA INFORMACAO",
                      ETIQUETAS + " CANCELADO", CAPTURA.replace("000802279749", "INVALIDO"),
                      CAPTURA.replace("AUJ0915", "INVALIDA")):
            with self.subTest(texto=texto), self.assertRaises(ValueError):
                ler(instrucao(texto) + oc("64"))

    def test_campos_divergentes_ou_invalidos_bloqueiam(self):
        for texto in (CAPTURA, ETIQUETAS):
            base = instrucao(texto)
            for xml in (base.replace("<f5></f5>", "<f5>OC 73 desconhecida</f5>"),
                        base.replace("<f10></f10>", "<f10>84</f10>"),
                        base.replace("<f3>07/11/26 13:57</f3>", "<f3>07/11/26 13:58</f3>"),
                        base.replace("07/11/26 13:57", "DATA INVALIDA")):
                with self.subTest(xml=xml), self.assertRaises(ValueError):
                    ler(xml + oc("64"))

    def test_tags_ausentes_duplicadas_ou_truncadas_bloqueiam(self):
        for texto in (CAPTURA, ETIQUETAS):
            base = instrucao(texto)
            for indice in (0, 3, 5, 6, 10):
                tag = rf"<f{indice}>.*?</f{indice}>"
                original = re.search(tag, base).group(0)
                for xml in (re.sub(tag, "", base), base.replace(original, original * 2),
                            base.replace(f"</f{indice}>", "", 1)):
                    with self.subTest(xml=xml), self.assertRaises(ValueError):
                        ler(xml + oc("64"))

    def test_xml_truncado_continua_bloqueado(self):
        with self.assertRaises(ValueError):
            ler(instrucao().replace("</r>", "") + oc("64"))


if __name__ == "__main__":
    unittest.main()
