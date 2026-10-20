import csv
import os
import json

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from modules.ocorrencia_73.config import (
    CLIENTES_PERMITIDOS,
    DRY_RUN,
    ROTAS_PERMITIDAS,
)
from modules.ocorrencia_73.parser import (
    carregar_relatorio,
    diagnosticar_filtros,
    filtrar_registros,
    normalizar_sem_acento,
    normalizar_texto,
)
from operations.op101.ocorrencias import (
    OP101Ocorrencias,
)
from operations.op455.report import OP455Report
from ssw.client import SSWClient

from modules.ocorrencia_73.gate import PortaoOC73
from modules.ocorrencia_73.repository import Ocorrencia73Repository

from modules.ocorrencia_73.calendario import (
    calcular_janelas_oc73,
)

TIMEZONE = ZoneInfo("America/Sao_Paulo")

def obter_ultima_ocorrencia_op101(historico):
    """Retorna a ocorrência mais recente, sem depender da ordem da tela."""
    ocorrencias_validas = []

    for ocorrencia in historico:
        data_texto = str(ocorrencia.data_hora or "").strip()

        try:
            data_ocorrencia = datetime.strptime(
                data_texto,
                "%d/%m/%y %H:%M",
            )
        except ValueError:
            # Uma data desconhecida impede validar com segurança.
            return None

        ocorrencias_validas.append(
            (data_ocorrencia, ocorrencia)
        )

    if not ocorrencias_validas:
        return None

    return max(
        ocorrencias_validas,
        key=lambda item: item[0],
    )[1]

MAX_LANCAMENTOS = int(
    os.getenv("OCORRENCIA_73_MAX_LANCAMENTOS", "").strip()
    or "1"
)

CTRC_TESTE = (
    os.getenv(
        "OCORRENCIA_73_CTRC_TESTE",
        "",
    )
    .strip()
    .upper()
)

# Trava adicional de producao.
# Por padrao, nenhum lancamento real e autorizado.
OC73_ENVIO_HABILITADO = (
    os.getenv("OC73_ENVIO_HABILITADO", "")
    .strip()
    .upper() == "SIM"
)


# Classificação única para auditoria e fechamento da execução.
# Incerto é pendência de reconciliação, não erro técnico confirmado.
OC73_STATUS_INCERTO = frozenset({"incerto"})
OC73_STATUS_BLOQUEADO = frozenset({
    "bloqueado_reserva_existente", "bloqueado_transicao", "bloqueado_seguranca", "bloqueado_ultima_ocorrencia",
})
OC73_STATUS_ERRO_TECNICO = frozenset({
    "erro_consulta", "erro_persistencia", "erro_lancamento",
})


def calcular_indicadores_oc73(itens: list[dict]) -> dict[str, int]:
    """Classifica estados sem confundir tentativa incerta com lançamento."""
    estados = [item.get("status") for item in itens]
    return {
        "total_incerto": sum(x in OC73_STATUS_INCERTO for x in estados),
        "total_bloqueado": sum(x in OC73_STATUS_BLOQUEADO for x in estados),
        "total_erro_tecnico": sum(x in OC73_STATUS_ERRO_TECNICO for x in estados),
    }



class Ocorrencia73Service:
    def __init__(self, repository=None) -> None:
        self.repository = repository
        self.base_output_dir = Path(
            "downloads/ocorrencia_73"
        )

        self.base_output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

    def criar_client_logado(self) -> SSWClient:
        dominio = (
            os.getenv("OCORRENCIA_73_SSW_DOMINIO")
            or ""
        ).strip()

        cpf = (
            os.getenv("OCORRENCIA_73_SSW_CPF")
            or ""
        ).strip()

        usuario = (
            os.getenv("OCORRENCIA_73_SSW_USUARIO")
            or ""
        ).strip()

        senha = (
            os.getenv("OCORRENCIA_73_SSW_SENHA")
            or ""
        ).strip()

        faltando = []

        if not dominio:
            faltando.append(
                "OCORRENCIA_73_SSW_DOMINIO"
            )

        if not cpf:
            faltando.append(
                "OCORRENCIA_73_SSW_CPF"
            )

        if not usuario:
            faltando.append(
                "OCORRENCIA_73_SSW_USUARIO"
            )

        if not senha:
            faltando.append(
                "OCORRENCIA_73_SSW_SENHA"
            )

        if faltando:
            raise ValueError(
                "Variáveis do SSW não configuradas: "
                + ", ".join(faltando)
            )

        client = SSWClient(
            dominio=dominio,
            cpf=cpf,
            usuario=usuario,
            senha=senha,
            unidade="MTZ",
        )

        client.login()
        client.open_menu()

        return client

    def montar_diretorios_execucao(
        self,
        data_referencia: date,
    ) -> dict[str, Path]:
        diretorio_base = (
            self.base_output_dir
            / data_referencia.isoformat()
        )

        diretorio_original = (
            diretorio_base
            / "original"
        )

        diretorio_auditoria = (
            diretorio_base
            / "auditoria"
        )

        diretorio_original.mkdir(
            parents=True,
            exist_ok=True,
        )

        diretorio_auditoria.mkdir(
            parents=True,
            exist_ok=True,
        )

        return {
            "base": diretorio_base,
            "original": diretorio_original,
            "auditoria": diretorio_auditoria,
        }

    def gerar_auditoria_rotas(
        self,
        registros: list[dict],
        diretorio_auditoria: Path,
    ) -> Path | None:
        if not registros:
            print(
                "[AUDITORIA] Relatório sem registros."
            )
            return None

        coluna_unidade = None
        coluna_cidade = None
        coluna_receptora = None

        for coluna in registros[0]:
            coluna_normalizada = normalizar_sem_acento(
                coluna
            )

            if (
                coluna_normalizada
                == normalizar_sem_acento(
                    "Unidade Emissora"
                )
            ):
                coluna_unidade = coluna

            if (
                coluna_normalizada
                == normalizar_sem_acento(
                    "Cidade do Destinatario"
                )
            ):
                coluna_cidade = coluna

            if (
                coluna_normalizada
                == normalizar_sem_acento(
                    "Unidade Receptora"
                )
            ):
                coluna_receptora = coluna

        if not coluna_unidade:
            raise KeyError(
                "Coluna 'Unidade Emissora' "
                "não encontrada para auditoria."
            )

        if not coluna_cidade:
            raise KeyError(
                "Coluna 'Cidade do Destinatario' "
                "não encontrada para auditoria."
            )

        if not coluna_receptora:
            raise KeyError(
                "Coluna 'Unidade Receptora' "
                "não encontrada para auditoria."
            )

        rotas_normalizadas = {
            normalizar_sem_acento(unidade): {
                normalizar_sem_acento(cidade)
                for cidade in cidades
            }
            for unidade, cidades in (
                ROTAS_PERMITIDAS.items()
            )
        }

        registros_rotas = []

        total_cwb_curitiba = 0
        total_joi_florianopolis = 0
        total_joi_big = 0
        total_sobreposicao = 0

        for registro in registros:
            unidade_original = normalizar_texto(
                registro.get(coluna_unidade)
            )

            cidade_original = normalizar_texto(
                registro.get(coluna_cidade)
            )

            receptora_original = normalizar_texto(
                registro.get(coluna_receptora)
            )

            unidade = normalizar_sem_acento(
                unidade_original
            )

            cidade = normalizar_sem_acento(
                cidade_original
            )

            receptora = normalizar_sem_acento(
                receptora_original
            )

            cidades_validas = (
                rotas_normalizadas.get(
                    unidade,
                    set(),
                )
            )

            atende_rota_antiga = (
                cidade in cidades_validas
            )

            atende_joi_big = (
                receptora == "BIG"
            )

            if not (
                atende_rota_antiga
                or atende_joi_big
            ):
                continue

            regras_auditoria = []

            if atende_rota_antiga:
                if (
                    unidade == "CWB"
                    and cidade == "CURITIBA"
                ):
                    total_cwb_curitiba += 1
                    regras_auditoria.append(
                        "CWB -> CURITIBA"
                    )

                elif (
                    unidade == "JOI"
                    and cidade == "FLORIANOPOLIS"
                ):
                    total_joi_florianopolis += 1
                    regras_auditoria.append(
                        "JOI -> FLORIANOPOLIS"
                    )

            if atende_joi_big:
                total_joi_big += 1
                regras_auditoria.append(
                    "RECEPTORA BIG (QUALQUER EMISSORA)"
                )

            if (
                atende_rota_antiga
                and atende_joi_big
            ):
                total_sobreposicao += 1

            # Fazemos uma cópia para não alterar o
            # registro original carregado da OP455.
            registro_auditoria = dict(registro)

            registro_auditoria[
                "AUDITORIA_REGRAS"
            ] = " | ".join(
                regras_auditoria
            )

            registros_rotas.append(
                registro_auditoria
            )

        if not registros_rotas:
            print(
                "[AUDITORIA] Nenhum registro "
                "nas rotas monitoradas."
            )
            return None

        csv_saida = (
            diretorio_auditoria
            / "auditoria_rotas.csv"
        )

        with csv_saida.open(
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as fp:
            writer = csv.DictWriter(
                fp,
                fieldnames=list(
                    registros_rotas[0].keys()
                ),
            )

            writer.writeheader()
            writer.writerows(
                registros_rotas
            )

        print(
            "[AUDITORIA] Registros únicos monitorados: "
            f"{len(registros_rotas)}"
        )

        print(
            "[AUDITORIA] CWB -> CURITIBA: "
            f"{total_cwb_curitiba}"
        )

        print(
            "[AUDITORIA] JOI -> FLORIANOPOLIS: "
            f"{total_joi_florianopolis}"
        )

        print(
            "[AUDITORIA] RECEPTORA BIG (QUALQUER EMISSORA): "
            f"{total_joi_big}"
        )

        print(
            "[AUDITORIA] Sobreposição entre regras: "
            f"{total_sobreposicao}"
        )

        return csv_saida

    def gerar_auditoria_filtrados(
        self,
        registros: list[dict],
        diretorio_auditoria: Path,
    ) -> Path | None:
        if not registros:
            print(
                "[AUDITORIA] Nenhum registro "
                "atendeu aos filtros."
            )
            return None

        csv_saida = (
            diretorio_auditoria
            / "registros_filtrados.csv"
        )

        campos = [
            "ctrc_original",
            "serie",
            "numero",
            "digito",
            "cliente_pagador",
            "cidade_destinatario",
            "unidade_emissora",
            "unidade_receptora",
            "ultima_ocorrencia",
            "regra_filtro",
            "fluxo",
            "uf_remetente",
        ]

        with csv_saida.open(
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as fp:
            writer = csv.DictWriter(
                fp,
                fieldnames=campos,
                extrasaction="ignore",
            )

            writer.writeheader()
            writer.writerows(registros)

        print(
            "[AUDITORIA] Filtrados: "
            f"{len(registros)} registros"
        )

        return csv_saida

    def preparar_lotes_oc73(
        self,
        op455: OP455Report,
        data_referencia: date,
        diretorio_original: Path,
        fluxo: str | None = None,
    ) -> dict:
        """
        Gera e filtra os relatórios CWB e BIG.

        Não consulta a OP101 e não lança ocorrências.
        """

        janelas = calcular_janelas_oc73(
            data_referencia,
            fluxo=fluxo,
        )

        lotes = []
        ctrcs_unicos = {}
        total_registros = 0

        for janela in janelas:
            fluxo = janela.fluxo

            print(
                f"[OC73] Preparando {fluxo}: "
                f"{janela.data_inicial_ssw} até "
                f"{janela.data_final_ssw}"
            )

            # Cada fluxo recebe seu próprio diretório.
            # Isso evita colisão entre arquivos baixados.
            diretorio_fluxo = (
                diretorio_original / fluxo.lower()
            )

            diretorio_fluxo.mkdir(
                parents=True,
                exist_ok=True,
            )

            arquivo = (
                op455.gerar_e_baixar_ocorrencia_73(
                    output_dir=diretorio_fluxo,
                    data_inicial=janela.data_inicial_ssw,
                    data_final=janela.data_final_ssw,
                    unidade=fluxo,
                    timeout_seconds=300,
                )
            )

            registros = carregar_relatorio(arquivo)

            filtrados = filtrar_registros(
                registros=registros,
                fluxo=fluxo,
            )

            total_registros += len(registros)

            for item in filtrados:
                chave = (
                    str(item["serie"]).upper(),
                    str(item["numero"]).strip(),
                )

                if chave not in ctrcs_unicos:
                    ctrcs_unicos[chave] = {
                        **item,
                        "data_inicial_pesquisa": (
                            janela.data_inicial.isoformat()
                        ),
                        "data_final_pesquisa": (
                            janela.data_final.isoformat()
                        ),
                    }

            lotes.append({
                "fluxo": fluxo,
                "data_inicial": (
                    janela.data_inicial.isoformat()
                ),
                "data_final": (
                    janela.data_final.isoformat()
                ),
                "arquivo": str(arquivo),
                "total_relatorio": len(registros),
                "total_filtrado": len(filtrados),
            })

            print(
                f"[OC73] {fluxo}: "
                f"{len(registros)} registros, "
                f"{len(filtrados)} elegíveis."
            )

        return {
            "lotes": lotes,
            "total_relatorio": total_registros,
            "filtrados": list(ctrcs_unicos.values()),
            "total_filtrado": len(ctrcs_unicos),
        }

    def executar(
        self,
        data_referencia: date | None = None,
        triggered_by: str = "manual",
        fluxo: str | None = None,
        execucao_id_reservada: int | None = None,
    ) -> dict:
        """Registra a execução e impede envio sem persistência válida."""
        data_referencia = data_referencia or datetime.now(TIMEZONE).date()

        if execucao_id_reservada is None and DRY_RUN:
            # Simulações manuais não precisam de banco e nunca fazem POST.
            return self._executar_interno(
                data_referencia,
                triggered_by,
                None,
                fluxo,
            )

        repo = (
            self.repository
            if self.repository is not None
            else Ocorrencia73Repository()
        )
        self.repository = repo

        if execucao_id_reservada is not None:
            if (
                not isinstance(execucao_id_reservada, int)
                or execucao_id_reservada <= 0
            ):
                raise ValueError("Reserva de execução agendada inválida.")
            execucao_id = execucao_id_reservada
        else:
            # Em modo de produção, a execução é persistida antes do fluxo.
            execucao_id = repo.iniciar_execucao(
                data_referencia=data_referencia,
                triggered_by=triggered_by,
                dry_run=False,
            )

        try:
            resultado = self._executar_interno(
                data_referencia,
                triggered_by,
                execucao_id,
                fluxo,
            )
        except BaseException as exc:
            repo.finalizar_execucao(
                execucao_id=execucao_id, status="erro",
                mensagem_erro=f"{type(exc).__name__}: {exc}"[:2000],
            )
            raise
        indicadores = calcular_indicadores_oc73(resultado.get("itens", []))
        resultado.update(indicadores)
        pendencias = sum(indicadores.values())
        # Compatibilidade: total_erros inclui falhas técnicas e estados incertos,
        # mas não bloqueios deliberados. O estado da execução considera os três.
        total_erros = indicadores["total_erro_tecnico"] + indicadores["total_incerto"]
        repo.finalizar_execucao(
            execucao_id=execucao_id,
            status="concluido_com_pendencias" if pendencias else "concluido",
            total_relatorio=resultado.get("total_relatorio", 0),
            total_filtrado=resultado.get("total_filtrado", 0),
            total_lancado=resultado.get("total_lancado", 0),
            total_erros=total_erros,
        )
        resultado["execucao_id"] = execucao_id
        return resultado

    def _executar_interno(
        self,
        data_referencia: date,
        triggered_by: str,
        execucao_id: int | None,
        fluxo: str | None = None,
    ) -> dict:
        data_referencia = (
            data_referencia
            or datetime.now(TIMEZONE).date()
        )

        diretorios = (
            self.montar_diretorios_execucao(
                data_referencia
            )
        )

        diretorio_base = diretorios["base"]
        diretorio_original = diretorios["original"]
        diretorio_auditoria = diretorios["auditoria"]

        janelas = calcular_janelas_oc73(
            data_referencia,
            fluxo=fluxo,
        )

        if not janelas:
            return {
                "success": True,
                "triggered_by": triggered_by,
                "data_referencia": data_referencia.isoformat(),
                "status": "sem_execucao_programada",
                "message": "Nenhum fluxo programado para esta data.",
                "total_relatorio": 0,
                "total_filtrado": 0,
                "itens": [],
                **calcular_indicadores_oc73([]),
            }

        client = self.criar_client_logado()
        op455 = OP455Report(client)

        preparacao = self.preparar_lotes_oc73(
            op455=op455,
            data_referencia=data_referencia,
            diretorio_original=diretorio_original,
            fluxo=fluxo,
        )

        filtrados = preparacao["filtrados"]
        lotes = preparacao["lotes"]
        total_relatorio = preparacao["total_relatorio"]

        diagnostico = {
            "total_op455": total_relatorio,
            "total_filtrado": len(filtrados),
            "total_lotes": len(lotes),
            "fluxos": {
                lote["fluxo"]: {
                    "data_inicial": lote["data_inicial"],
                    "data_final": lote["data_final"],
                    "total_relatorio": lote["total_relatorio"],
                    "total_filtrado": lote["total_filtrado"],
                }
                for lote in lotes
            },
        }

        auditoria_rotas = None

        auditoria_filtrados = (
            self.gerar_auditoria_filtrados(
                registros=filtrados,
                diretorio_auditoria=diretorio_auditoria,
            )
        )

        resumo_auditoria = {
            "total_op455": total_relatorio,
            "total_apos_filtros": len(filtrados),
            "fluxos": diagnostico["fluxos"],
            "modo": "dry_run" if DRY_RUN else "producao",
        }

        if not filtrados:
            print(
                "[OC73] Nenhum CTRC elegível. "
                "Execução concluída sem consultar OP101."
            )

            resultado = {
                "success": True,
                "status": "sem_elegiveis",
                "triggered_by": triggered_by,
                "dry_run": DRY_RUN,
                "data_referencia": data_referencia.isoformat(),
                "lotes": lotes,
                "arquivos_originais": [
                    lote["arquivo"] for lote in lotes
                ],
                "diretorio_execucao": str(diretorio_base),
                "auditoria_rotas": None,
                "auditoria_filtrados": (
                    str(auditoria_filtrados)
                    if auditoria_filtrados else None
                ),
                "total_relatorio": total_relatorio,
                "total_filtrado": 0,
                "diagnostico": diagnostico,
                "resumo_auditoria": resumo_auditoria,
                "total_consultado": 0,
                "total_encontrado_op101": 0,
                "total_nao_encontrado_op101": 0,
                "total_erro_op101": 0,
                "total_ja_existia": 0,
                "total_pendente_lancamento": 0,
                "total_lancado": 0,
                "total_erro_lancamento": 0,
                "itens": [],
                **calcular_indicadores_oc73([]),
                "message": "Nenhum CTRC atendeu aos filtros.",
            }

            arquivo_resultado = self.salvar_resultado_json(
                resultado=resultado,
                diretorio_auditoria=diretorio_auditoria,
            )

            resultado["resultado_json"] = str(arquivo_resultado)

            return resultado

        op101 = OP101Ocorrencias(
            client
        )

        itens_consultados = []

        total_lancado = 0
        total_erro_lancamento = 0
        total_tentativas_lancamento = 0

        for indice, item in enumerate(
            filtrados,
            start=1,
        ):
            print(
                "[OP101] "
                f"{indice}/{len(filtrados)} "
                f"Consultando "
                f"{item['serie']}"
                f"{item['numero']}..."
            )

            try:
                consulta = op101.consultar_ctrc(
                    serie=item["serie"],
                    numero=item["numero"],
                    data_referencia=data_referencia,
                    data_inicial=(
                        date.fromisoformat(
                            item["data_inicial_pesquisa"]
                        )
                        if item.get("data_inicial_pesquisa")
                        else data_referencia
                    ),
                    data_final=(
                        date.fromisoformat(
                            item["data_final_pesquisa"]
                        )
                        if item.get("data_final_pesquisa")
                        else data_referencia
                    ),
                )

                if not consulta.encontrado:
                    status = "nao_encontrado"

                    print(
                        "[OP101] "
                        f"{item['serie']}{item['numero']} "
                        f"-> {status}"
                    )

                    item_processado = {
                        **item,
                        "op101": consulta.to_dict(),
                        "historico_ocorrencias": [],
                        "total_historico_ocorrencias": 0,
                        "ocorrencia_73": None,
                        "lancamento": None,
                        "status": status,
                    }

                else:
                    html_ocorrencias = (
                        op101.abrir_ocorrencias(
                            serie=item["serie"],
                            numero=item["numero"],
                            seq_ctrc=consulta.seq_ctrc,
                            local=consulta.local,
                            familia=consulta.familia,
                            data_referencia=data_referencia,
                        )
                    )

                    historico = (
                        op101.listar_ocorrencias(
                            html_ocorrencias
                        )
                    )

                    ocorrencia_73 = (
                        op101.encontrar_ocorrencia(
                            historico,
                            73,
                        )
                    )

                    ja_existia = (
                        ocorrencia_73 is not None
                    )

                    ultima_ocorrencia_op101 = obter_ultima_ocorrencia_op101(
                        historico
                    )

                    fluxo_atual = str(item.get("fluxo") or "").upper()

                    codigo_exigido = {
                        "CWB": "63",
                        "BIG": "64",
                    }.get(fluxo_atual)

                    ultima_ocorrencia_valida = (
                        ultima_ocorrencia_op101 is not None
                        and codigo_exigido is not None
                        and ultima_ocorrencia_op101.codigo == codigo_exigido
                    )

                    if ja_existia:
                        status = "ja_existia"
                        lancamento = None

                    elif not ultima_ocorrencia_valida:
                        status = "bloqueado_ultima_ocorrencia"
                        lancamento = None

                    elif DRY_RUN:
                        status = "pendente_lancamento"
                        lancamento = None

                    else:
                        ctrc_chave = (
                            f"{item['serie']}"
                            f"{item['numero']}"
                        )

                        if CTRC_TESTE and ctrc_chave != CTRC_TESTE:
                            status = "ignorado_fora_teste"
                            lancamento = None
                        elif total_tentativas_lancamento >= MAX_LANCAMENTOS:
                            status = "ignorado_limite"
                            lancamento = None
                        elif not OC73_ENVIO_HABILITADO:
                            status = "bloqueado_seguranca"
                            lancamento = None
                        elif execucao_id is None:
                            raise RuntimeError("Envio sem execução MySQL persistida")
                        else:
                            total_tentativas_lancamento += 1
                            portao = PortaoOC73(self.repository)
                            data_ini = date.fromisoformat(
                                item.get("data_inicial_pesquisa") or data_referencia.isoformat()
                            )
                            data_fim = date.fromisoformat(
                                item.get("data_final_pesquisa") or data_referencia.isoformat()
                            )
                            resultado_portao = portao.processar(
                                execucao_id=execucao_id,
                                serie=item["serie"], numero=item["numero"],
                                fluxo=item["fluxo"],
                                data_inicial=data_ini, data_final=data_fim,
                                enviar=lambda referencia: op101.lancar_ocorrencia_73(
                                    seq_ctrc=consulta.seq_ctrc,
                                    familia=consulta.familia,
                                    data_ocorrencia=data_referencia,
                                    observacao=f"BOT OC73 | REF:{referencia}",
                                    local=consulta.local,
                                ),
                                confirmar=lambda referencia: (
                                    op101.confirmar_ocorrencia_73(
                                        serie=item["serie"],
                                        numero=item["numero"],
                                        seq_ctrc=consulta.seq_ctrc,
                                        local=consulta.local,
                                        familia=consulta.familia,
                                        data_referencia=data_referencia,
                                        referencia=referencia,
                                    )
                                ),
                            )
                            status = (
                                "lancada" if resultado_portao.status == "confirmado"
                                else resultado_portao.status
                            )
                            lancamento = {
                                "tentativa_id": resultado_portao.tentativa_id,
                                "status": resultado_portao.status,
                                "detalhe": resultado_portao.detalhe,
                            }

                    print(
                        "[OP101] "
                        f"{item['serie']}{item['numero']} "
                        f"-> {status}"
                    )

                    item_processado = {
                        **item,
                        "op101": consulta.to_dict(),
                        "historico_ocorrencias": [
                            ocorrencia.to_dict()
                            for ocorrencia in historico
                        ],
                        "total_historico_ocorrencias": len(
                            historico
                        ),
                        "ultima_ocorrencia_op101": (
                            ultima_ocorrencia_op101.to_dict()
                            if ultima_ocorrencia_op101
                            else None
                        ),
                        "codigo_ultima_ocorrencia_exigido": codigo_exigido,
                        "ocorrencia_73": (
                            ocorrencia_73.to_dict()
                            if ocorrencia_73
                            else None
                        ),
                        "lancamento": lancamento,
                        "status": status,
                    }

            except Exception as erro:
                if not DRY_RUN and OC73_ENVIO_HABILITADO:
                    raise RuntimeError(
                        f"OC73 interrompida; verificar estado MySQL/SSW para "
                        f"{item['serie']}{item['numero']}: {erro}"
                    ) from erro
                item_processado = {
                    **item,
                    "op101": {
                        "encontrado": False,
                        "serie": item["serie"],
                        "numero": item["numero"],
                        "seq_ctrc": "",
                        "local": "",
                        "familia": "",
                        "mensagem": str(erro),
                    },
                    "status": "erro_consulta",
                }

                print(
                    "[OP101] Erro ao consultar "
                    f"{item['serie']}"
                    f"{item['numero']}: "
                    f"{erro}"
                )

            itens_consultados.append(
                item_processado
            )

        total_ja_existia = sum(
            1
            for item in itens_consultados
            if item["status"] == "ja_existia"
        )

        total_pendente_lancamento = sum(
            1
            for item in itens_consultados
            if item["status"] == "pendente_lancamento"
        )

        total_lancado = sum(
            1
            for item in itens_consultados
            if item["status"] == "lancada"
        )

        total_erro_lancamento = sum(
            1
            for item in itens_consultados
            if item["status"] == "erro_lancamento"
        )

        total_ignorado_fora_teste = sum(
            1
            for item in itens_consultados
            if item["status"] == "ignorado_fora_teste"
        )

        total_ignorado_limite = sum(
            1
            for item in itens_consultados
            if item["status"] == "ignorado_limite"
        )

        total_encontrado = sum(
            1
            for item in itens_consultados
            if item["status"] not in {
                "nao_encontrado",
                "erro_consulta",
            }
        )

        total_nao_encontrado = sum(
            1
            for item in itens_consultados
            if (
                item["status"]
                == "nao_encontrado"
            )
        )

        total_erro = sum(
            1
            for item in itens_consultados
            if (
                item["status"]
                == "erro_consulta"
            )
        )

        indicadores = calcular_indicadores_oc73(itens_consultados)

        resumo_auditoria.update({
            "total_consultado_op101": len(itens_consultados),
            "total_encontrado_op101": total_encontrado,
            "total_nao_encontrado_op101": total_nao_encontrado,
            "total_erro_op101": total_erro,
            "total_ja_existia": total_ja_existia,
            "total_pendente_lancamento": total_pendente_lancamento,
            "total_lancado": total_lancado,
            "total_erro_lancamento": total_erro_lancamento,
            **indicadores,
        })

        print()
        print("=" * 62)
        print("AUDITORIA - BOT OCORRENCIA 73")
        print("=" * 62)

        for lote in lotes:
            print(
                f"{lote['fluxo']}: "
                f"{lote['total_relatorio']} registros, "
                f"{lote['total_filtrado']} elegíveis"
            )

        print(f"Total OP455...........: {total_relatorio}")
        print(f"CTRCs únicos elegíveis: {len(filtrados)}")
        print(f"Consultados na OP101..: {len(itens_consultados)}")
        print(f"OC73 já existente.....: {total_ja_existia}")
        print(f"OC73 lançadas.........: {total_lancado}")
        print(f"Erros de lançamento...: {total_erro_lancamento}")
        print(f"Envios incertos........: {indicadores['total_incerto']}")
        print(f"Bloqueios..............: {indicadores['total_bloqueado']}")
        print(f"Erros técnicos.........: {indicadores['total_erro_tecnico']}")
        print("=" * 62)

        resultado = {
            "success": True,
            "triggered_by": triggered_by,
            "dry_run": DRY_RUN,
            "data_referencia": (
                data_referencia.isoformat()
            ),
            "lotes": lotes,
            "arquivos_originais": [
                lote["arquivo"] for lote in lotes
            ],
            "diretorio_execucao": str(
                diretorio_base
            ),
            "auditoria_rotas": (
                str(auditoria_rotas)
                if auditoria_rotas
                else None
            ),
            "auditoria_filtrados": (
                str(auditoria_filtrados)
                if auditoria_filtrados
                else None
            ),
            "total_relatorio": total_relatorio,
            "total_filtrado": len(
                filtrados
            ),
            "diagnostico": diagnostico,
            "total_consultado": len(
                itens_consultados
            ),
            "total_encontrado_op101": (
                total_encontrado
            ),
            "total_ja_existia": total_ja_existia,
            "total_pendente_lancamento": (
                total_pendente_lancamento
            ),
            "total_lancado": total_lancado,
            "total_erro_lancamento": (
                total_erro_lancamento
            ),
            "total_ignorado_fora_teste": (
                total_ignorado_fora_teste
            ),
            "total_ignorado_limite": (
                total_ignorado_limite
            ),
            "total_nao_encontrado_op101": (
                total_nao_encontrado
            ),
            "total_erro_op101": (
                total_erro
            ),

            "resumo_auditoria": resumo_auditoria,

            "itens": itens_consultados,
            **indicadores,
        }

        arquivo_resultado = (
            self.salvar_resultado_json(
                resultado=resultado,
                diretorio_auditoria=(
                    diretorio_auditoria
                ),
            )
        )

        resultado["resultado_json"] = str(
            arquivo_resultado
        )

        return resultado

    def salvar_resultado_json(
        self,
        resultado: dict,
        diretorio_auditoria: Path,
    ) -> Path:
        def sem_registro_original(valor):
            if isinstance(valor, dict):
                return {
                    chave: sem_registro_original(item)
                    for chave, item in valor.items()
                    if chave != "registro_original"
                }
            if isinstance(valor, (list, tuple)):
                return [sem_registro_original(item) for item in valor]
            return valor

        arquivo_saida = (
            diretorio_auditoria
            / "resultado.json"
        )

        conteudo = json.dumps(
            sem_registro_original(resultado),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

        arquivo_saida.write_text(
            conteudo,
            encoding="utf-8",
        )

        print(
            "[AUDITORIA] Resultado salvo em: "
            f"{arquivo_saida}"
        )

        return arquivo_saida

    def imprimir_resumo_auditoria(
        self,
        *,
        total_relatorio: int,
        diagnostico: dict,
        total_filtrado: int,
        total_consultado: int,
        total_encontrado: int,
        total_nao_encontrado: int,
        total_erro: int,
        total_ja_existia: int,
        total_pendente_lancamento: int,
        total_lancado: int,
        total_erro_lancamento: int,
        dry_run: bool,
    ) -> None:
        rotas_encontradas = (
            diagnostico.get("rotas_encontradas")
            or {}
        )

        print()
        print("=" * 62)
        print("AUDITORIA - BOT OCORRENCIA 73")
        print("=" * 62)

        print(
            f"Total de registros na OP455........: "
            f"{total_relatorio}"
        )

        print(
            f"Registros nas rotas monitoradas....: "
            f"{diagnostico.get('rota', 0)}"
        )

        if rotas_encontradas:
            for rota, total in sorted(
                rotas_encontradas.items()
            ):
                print(
                    f"  {rota:<32}: {total}"
                )
        else:
            print(
                "  Nenhuma rota monitorada encontrada."
            )

        print(
            f"Registros dos clientes monitorados.: "
            f"{diagnostico.get('cliente', 0)}"
        )

        print()
        print("REGRA ANTIGA")

        print(
            f"CWB -> CURITIBA + cliente...........: "
            f"{diagnostico.get('cwb_curitiba', 0)}"
        )

        print(
            f"JOI -> FLORIANOPOLIS + cliente......: "
            f"{diagnostico.get('joi_florianopolis', 0)}"
        )

        print(
            f"Total regra antiga + cliente.........: "
            f"{diagnostico.get('rota_antiga_cliente', 0)}"
        )

        print()
        print("REGRA NOVA - JOI -> BIG -> OC64")

        print(
            f"JOI -> BIG...........................: "
            f"{diagnostico.get('joi_big', 0)}"
        )

        print(
            f"JOI -> BIG + OC64....................: "
            f"{diagnostico.get('joi_big_oc64', 0)}"
        )

        print(
            f"JOI -> BIG + OC64 + cliente..........: "
            f"{diagnostico.get('joi_big_oc64_cliente', 0)}"
        )

        print()
        print(
            f"Registros finais únicos..............: "
            f"{diagnostico.get('todos_filtros', 0)}"
        )

        print(
            f"Registros enviados pelo filtro.......: "
            f"{total_filtrado}"
        )

        print("-" * 62)

        print(
            f"CTRCs enviados para consulta OP101..: "
            f"{total_consultado}"
        )

        print(
            f"CTRCs encontrados na OP101..........: "
            f"{total_encontrado}"
        )

        print(
            f"CTRCs não encontrados na OP101......: "
            f"{total_nao_encontrado}"
        )

        print(
            f"Erros técnicos na consulta OP101....: "
            f"{total_erro}"
        )

        print(
            f"Já possuíam ocorrência 73...........: "
            f"{total_ja_existia}"
        )

        print(
            f"Pendentes de lançamento.............: "
            f"{total_pendente_lancamento}"
        )

        print(
            f"Ocorrências lançadas................: "
            f"{total_lancado}"
        )

        print(
            f"Ocorrências com erro................: "
            f"{total_erro_lancamento}"
        )

        print("-" * 62)

        print(
            f"Modo de execução.....................: "
            f"{'SIMULACAO - DRY RUN' if dry_run else 'PRODUCAO'}"
        )

        if dry_run:
            print(
                "Motivo...............................: "
                "modo seguro habilitado"
            )

        print("=" * 62)
        print()
