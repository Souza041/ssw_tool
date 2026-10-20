import os
from datetime import date
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path.cwd() / ".env", override=True)

assert os.getenv("OCORRENCIA_73_DRY_RUN", "").lower() == "true"
assert os.getenv("OCORRENCIA_73_ENABLED", "").lower() == "false"
assert os.getenv("OCORRENCIA_73_MAX_LANCAMENTOS", "") == "0"

from modules.ocorrencia_73 import service as modulo
from modules.ocorrencia_73.parser import carregar_relatorio, filtrar_registros

assert modulo.DRY_RUN is True, "DRY_RUN do servico nao esta ativo"

ARQUIVO = Path(
    "downloads/ocorrencia_73/teste_recebidos/CSVROD00901243.sswweb"
).resolve()

assert ARQUIVO.is_file(), "Relatorio OP455 nao encontrado"

registros = carregar_relatorio(ARQUIVO)
filtrados = filtrar_registros(registros, fluxo="CWB")

assert len(registros) == 234
assert len(filtrados) == 6

DATA_REFERENCIA = date(2026, 10, 19)

def preparar_lotes_local(self, op455, data_referencia, diretorio_original, fluxo=None):
    assert fluxo == "CWB", "Somente CWB autorizado neste teste"

    itens = [
        {
            **item,
            "data_inicial_pesquisa": DATA_REFERENCIA.isoformat(),
            "data_final_pesquisa": DATA_REFERENCIA.isoformat(),
        }
        for item in filtrados
    ]

    return {
        "lotes": [{
            "fluxo": "CWB",
            "data_inicial": DATA_REFERENCIA.isoformat(),
            "data_final": DATA_REFERENCIA.isoformat(),
            "arquivo": str(ARQUIVO),
            "total_relatorio": len(registros),
            "total_filtrado": len(itens),
        }],
        "total_relatorio": len(registros),
        "filtrados": itens,
        "total_filtrado": len(itens),
    }

def proibido(*args, **kwargs):
    raise RuntimeError("OPERACAO PROIBIDA DURANTE HOMOLOGACAO")

servico = modulo.Ocorrencia73Service()

with (
    patch.object(modulo.Ocorrencia73Service, "preparar_lotes_oc73", preparar_lotes_local),
    patch.object(modulo.OP455Report, "gerar_e_baixar_ocorrencia_73", proibido),
    patch.object(modulo.OP101Ocorrencias, "lancar_ocorrencia_73", proibido),
    patch.object(modulo.OP101Ocorrencias, "confirmar_ocorrencia_73", proibido),
):
    resultado = servico.executar(
        data_referencia=DATA_REFERENCIA,
        triggered_by="homologacao_local",
        fluxo="CWB",
    )

assert resultado["dry_run"] is True
assert resultado["total_lancado"] == 0
assert resultado["total_filtrado"] == 6

print("\nHOMOLOGACAO INTEGRADA FINALIZADA")
print("Consultados:", resultado["total_consultado"])
print("Pendentes:", resultado["total_pendente_lancamento"])
print("Bloqueados:", resultado["total_bloqueado"])
print("OC73 existentes:", resultado["total_ja_existia"])
print("Erros tecnicos:", resultado["total_erro_tecnico"])
print("Auditoria:", resultado["resultado_json"])

for item in resultado["itens"]:
    ultima = item.get("ultima_ocorrencia_op101") or {}
    print(
        f"{item['serie']}{item['numero']} | "
        f"ultima={ultima.get('codigo', '-')} | "
        f"status={item['status']}"
    )
