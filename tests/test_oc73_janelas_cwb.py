"""Validação offline: emissão D-3 e data efetiva da OC63, sem SSW/MySQL."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from modules.ocorrencia_73.calendario import calcular_janelas_oc73, AGENDAMENTOS_OC73
from modules.ocorrencia_73.parser import filtrar_registros
from modules.ocorrencia_73.datas_oc63 import motivo_bloqueio_data_oc63
from modules.ocorrencia_73 import service as servico
from operations.op101.ocorrencias import CTRCConsultado, OcorrenciaHistorico

D = date(2026, 11, 5)


def registro(data="05/11/26", codigo="63", emissora="GRU", cidade="CURITIBA"):
    return {
        "Serie/Numero CTRC": "CWB490132-1", "Unidade Emissora": emissora,
        "Cidade do Destinatario": cidade, "Codigo Ultima Ocorrencia": codigo,
        "Data de Emissao": "02/11/26", "Data da Ultima Ocorrencia": data,
        "UF do Expedidor": "SC",
    }


def filtrar(item, inicio=D, fim=D):
    motivos = []
    resultado = filtrar_registros(
        [item], fluxo="CWB", data_inicial_ocorrencia=inicio,
        data_final_ocorrencia=fim, diagnostico_datas=motivos,
    )
    return resultado, motivos


class JanelasCWB(unittest.TestCase):
    def test_d3_cruza_mes_e_ano(self):
        for dia, esperado in [(D, date(2026,11,2)), (date(2027,1,1),date(2026,12,29))]:
            with self.subTest(dia=dia):
                j = calcular_janelas_oc73(dia, fluxo="CWB")[0]
                self.assertEqual(j.data_inicial_emissao, esperado)
                self.assertEqual(j.data_inicial, dia)
                self.assertEqual(j.data_final, dia)

    def test_domingo_tem_janelas_independentes(self):
        j = calcular_janelas_oc73(date(2026,11,8), fluxo="CWB")[0]
        self.assertEqual(j.data_inicial_emissao, date(2026,11,5))
        self.assertEqual(j.data_inicial, date(2026,11,7))
        self.assertEqual(j.data_final, date(2026,11,8))

    def test_big_e_horarios_preservados(self):
        j = calcular_janelas_oc73(D, fluxo="BIG")[0]
        self.assertEqual(j.data_inicial_emissao, date(2026,11,2))
        self.assertEqual(j.data_inicial, D)
        self.assertTrue(all((j.hora,j.minuto)==(23,55) for j in AGENDAMENTOS_OC73))
        self.assertEqual(calcular_janelas_oc73(date(2026,11,7), fluxo="CWB"), [])

    def test_emissao_antiga_oc63_hoje_entra(self):
        for data in ("05/11/26", "05/11/2026", "2026-11-05"):
            with self.subTest(data=data):
                itens, motivos = filtrar(registro(data))
                self.assertEqual(len(itens),1)
                self.assertEqual(motivos,[])

    def test_oc63_antiga_futura_ou_invalida_bloqueia(self):
        for data in ("04/11/26", "06/11/26", "", None, "31/11/2026", "lixo"):
            with self.subTest(data=data):
                itens,motivos = filtrar(registro(data))
                self.assertEqual(itens,[])
                self.assertEqual(len(motivos),1)

    def test_coluna_ausente_ambigua_inclusao_nao_substitui_evento(self):
        for ambiguo in (False,True):
            item=registro();item["Data de inclusao da Ultima Ocorrencia"]="05/11/26"
            if ambiguo:
                item["Data da Última Ocorrência"]="05/11/26"
            else:
                del item["Data da Ultima Ocorrencia"]
            itens,motivos=filtrar(item)
            self.assertEqual(itens,[])
            self.assertEqual(motivos[0]["motivo"],"data_oc63_ausente_ou_invalida")

    def test_data_inclusao_hoje_nao_libera_evento_antigo(self):
        item=registro("04/11/26");item["Data de inclusao da Ultima Ocorrencia"]="05/11/26"
        self.assertEqual(filtrar(item)[0],[])

    def test_domingo_aceita_sabado_e_domingo_apenas(self):
        for data,quantidade in [("06/11/26",0),("07/11/26",1),("08/11/26",1),("09/11/26",0)]:
            with self.subTest(data=data):
                itens,_=filtrar(registro(data),date(2026,11,7),date(2026,11,8))
                self.assertEqual(len(itens),quantidade)

    def test_demais_filtros_continuam_obrigatorios(self):
        for item in (registro(codigo="64"),registro(emissora="POA"),registro(cidade="SAO PAULO")):
            self.assertEqual(filtrar(item)[0],[])

    def test_big_sem_coluna_data_continua_elegivel(self):
        item=registro(codigo="64");del item["Data da Ultima Ocorrencia"]
        self.assertEqual(len(filtrar_registros([item],fluxo="BIG")),1)

    def test_janela_incompleta_ou_invertida_bloqueia(self):
        for inicio,fim in [(D,None),(None,D),(date(2026,11,6),D)]:
            with self.assertRaises(ValueError):
                filtrar_registros([registro()],fluxo="CWB",
                    data_inicial_ocorrencia=inicio,data_final_ocorrencia=fim)

    def test_preparacao_envia_d3_ao455_e_ao101(self):
        with tempfile.TemporaryDirectory() as pasta:
            service=servico.Ocorrencia73Service(repository=Mock())
            report=Mock();report.gerar_e_baixar_ocorrencia_73.return_value=Path(pasta)/"r.sswweb"
            with patch.object(servico,"carregar_relatorio",return_value=[registro(),registro("04/11/26")]), redirect_stdout(io.StringIO()):
                resultado=service.preparar_lotes_oc73(report,D,Path(pasta),fluxo="CWB")
            args=report.gerar_e_baixar_ocorrencia_73.call_args.kwargs
            self.assertEqual(args["data_inicial"],"021126")
            self.assertEqual(args["data_final"],"051126")
            self.assertEqual(resultado["filtrados"][0]["data_inicial_pesquisa"],"2026-11-02")
            self.assertEqual(resultado["filtrados"][0]["data_inicial_ocorrencia"],"2026-11-05")
            self.assertEqual(len(resultado["lotes"][0]["rejeitados_data_oc63"]),1)

    def executar_com_historico(self, data, referencia=D, codigo="63", real=True, fluxo="CWB"):
        with tempfile.TemporaryDirectory() as pasta:
            service=servico.Ocorrencia73Service(repository=Mock())
            service.base_output_dir=Path(pasta)
            service.criar_client_logado=Mock(return_value=Mock())
            item={"serie":"CWB","numero":"490132","fluxo":fluxo,
                  "data_inicial_pesquisa":"2026-11-02","data_final_pesquisa":referencia.isoformat()}
            service.preparar_lotes_oc73=Mock(return_value={"filtrados":[item],"lotes":[],"total_relatorio":1})
            op101=Mock()
            op101.consultar_ctrc.return_value=CTRCConsultado(True,"CWB","490132","123","CWB","ROD")
            op101.listar_ocorrencias.return_value=[OcorrenciaHistorico(codigo,"TESTE",data)]
            op101.encontrar_ocorrencia.return_value=None
            with patch.object(servico,"OP455Report"), patch.object(servico,"OP101Ocorrencias",return_value=op101), \
                 patch.object(servico,"PortaoOC73") as gate, patch.object(servico,"DRY_RUN",not real), \
                 patch.object(servico,"OC73_ENVIO_HABILITADO",True),patch.object(servico,"MAX_LANCAMENTOS",100), \
                 patch.object(servico,"CTRC_TESTE",""), patch.object(servico,"rechecagem_big_habilitada",return_value=False), \
                 redirect_stdout(io.StringIO()):
                gate.return_value.processar.return_value=Mock(status="confirmado",tentativa_id=1,detalhe="")
                resultado=service._executar_interno(referencia,"teste_offline",1,fluxo=fluxo)
                return resultado,gate.call_count,op101

    def test_op101_antiga_invalida_futura_nao_chega_ao_portao(self):
        for data in ("04/11/26 16:25","06/11/26 00:01","data invalida",""):
            with self.subTest(data=data):
                r,chamadas,op101=self.executar_com_historico(data)
                self.assertEqual(r["itens"][0]["status"],"bloqueado_ultima_ocorrencia")
                self.assertEqual(chamadas,0)
                op101.lancar_ocorrencia_73.assert_not_called()

    def test_op101_hoje_chega_ao_portao_e_pesquisa_emissao_ampla(self):
        r,chamadas,op101=self.executar_com_historico("05/11/26 16:25")
        self.assertEqual(chamadas,1)
        self.assertEqual(r["total_lancado"],1)
        self.assertEqual(op101.consultar_ctrc.call_args.kwargs["data_inicial"],date(2026,11,2))

    def test_op101_domingo_aceita_sabado_mas_nao_sexta(self):
        for data,esperado in [("07/11/26 16:25",1),("08/11/26 09:00",1),("06/11/26 16:25",0)]:
            with self.subTest(data=data):
                _,chamadas,_=self.executar_com_historico(data,referencia=date(2026,11,8))
                self.assertEqual(chamadas,esperado)

    def test_big_oc64_antiga_agora_bloqueada(self):
        r,chamadas,_=self.executar_com_historico("04/11/26 16:25",codigo="64",fluxo="BIG")
        self.assertEqual(chamadas,0)
        self.assertEqual(r["total_lancado"],0)

    def test_op101_dry_run_nao_envia(self):
        r,chamadas,_=self.executar_com_historico("05/11/26 16:25",real=False)
        self.assertEqual(chamadas,0)
        self.assertEqual(r["itens"][0]["status"],"pendente_lancamento")


class JanelasBIG(unittest.TestCase):
    def test_big_filtro_por_data_e_uf(self):
        for data,uf,esperado in [("05/11/26","SC",1),("04/11/26","SC",0),
                                 ("06/11/26","SC",0),("","SC",0),("05/11/26","PR",0)]:
            with self.subTest(data=data,uf=uf):
                item=registro(data,codigo="64");item["UF do Expedidor"]=uf
                itens=filtrar_registros([item],fluxo="BIG",data_inicial_ocorrencia=D,data_final_ocorrencia=D)
                self.assertEqual(len(itens),esperado)

    def test_big_preparacao_consulta_d3_e_valida_oc64_de_d(self):
        with tempfile.TemporaryDirectory() as pasta:
            service=servico.Ocorrencia73Service(repository=Mock())
            report=Mock();report.gerar_e_baixar_ocorrencia_73.return_value=Path(pasta)/"r.sswweb"
            with patch.object(servico,"carregar_relatorio",return_value=[registro(codigo="64"),registro("04/11/26",codigo="64")]),redirect_stdout(io.StringIO()):
                r=service.preparar_lotes_oc73(report,D,Path(pasta),fluxo="BIG")
            self.assertEqual(report.gerar_e_baixar_ocorrencia_73.call_args.kwargs["data_inicial"],"021126")
            self.assertEqual(r["total_filtrado"],1)
            self.assertEqual(len(r["lotes"][0]["rejeitados_data_oc64"]),1)
            self.assertEqual(r["filtrados"][0]["data_inicial_pesquisa"],"2026-11-02")

    def test_big_op101_data_hoje_libera_e_antiga_bloqueia(self):
        for data,esperado in [("05/11/26 19:33",1),("04/11/26 19:33",0),("06/11/26 00:01",0)]:
            with self.subTest(data=data):
                r,n,_=JanelasCWB().executar_com_historico(data,codigo="64",fluxo="BIG")
                self.assertEqual(n,esperado)
                if not esperado:
                    self.assertEqual(r["itens"][0]["motivo_bloqueio_data_oc64"],"data_oc64_fora_da_janela")

    def test_big_oc77_continua_espera_sem_liberar_oc64(self):
        item=registro("04/11/26",codigo="77")
        self.assertEqual(filtrar_registros([item],fluxo="BIG",data_inicial_ocorrencia=D,data_final_ocorrencia=D),[])
        self.assertEqual(len(filtrar_registros([item],fluxo="BIG",incluir_espera_big=True,data_inicial_ocorrencia=D,data_final_ocorrencia=D)),1)


if __name__ == "__main__":
    unittest.main()
