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

from modules.ocorrencia_73.calendario import (
    calcular_janelas_oc73,
)

TIMEZONE = ZoneInfo("America/Sao_Paulo")

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

OBSERVACAO_LANCAMENTO = (
    os.getenv(
        "OCORRENCIA_73_OBSERVACAO",
        "LANCAMENTO AUTOMATICO - BOT OCORRENCIA 73",
    )
    .strip()
)


class Ocorrencia73Service:
    def __init__(self) -> None:
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
    ) -> dict:
        """
        Gera e filtra os relatórios CWB e BIG.

        Não consulta a OP101 e não lança ocorrências.
        """

        janelas = calcular_janelas_oc73(
            data_referencia
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

        janelas = calcular_janelas_oc73(data_referencia)

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
            }

        client = self.criar_client_logado()
        op455 = OP455Report(client)

        preparacao = self.preparar_lotes_oc73(
            op455=op455,
            data_referencia=data_referencia,
            diretorio_original=diretorio_original,
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

                    if ja_existia:
                        status = "ja_existia"
                        lancamento = None

                    elif DRY_RUN:
                        status = "pendente_lancamento"
                        lancamento = None

                    else:
                        ctrc_chave = (
                            f"{item['serie']}"
                            f"{item['numero']}"
                        )

                        if (
                            CTRC_TESTE
                            and ctrc_chave != CTRC_TESTE
                        ):
                            status = "ignorado_fora_teste"
                            lancamento = None

                        elif (
                            total_tentativas_lancamento
                            >= MAX_LANCAMENTOS
                        ):
                            status = "ignorado_limite"
                            lancamento = None

                        else:
                            total_tentativas_lancamento += 1

                            resultado_lancamento = (
                                op101.lancar_ocorrencia_73(
                                    seq_ctrc=consulta.seq_ctrc,
                                    familia=consulta.familia,
                                    data_ocorrencia=data_referencia,
                                    observacao=(
                                        OBSERVACAO_LANCAMENTO
                                    ),
                                    local=consulta.local,
                                )
                            )

                            confirmada = None

                            if resultado_lancamento.success:
                                confirmada = op101.confirmar_ocorrencia_73(
                                    serie=item["serie"],
                                    numero=item["numero"],
                                    seq_ctrc=consulta.seq_ctrc,
                                    local=consulta.local,
                                    familia=consulta.familia,
                                    data_referencia=data_referencia,
                                )

                            if resultado_lancamento.success and confirmada is not None:
                                status = "lancada"
                                total_lancado += 1
                            else:
                                status = "erro_lancamento"
                                total_erro_lancamento += 1

                            lancamento = {
                                **resultado_lancamento.to_dict(),
                                "confirmada_no_historico": (
                                    confirmada.to_dict()
                                    if confirmada
                                    else None
                                ),
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
                        "ocorrencia_73": (
                            ocorrencia_73.to_dict()
                            if ocorrencia_73
                            else None
                        ),
                        "lancamento": lancamento,
                        "status": status,
                    }

            except Exception as erro:
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

        resumo_auditoria.update({
            "total_consultado_op101": len(itens_consultados),
            "total_encontrado_op101": total_encontrado,
            "total_nao_encontrado_op101": total_nao_encontrado,
            "total_erro_op101": total_erro,
            "total_ja_existia": total_ja_existia,
            "total_pendente_lancamento": total_pendente_lancamento,
            "total_lancado": total_lancado,
            "total_erro_lancamento": total_erro_lancamento,
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
        arquivo_saida = (
            diretorio_auditoria
            / "resultado.json"
        )

        conteudo = json.dumps(
            resultado,
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