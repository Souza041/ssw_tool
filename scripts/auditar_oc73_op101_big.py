
import csv
import json
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from modules.ocorrencia_73.parser import (
    carregar_relatorio,
    filtrar_registros,
)
from modules.ocorrencia_73.service import (
    Ocorrencia73Service,
    obter_ultima_ocorrencia_op101,
)
from operations.op101.ocorrencias import OP101Ocorrencias


DATA_REFERENCIA = date(2026, 10, 20)

ARQUIVO = (
    ROOT / "downloads" / "ocorrencia_73"
    / "homologacao" / DATA_REFERENCIA.isoformat()
    / "big" / "CSVROD00906462.sswweb"
)

SAIDA = (
    ROOT / "downloads" / "ocorrencia_73"
    / "auditorias" / DATA_REFERENCIA.isoformat()
)


def auditar_item(op101, item):
    resultado = {
        "serie": item["serie"],
        "numero": item["numero"],
        "fluxo": "BIG",
        "ultima_oc_op455": item.get("ultima_ocorrencia"),
        "ultima_oc_op101": None,
        "data_ultima_oc": None,
        "descricao_ultima_oc": None,
        "possui_oc73": None,
        "total_historico": 0,
        "status": "erro_consulta",
        "motivo": "",
    }

    try:
        consulta = op101.consultar_ctrc(
            serie=item["serie"],
            numero=item["numero"],
            data_referencia=DATA_REFERENCIA,
            data_inicial=DATA_REFERENCIA,
            data_final=DATA_REFERENCIA,
        )

        if not consulta.encontrado:
            resultado["status"] = "nao_encontrado"
            resultado["motivo"] = "CTRC nao encontrado na OP101"
            return resultado

        html = op101.abrir_ocorrencias(
            serie=item["serie"],
            numero=item["numero"],
            seq_ctrc=consulta.seq_ctrc,
            local=consulta.local,
            familia=consulta.familia,
            data_referencia=DATA_REFERENCIA,
        )

        historico = op101.listar_ocorrencias(
            html,
            estrito=True,
        )

        resultado["total_historico"] = len(historico)

        possui_73 = any(
            str(oc.codigo).strip() == "73"
            for oc in historico
        )

        resultado["possui_oc73"] = possui_73

        ultima = obter_ultima_ocorrencia_op101(historico)

        if ultima is None:
            resultado["status"] = "bloqueado_historico"
            resultado["motivo"] = (
                "Ultima ocorrencia indeterminada"
            )
            return resultado

        resultado["ultima_oc_op101"] = ultima.codigo
        resultado["data_ultima_oc"] = ultima.data_hora
        resultado["descricao_ultima_oc"] = ultima.descricao

        if possui_73:
            resultado["status"] = "ja_existia"
            resultado["motivo"] = "OC73 ja presente no historico"

        elif str(ultima.codigo).strip() != "64":
            resultado["status"] = "bloqueado_ultima_ocorrencia"
            resultado["motivo"] = (
                f"Esperada OC64; encontrada OC{ultima.codigo}"
            )

        else:
            resultado["status"] = "candidato_tecnico"
            resultado["motivo"] = (
                "Ultima OC64 confirmada; "
                "aguarda validacao operacional"
            )

    except Exception as erro:
        resultado["status"] = "erro_consulta"
        resultado["motivo"] = (
            f"{type(erro).__name__}: {erro}"
        )

    return resultado


def main():
    if not ARQUIVO.is_file():
        raise FileNotFoundError(ARQUIVO)

    registros = carregar_relatorio(ARQUIVO)

    candidatos = filtrar_registros(
        registros,
        fluxo="BIG",
        regra_big_experimental=True,
    )

    print(f"Total OP455: {len(registros)}")
    print(f"Candidatos BIG: {len(candidatos)}")

    if not candidatos:
        print("Nenhum candidato. Encerrando.")
        return

    SAIDA.mkdir(parents=True, exist_ok=True)

    service = Ocorrencia73Service()
    client = service.criar_client_logado()
    op101 = OP101Ocorrencias(client)

    resultados = []

    try:
        for indice, item in enumerate(candidatos, 1):
            identificador = (
                f"{item['serie']}{item['numero']}"
            )

            print(
                f"[{indice}/{len(candidatos)}] "
                f"Consultando {identificador}"
            )

            resultado = auditar_item(op101, item)
            resultados.append(resultado)

            print(
                f"  OP101: {resultado['ultima_oc_op101']} "
                f"| Status: {resultado['status']}"
            )

    finally:
        # Nao presumimos que SSWClient possui close().
        # Encerramos a sessao HTTP somente se disponivel.
        sessao = getattr(client, "session", None)
        fechar = getattr(sessao, "close", None)
        if callable(fechar):
            fechar()

    momento = datetime.now().strftime("%Y%m%d_%H%M%S")

    csv_path = SAIDA / f"auditoria_big_{momento}.csv"
    json_path = SAIDA / f"auditoria_big_{momento}.json"

    with csv_path.open(
        "w", newline="", encoding="utf-8-sig"
    ) as arquivo:
        writer = csv.DictWriter(
            arquivo,
            fieldnames=list(resultados[0].keys()),
            delimiter=";",
        )
        writer.writeheader()
        writer.writerows(resultados)

    with json_path.open(
        "w", encoding="utf-8"
    ) as arquivo:
        json.dump(
            {
                "data_referencia": DATA_REFERENCIA.isoformat(),
                "total_op455": len(registros),
                "total_candidatos": len(candidatos),
                "resultados": resultados,
            },
            arquivo,
            ensure_ascii=False,
            indent=2,
        )

    contagem = Counter(
        item["status"] for item in resultados
    )

    print("\n=== RESUMO AUDITORIA BIG ===")

    for status, total in sorted(contagem.items()):
        print(f"{status}: {total}")

    print("\nCSV:", csv_path)
    print("JSON:", json_path)
    print("\nNENHUMA OCORRENCIA FOI LANCADA.")


if __name__ == "__main__":
    main()
