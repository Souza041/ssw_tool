import json
import os
from pathlib import Path

from operations.op455.report import OP455Report
from ssw.client import SSWClient

from modules.metricas import repository as repo
from modules.metricas.processor_v34_compact import processar_op455_snapshot


class MetricasService:
    def __init__(self):
        self.output_dir = Path("downloads/metricas")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def criar_client_logado(self):
        client = SSWClient(
            dominio=os.getenv("METRICAS_SSW_DOMINIO"),
            cpf=os.getenv("METRICAS_SSW_CPF"),
            usuario=os.getenv("METRICAS_SSW_USUARIO"),
            senha=os.getenv("METRICAS_SSW_SENHA"),
            unidade=os.getenv("METRICAS_SSW_UNIDADE", "MTZ"),
        )

        client.login()
        client.open_menu()

        return client

    def atualizar_op455(self, triggered_by="manual", triggered_user_id=None, dias=7):
        run_id = repo.criar_execucao(
            source="OP455",
            triggered_by=triggered_by,
            triggered_user_id=triggered_user_id,
        )

        try:
            client = self.criar_client_logado()
            op455 = OP455Report(client)

            arquivo = op455.gerar_e_baixar(
                output_dir=self.output_dir,
                dias_periodo=dias,
                timeout_seconds=300,
            )

            payload = processar_op455_snapshot(arquivo)
            total = payload["meta"]["total"]

            repo.salvar_snapshot(run_id, payload)

            repo.finalizar_execucao_sucesso(
                run_id=run_id,
                file_name=Path(arquivo).name,
                total_records=total,
            )

            return {
                "success": True,
                "run_id": run_id,
                "file": Path(arquivo).name,
                "total": total,
            }

        except Exception as e:

            print("=" * 80)
            print("ERRO METRICAS:")
            print(repr(e))
            import traceback
            traceback.print_exc()
            print("=" * 80)
            repo.finalizar_execucao_erro(run_id, e)

            return {
                "success": False,
                "run_id": run_id,
                "error": str(e),
            }

    def obter_dashboard(self):
        snap = repo.obter_ultimo_snapshot()

        if not snap:
            return {
                "has_data": False,
                "message": "Nenhuma execução encontrada."
            }

        payload = None

        if snap.get("payload_path"):
            with open(snap["payload_path"], "r", encoding="utf-8") as f:
                payload = json.load(f)
        else:
            payload = snap["payload_json"]
            if isinstance(payload, str):
                payload = json.loads(payload)

        payload["has_data"] = True
        payload["run"] = {
            "id": snap["run_id"],
            "snapshot_id": snap["id"],
            "file_name": snap["file_name"],
            "total_records": snap["total_records"],
            "started_at": snap["started_at"],
            "finished_at": snap["finished_at"],
            "created_at": snap["created_at"],
        }

        def reduzir_item_metricas(item):
            campos = [
                "emissao",
                "diaEmissao",
                "previsao",
                "entrega",
                "status",
                "prazo",
                "diasAtraso",
                "ocorrencia",
                "ocorrenciaDescricao",
                "ocorr73",
                "cliente",
                "uf",
                "cidade",
                "unidade",
                "unidadeReceptora",
                "usuario",
                "romaneio",
                "baixaMobile",
                "h",
                "i",
                "l",
                "operacao",
                "frete",
                "peso",
                "cubagem",
                "volumes",
                "parceiro",
                "cidParceiros",
                "ufParceiro",
                "endereco",
            ]

            return {campo: item.get(campo) for campo in campos}

        payload["DATA"] = [
            reduzir_item_metricas(item)
            for item in payload.get("DATA", [])
        ]

        return payload