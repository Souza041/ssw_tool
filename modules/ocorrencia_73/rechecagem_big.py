"""Fila persistente de reconsulta OC77 -> OC64, sem retry de envio."""
import json
import logging

from modules.ocorrencia_73.gate import PortaoOC73
from modules.ocorrencia_73.rechecagem_politica import (
    CODIGOS_ESPERA_BIG, agora_local, data_local, data_mysql,
    janela_rechecagem, proxima_rechecagem, rechecagem_big_habilitada,
)

logger = logging.getLogger(__name__)


class RechecagemBIG:
    def __init__(self, repository, *, client_factory=None, op101_factory=None,
                 clock=agora_local):
        self.repo = repository
        self.client_factory = client_factory
        self.op101_factory = op101_factory
        self.clock = clock

    def registrar(self, *, item, execucao_id, data_referencia, limite_envios,
                  dry_run, envio_habilitado, ctrc_teste=""):
        if not rechecagem_big_habilitada():
            return "bloqueado_ultima_ocorrencia"
        if item.get("fluxo") != "BIG" or item.get("uf_expedidor") != "SC":
            return "bloqueado_ultima_ocorrencia"
        inicio, limite = janela_rechecagem(data_referencia)
        agora = self.clock()
        if agora < inicio or agora > limite:
            return "rechecagem_fora_janela"
        if ctrc_teste and f"{item['serie']}{item['numero']}" != ctrc_teste:
            return "ignorado_fora_teste"
        if dry_run:
            return "aguardando_oc64_simulacao"
        if not envio_habilitado:
            return "bloqueado_seguranca"
        if limite_envios <= 0:
            return "ignorado_limite"
        proxima = proxima_rechecagem(agora, limite)
        if proxima is None:
            return "rechecagem_expirada"
        if not isinstance(execucao_id, int) or execucao_id <= 0:
            raise ValueError("Espera exige execução MySQL persistida.")
        self.repo.registrar_rechecagem_big(
            execucao_id=execucao_id, data_referencia=data_referencia,
            item=item, limite_envios=limite_envios,
            proxima_consulta_em=data_mysql(proxima), limite_em=data_mysql(limite),
        )
        return "aguardando_oc64"

    def executar_pendentes(self, *, dry_run, envio_habilitado,
                          max_lancamentos, ctrc_teste=""):
        if not rechecagem_big_habilitada() or dry_run or not envio_habilitado:
            return []
        resultados = []
        for candidato in self.repo.listar_rechecagens_big(data_mysql(self.clock())):
            with self.repo.trava_rechecagem_big(candidato["execucao_id"]) as possui_trava:
                if not possui_trava():
                    continue
                # Releitura depois da trava: outro processo pode ter concluído a linha.
                pendencia = self.repo.buscar_rechecagem_big(candidato["id"])
                if not pendencia or pendencia["status"] != "aguardando":
                    continue
                if pendencia["proxima_consulta_em"] > data_mysql(self.clock()):
                    continue
                resultado = self._processar(
                    pendencia, possui_trava, max_lancamentos, ctrc_teste,
                )
                logger.info("[OC73][rechecagem BIG] id=%s status=%s", pendencia["id"], resultado)
                resultados.append({"id": pendencia["id"], "status": resultado})
        return resultados

    def _processar(self, pendencia, possui_trava, max_lancamentos, ctrc_teste):
        limite = data_local(pendencia["limite_em"])
        identificador = pendencia["id"]
        codigo = None
        consultou = False
        child_id = None

        def terminar(status, detalhe="", tentativa_id=None, proxima=None):
            self.repo.atualizar_rechecagem_big(
                identificador, status=status, codigo=codigo, detalhe=detalhe,
                proxima=data_mysql(proxima) if proxima else None,
                tentativa_id=tentativa_id, consultou=consultou,
            )
            return status

        if self.clock() > limite:
            return terminar("expirado", "Prazo 00h30 encerrado; nenhuma consulta SSW realizada.")
        try:
            item = json.loads(pendencia["item_json"])
            if (item.get("fluxo") != "BIG" or item.get("uf_expedidor") != "SC"
                    or item.get("serie") != pendencia["serie"]
                    or item.get("numero") != pendencia["numero"]):
                return terminar("bloqueado_dados", "Identidade ou filtro SC inválido na pendência.")
            # Revalida calendário/prazo da origem, sem aplicar o calendário da sexta.
            inicio, limite_original = janela_rechecagem(pendencia["data_referencia"])
            if self.clock() < inicio or limite != limite_original:
                return terminar("bloqueado_dados", "Janela persistida não corresponde à origem.")
            chave = f"{item['serie']}{item['numero']}"
            if ctrc_teste and chave != ctrc_teste:
                return terminar("ignorado_fora_teste")
            existente = self.repo.buscar_tentativa(item["serie"], item["numero"])
            if existente:
                # Inclusive envio_iniciado após queda: não repetir o POST.
                estado = "confirmado" if existente["status"] == "confirmado" else "incerto"
                return terminar(estado, "Reserva existente; sem reenvio automático.", existente["id"])
            limite_envios = min(int(pendencia["limite_envios"]), max_lancamentos)
            if self.repo.contar_tentativas_rechecagem_big(pendencia["execucao_id"]) >= limite_envios:
                return terminar("ignorado_limite", "Limite acumulado da execução original atingido.")

            from modules.ocorrencia_73.service import (
                Ocorrencia73Service, obter_ultima_ocorrencia_op101,
            )
            from operations.op101.ocorrencias import OP101Ocorrencias
            client_factory = self.client_factory or Ocorrencia73Service().criar_client_logado
            op101_factory = self.op101_factory or OP101Ocorrencias
            client = client_factory()
            try:
                if self.clock() > limite:
                    return terminar("expirado")
                op101 = op101_factory(client)
                agora = self.clock()
                consulta = op101.consultar_ctrc(
                    serie=item["serie"], numero=item["numero"],
                    data_referencia=agora.date(),
                    data_inicial=pendencia["data_referencia"], data_final=agora.date(),
                )
                consultou = True
                if not consulta.encontrado:
                    return terminar("nao_encontrado")
                if self.clock() > limite:
                    return terminar("expirado")
                html = op101.abrir_ocorrencias(
                    serie=item["serie"], numero=item["numero"],
                    seq_ctrc=consulta.seq_ctrc, local=consulta.local,
                    familia=consulta.familia, data_referencia=self.clock().date(),
                )
                historico = op101.listar_ocorrencias(html, estrito=True)
                if op101.encontrar_ocorrencia(historico, 73) is not None:
                    return terminar("ja_existia")
                ultima = obter_ultima_ocorrencia_op101(historico)
                if ultima is None:
                    return terminar("bloqueado_historico")
                codigo = str(ultima.codigo)
                if self.clock() > limite:
                    return terminar("expirado")
                if codigo in CODIGOS_ESPERA_BIG:
                    proxima = proxima_rechecagem(self.clock(), limite)
                    if proxima is None:
                        return terminar("expirado", "OC77 permaneceu na rodada final.")
                    return terminar("aguardando", proxima=proxima)
                if codigo != "64":
                    return terminar("bloqueado_ocorrencia", "Última ocorrência não é 64 nem 77.")
                if not possui_trava():
                    return terminar("bloqueado_trava")
                child_id = self.repo.iniciar_envio_rechecagem_big(pendencia)
                envio_em = {}

                def enviar(referencia):
                    agora_envio = self.clock()
                    if agora_envio > limite or not possui_trava():
                        # O portão preservará como incerto: nunca rearma reserva.
                        raise RuntimeError("Prazo ou posse da trava perdido antes do envio.")
                    envio_em["data"] = agora_envio.date()
                    return op101.lancar_ocorrencia_73(
                        seq_ctrc=consulta.seq_ctrc, familia=consulta.familia,
                        data_ocorrencia=agora_envio.date(),
                        hora_ocorrencia=agora_envio.strftime("%H%M"),
                        observacao=f"BOT OC73 | REF:{referencia}", local=consulta.local,
                    )

                resultado = PortaoOC73(self.repo).processar(
                    execucao_id=child_id, serie=item["serie"], numero=item["numero"],
                    fluxo="BIG", data_inicial=pendencia["data_referencia"],
                    data_final=self.clock().date(), enviar=enviar,
                    confirmar=lambda referencia: op101.confirmar_ocorrencia_73(
                        serie=item["serie"], numero=item["numero"],
                        seq_ctrc=consulta.seq_ctrc, local=consulta.local,
                        familia=consulta.familia, data_referencia=envio_em["data"],
                        referencia=referencia,
                    ),
                )
                self.repo.finalizar_execucao(
                    execucao_id=child_id,
                    status="concluido" if resultado.status == "confirmado" else "concluido_com_pendencias",
                    total_filtrado=1, total_lancado=int(resultado.status == "confirmado"),
                    total_erros=int(resultado.status == "incerto"),
                    mensagem_erro=resultado.detalhe or None,
                )
                return terminar(resultado.status, resultado.detalhe, resultado.tentativa_id)
            finally:
                client.session.close()
        except Exception as exc:
            # Antes do portão: erro de consulta/dados. Depois da criação da filha:
            # intervenção manual; a reserva do portão também impede novo envio.
            estado = "incerto" if child_id is not None else "erro_consulta"
            return terminar(estado, f"{type(exc).__name__}: {exc}"[:2000])
