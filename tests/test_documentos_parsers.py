from pathlib import Path
from datetime import date, datetime
import tempfile
import unittest

from openpyxl import Workbook

from modules.documentos.parsers.op455 import iter_op455
from modules.documentos.parsers.op930 import iter_op930
from modules.documentos.parsers.portal import iter_portal


class DocumentParsersTest(unittest.TestCase):
    def test_op455_grouped_invoices_and_orders(self):
        content = (
            "0;RELATORIO;PERIODO;\n"
            "1;Serie/Numero CTRC;Serie/Numero CT-e;Tipo do Documento;Data de Emissao;"
            "Unidade Emissora;Unidade Receptora;CNPJ Pagador;Cliente Pagador;"
            "CNPJ Destinatario;Cliente Destinatario;UF do Destinatario;Numero da Nota Fiscal;"
            "Notas Fiscais;Numero dos Pedidos;Numero da Capa de Remessa;"
            "Numero do Pacote de Arquivamento;Compr. de Entrega Escaneado;"
            "Data do Escaneamento;Hora do Escaneamento;CTRC Origem;"
            "Rel de Comissao de Expedicao;Rel de Comissao de Recepcao;Chaves NF-es;"
            "Volumes;Volume Cliente/Shipment\n"
            "2;APU404986-1;123;REVERSA;02/06/26;APU;CWB;05117268000806;Whirlpool;"
            "12345678901234;Destinatario;PR;632014;1/632014,1/632012;"
            "254277739,65031004;CWB-1;MTZ-2;S;24/06/2026;08:06;;"
            "MAPA: 1026;MAPA: 1029;CHAVE1,CHAVE2;2/632014;SHIP1\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "op455.sswweb"
            path.write_text(content, encoding="latin1")
            document = next(iter_op455(path))

        self.assertEqual(document.ctrc, "404986")
        self.assertEqual(len(document.invoices), 2)
        self.assertEqual(document.order_numbers, ["254277739", "65031004"])
        self.assertTrue(document.scanned)

    def test_op930_occurrence(self):
        content = (
            "0;CTRC;SERIE_NOTA_FISCAL;NRO_NOTA_FISCAL;DIA_INCLUSAO_OCOR;"
            "HORA_INCLUSAO_OCOR;DATA_OCOR;HORA_OCOR;USUARIO_OCOR;COD_OCOR;"
            "DESCRICAO_OCOR;COMPLEMENTO_OCOR;DATA_ENTREGA;EMPR_OCOR;UNID_OCOR;"
            "CANCELADO;CNPJ_PAGADOR;NOME_PAGADOR\n"
            "1;APU404986-1;1;632014;10/06/26;09:20;03/06/26;23:59;user;93;"
            "SSWSCAN;ANEXADO;03/06/26;ROD;CWB;NAO;05117268000806;Whirlpool\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "op930.csv"
            path.write_text(content, encoding="latin1")
            occurrence = next(iter_op930(path))

        self.assertEqual(occurrence.occurrence_code, "93")
        self.assertEqual(occurrence.ctrc, "404986")
        self.assertEqual(occurrence.invoice_number, "632014")

    def test_portal_pending_document(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "portal.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append([
                "CNPJ Transportador", "CTRC", "Número", "Transporte", "Série",
                "Data Emissão", "Nome Destinatário", "CNPJ Destinatário",
                "Estado Destinatário", "Data Pendência", "Usuário Pendência", "Pendência",
            ])
            sheet.append([
                "02141029000119", "404986", "632014", "254277739", "1",
                date(2026, 6, 2), "Destinatário", "12345678901234",
                "PR", datetime(2026, 6, 10, 9, 20), "KARLA", "SEM ASSINATURA",
            ])
            workbook.save(path)
            workbook.close()
            document = next(iter_portal(path, "AGUARDANDO_SOLUCAO"))

        self.assertEqual(document.invoice_number, "632014")
        self.assertEqual(document.pending_reason, "SEM ASSINATURA")


if __name__ == "__main__":
    unittest.main()
