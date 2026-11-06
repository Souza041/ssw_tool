"""Reconhecimento estrito de instruções administrativas observadas na OP101."""
import unittest
from operations.op101.ocorrencias import OP101Ocorrencias

TEXTOS = (
    "DACTE conferido na saida do Manifesto CWB237315-7",
    "Manifesto CWB237298-3 cancelado.",
    "- OPC 034 MANIFESTO GRU151308-7 - DACTE CAPTURADO.",
)


def instrucao(texto):
    return (
        "<r><f0>05/11/26 21:32</f0><f1>ROD</f1><f2>CWB</f2>"
        "<f3>05/11/26 21:32</f3><f4>#u#teste|8#/u#</f4><f5></f5>"
        f"<f6>{texto}</f6><f7></f7><f8></f8><f9></f9>"
        "<f10></f10><f11></f11><f12>teste</f12></r>"
    )


def ocorrencia(codigo, hora="16:03"):
    return f"<r><f0>05/11/26 {hora}</f0><f5>{codigo} - TESTE</f5></r>"


def ler(xml):
    return OP101Ocorrencias.listar_ocorrencias(xml, estrito=True)


class InstrucoesManifesto(unittest.TestCase):
    def test_instrucao_posterior_nao_esconde_oc63(self):
        for texto in TEXTOS:
            with self.subTest(texto=texto):
                historico=ler(instrucao(texto)+ocorrencia("63"))
                self.assertEqual([x.codigo for x in historico],["63"])
                self.assertEqual(historico[0].data_hora,"05/11/26 16:03")

    def test_oc64_e_oc73_continuam_no_historico(self):
        for codigo in ("64","73"):
            with self.subTest(codigo=codigo):
                historico=ler(instrucao(TEXTOS[0])+ocorrencia(codigo,"22:00")+ocorrencia("63"))
                self.assertEqual([x.codigo for x in historico],[codigo,"63"])
                self.assertIsNotNone(OP101Ocorrencias.encontrar_ocorrencia(historico,codigo))

    def test_instrucao_sozinha_nao_libera_historico(self):
        with self.assertRaises(ValueError):
            ler(instrucao(TEXTOS[0]))

    def test_texto_desconhecido_nao_e_descartado(self):
        for texto in ("Texto livre sem classificação", "Manifesto CWB237298-3 cancelado. OUTRO TEXTO", "DACTE conferido"):
            with self.subTest(texto=texto), self.assertRaises(ValueError):
                ler(instrucao(texto)+ocorrencia("63"))

    def test_campos_nao_vazios_divergentes_invalidos_bloqueiam(self):
        base=instrucao(TEXTOS[0])
        casos=(
            base.replace("<f5></f5>","<f5>OC 73 - NÃO RECONHECIDA</f5>"),
            base.replace("<f10></f10>","<f10>84</f10>"),
            base.replace("<f3>05/11/26 21:32</f3>","<f3>05/11/26 21:31</f3>"),
            base.replace("05/11/26 21:32","DATA INVALIDA"),
        )
        for xml in casos:
            with self.subTest(xml=xml),self.assertRaises(ValueError):
                ler(xml+ocorrencia("63"))

    def test_tags_ausentes_truncadas_duplicadas_bloqueiam(self):
        base=instrucao(TEXTOS[0])
        for tag in ("f0","f3","f5","f6","f10"):
            import re
            for xml in (
                re.sub(rf"<{tag}>.*?</{tag}>","",base),
                base.replace(f"</{tag}>",""),
                base.replace(f"</{tag}>",f"</{tag}><{tag}></{tag}>"),
            ):
                with self.subTest(tag=tag),self.assertRaises(ValueError):
                    ler(xml+ocorrencia("63"))

    def test_ocorrencia_real_malformada_nao_e_ignorada(self):
        for xml in ("<r><f0>05/11/26 22:00</f0><f5>OC73 - TESTE</f5></r>",
                    "<r><f0>05/11/26 22:00</f0><f5>73 - TESTE</r>"):
            with self.assertRaises(ValueError):
                ler(instrucao(TEXTOS[0])+xml+ocorrencia("63"))


if __name__ == "__main__":
    unittest.main()
