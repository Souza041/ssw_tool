import csv
import os

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from modules.ocorrencia_73.config import (
    CIDADES_PERMITIDAS,
    CLIENTES_PERMITIDOS,
    DRY_RUN,
    UNIDADE_EMISSORA_PERMITIDA,
)
from modules.ocorrencia_73.parser import (
    carregar_relatorio,
    diagnosticar_filtros,
    filtrar_registros,
    normalizar_sem_acento,
)
from operations.op101.ocorrencias import (
    OP101Ocorrencias,
)
from operations.op455.report import OP455Report
from ssw.client import SSWClient


TIMEZONE = ZoneInfo("America/Sao_Paulo")


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

    def montar_diretorio_execucao(
        self,
        data_referencia: date,
    ) -> Path:
        diretorio = (
            self.base_output_dir
            / data_referencia.isoformat()
        )

        diretorio.mkdir(
            parents=True,
            exist_ok=True,
        )

        return diretorio

    def gerar_auditoria_joi(
        self,
        registros: list[dict],
        diretorio: Path,
    ) -> Path | None:
        if not registros:
            print(
                "[AUDITORIA] Relatório sem registros."
            )
            return None

        coluna_unidade = None

        for coluna in registros[0]:
            if (
                normalizar_sem_acento(coluna)
                == normalizar_sem_acento(
                    "Unidade Emissora"
                )
            ):
                coluna_unidade = coluna
                break

        if not coluna_unidade:
            raise KeyError(
                "Coluna 'Unidade Emissora' "
                "não encontrada para auditoria."
            )

        somente_joi = [
            registro
            for registro in registros
            if normalizar_sem_acento(
                registro.get(coluna_unidade)
            )
            == normalizar_sem_acento(
                UNIDADE_EMISSORA_PERMITIDA
            )
        ]

        print(
            "[AUDITORIA] JOI: "
            f"{len(somente_joi)} registros"
        )

        if not somente_joi:
            return None

        csv_saida = (
            diretorio
            / "auditoria_joi.csv"
        )

        with csv_saida.open(
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as fp:
            writer = csv.DictWriter(
                fp,
                fieldnames=list(
                    somente_joi[0].keys()
                ),
            )

            writer.writeheader()
            writer.writerows(somente_joi)

        return csv_saida

    def executar(
        self,
        data_referencia: date | None = None,
        triggered_by: str = "manual",
    ) -> dict:
        data_referencia = (
            data_referencia
            or datetime.now(TIMEZONE).date()
        )

        diretorio = (
            self.montar_diretorio_execucao(
                data_referencia
            )
        )

        diretorio_original = (
            diretorio
            / "original"
        )

        diretorio_original.mkdir(
            parents=True,
            exist_ok=True,
        )

        data_ssw = data_referencia.strftime(
            "%d%m%y"
        )

        client = self.criar_client_logado()

        op455 = OP455Report(client)

        arquivo = (
            op455.gerar_e_baixar_ocorrencia_73(
                output_dir=diretorio_original,
                data_referencia=data_ssw,
                timeout_seconds=300,
            )
        )

        registros = carregar_relatorio(
            arquivo
        )

        # O diagnóstico precisa ser criado antes
        # de qualquer retorno antecipado.
        diagnostico = diagnosticar_filtros(
            registros=registros,
            clientes_permitidos=(
                CLIENTES_PERMITIDOS
            ),
            cidades_permitidas=(
                CIDADES_PERMITIDAS
            ),
            unidade_emissora=(
                UNIDADE_EMISSORA_PERMITIDA
            ),
        )

        filtrados = filtrar_registros(
            registros=registros,
            clientes_permitidos=(
                CLIENTES_PERMITIDOS
            ),
            cidades_permitidas=(
                CIDADES_PERMITIDAS
            ),
            unidade_emissora=(
                UNIDADE_EMISSORA_PERMITIDA
            ),
        )

        auditoria_joi = (
            self.gerar_auditoria_joi(
                registros=registros,
                diretorio=diretorio,
            )
        )

        if not filtrados:
            return {
                "success": True,
                "triggered_by": triggered_by,
                "dry_run": DRY_RUN,
                "data_referencia": (
                    data_referencia.isoformat()
                ),
                "arquivo": arquivo.name,
                "auditoria_joi": (
                    str(auditoria_joi)
                    if auditoria_joi
                    else None
                ),
                "total_relatorio": len(
                    registros
                ),
                "total_filtrado": 0,
                "diagnostico": diagnostico,
                "total_consultado": 0,
                "total_encontrado_op101": 0,
                "total_nao_encontrado_op101": 0,
                "total_erro_op101": 0,
                "itens": [],
                "message": (
                    "Nenhum CTRC atendeu "
                    "aos filtros."
                ),
            }

        op101 = OP101Ocorrencias(
            client
        )

        itens_consultados = []

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
                consulta = (
                    op101.consultar_ctrc(
                        serie=item["serie"],
                        numero=item["numero"],
                        data_referencia=(
                            data_referencia
                        ),
                    )
                )

                item_processado = {
                    **item,
                    "op101": consulta.to_dict(),
                    "status": (
                        "consultado"
                        if consulta.encontrado
                        else "nao_encontrado"
                    ),
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

        total_encontrado = sum(
            1
            for item in itens_consultados
            if (
                item["status"]
                == "consultado"
            )
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

        return {
            "success": True,
            "triggered_by": triggered_by,
            "dry_run": DRY_RUN,
            "data_referencia": (
                data_referencia.isoformat()
            ),
            "arquivo": arquivo.name,
            "auditoria_joi": (
                str(auditoria_joi)
                if auditoria_joi
                else None
            ),
            "total_relatorio": len(
                registros
            ),
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
            "total_nao_encontrado_op101": (
                total_nao_encontrado
            ),
            "total_erro_op101": (
                total_erro
            ),
            "itens": itens_consultados,
        }