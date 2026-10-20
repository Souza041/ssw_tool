
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from modules.ocorrencia_73.parser import carregar_relatorio, filtrar_registros
from modules.ocorrencia_73.service import Ocorrencia73Service
from operations.op101.ocorrencias import OP101Ocorrencias

ARQUIVO = ROOT / "downloads/ocorrencia_73/teste_recebidos/CSVROD00901243.sswweb"

if __name__ == "__main__":
    registros = carregar_relatorio(ARQUIVO)
    candidatos = filtrar_registros(registros, fluxo="CWB")

    print(f"[OC73] Registros: {len(registros)}")
    print(f"[OC73] Candidatos: {len(candidatos)}")

    # Evita consultar documentos inesperados nesta homologacao.
    if len(registros) != 234 or len(candidatos) != 6:
        raise RuntimeError("Relatorio diferente do homologado. Consulta bloqueada.")

    client = Ocorrencia73Service().criar_client_logado()
    op101 = OP101Ocorrencias(client)

    referencia = date(2026, 10, 19)

    for item in candidatos:
        ctrc = item["ctrc_original"]
        print(f"\n[OP101] Consultando {ctrc}")

        try:
            consulta = op101.consultar_ctrc(
                serie=item["serie"],
                numero=item["numero"],
                data_referencia=referencia,
                data_inicial=referencia,
                data_final=referencia,
            )

            if not consulta.encontrado:
                print(f"[OP101] NAO ENCONTRADO: {consulta.mensagem}")
                continue

            html = op101.abrir_ocorrencias(
                serie=item["serie"],
                numero=item["numero"],
                seq_ctrc=consulta.seq_ctrc,
                local=consulta.local,
                familia=consulta.familia,
                data_referencia=referencia,
            )

            historico = op101.listar_ocorrencias(html)
            existente = op101.encontrar_ocorrencia(historico, 73)

            print("[OP101] Historico atual:")

            for ocorrencia in historico:
                print(
                    f"  OC {ocorrencia.codigo} | "
                    f"{ocorrencia.data_hora} | "
                    f"{ocorrencia.descricao}"
                )

            if existente:
                print(f"[OP101] OC73 JA EXISTE | {existente.data_hora}")
            else:
                print("[OP101] OC73 NAO ENCONTRADA NO HISTORICO")

            print(f"[OP101] Total de ocorrencias: {len(historico)}")

        except Exception as exc:
            print(f"[OP101] ERRO: {type(exc).__name__}: {exc}")

    print("\n[OC73] Consulta finalizada. Nenhum lancamento executado.")
