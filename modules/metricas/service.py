import json
import os
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from operations.op455.report import OP455Report
from ssw.client import SSWClient

from modules.metricas import repository as repo
from modules.metricas.processor_v34_compact import processar_op455_snapshot


class MetricasService:
    def __init__(self):
        self.output_dir = Path("downloads/metricas")

    def criar_client_logado(self):
        dominio = os.getenv("METRICAS_SSW_DOMINIO")
        cpf = os.getenv("METRICAS_SSW_CPF")
        usuario = os.getenv("METRICAS_SSW_USUARIO")
        senha = os.getenv("METRICAS_SSW_SENHA")
        unidade = os.getenv("METRICAS_SSW_UNIDADE", "MTZ")

        print("[METRICAS] Configuração de autenticação validada.")

        if not all([dominio, cpf, usuario, senha]):
            raise ValueError("Credenciais METRICAS_SSW_* nao configuradas.")

        client = SSWClient(
            dominio=dominio,
            cpf=cpf,
            usuario=usuario,
            senha=senha,
            unidade=unidade,
        )

        client.login()
        client.open_menu()

        return client

    def _coletar_op455(self, *, dias: int, output_dir: Path) -> tuple[Path, dict]:
        if type(dias) is not int or not 1 <= dias <= 365:
            raise ValueError("Período OP455 fora do intervalo permitido.")
        output_dir.mkdir(parents=True, exist_ok=True)
        client = None
        try:
            client = self.criar_client_logado()
            arquivo = Path(OP455Report(client).gerar_e_baixar(
                output_dir=output_dir,
                dias_periodo=dias,
                timeout_seconds=300,
            ))
            return arquivo, processar_op455_snapshot(arquivo)
        finally:
            if client is not None:
                try:
                    client.session.close()
                except Exception:
                    pass

    def homologar_op455(self, *, dias: int = 1) -> dict:
        """Valida login, geração, download e parser sem escrever em MySQL."""
        if not 1 <= dias <= 30:
            raise ValueError("Homologação OP455 aceita de 1 a 30 dias.")
        with TemporaryDirectory(prefix="nexus-op455-hml-") as temporary:
            arquivo, payload = self._coletar_op455(
                dias=dias, output_dir=Path(temporary)
            )
            total = payload.get("meta", {}).get("total")
            if type(total) is not int or total < 0:
                raise RuntimeError("Parser OP455 retornou total inválido.")
            digest = hashlib.sha256(arquivo.read_bytes()).hexdigest()
            return {
                "success": True,
                "mode": "validate_only",
                "days": dias,
                "total": total,
                "bytes": arquivo.stat().st_size,
                "sha256": digest,
                "database_writes": 0,
            }

    def atualizar_op455(self, triggered_by="manual", triggered_user_id=None, dias=7):
        run_id = repo.criar_execucao(
            source="OP455",
            triggered_by=triggered_by,
            triggered_user_id=triggered_user_id,
        )

        try:
            arquivo, payload = self._coletar_op455(
                dias=dias, output_dir=self.output_dir
            )
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
            safe_error = f"{type(e).__name__}: falha na atualização OP455"
            print("[METRICAS]", safe_error)
            repo.finalizar_execucao_erro(run_id, RuntimeError(safe_error))

            return {
                "success": False,
                "run_id": run_id,
                "error": safe_error,
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
