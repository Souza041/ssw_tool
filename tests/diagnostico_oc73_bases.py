from pathlib import Path

from modules.ocorrencia_73.parser import (
    carregar_relatorio,
    filtrar_registros,
)

BASES = {
    "CWB": Path("tests/bases_oc73/cwb.sswweb"),
    "BIG": Path("tests/bases_oc73/big.sswweb"),
}

for fluxo, caminho in BASES.items():
    registros = carregar_relatorio(caminho)
    filtrados = filtrar_registros(
        registros,
        fluxo=fluxo,
    )

    print(f"\n{'=' * 50}")
    print(f"FLUXO: {fluxo}")
    print(f"Arquivo: {caminho.name}")
    print(f"Total na base: {len(registros)}")
    print(f"CTRCs elegíveis: {len(filtrados)}")

    for item in filtrados[:10]:
        print(
            f"  {item['ctrc_original']} | "
            f"OC{item['ultima_ocorrencia']} | "
            f"{item['unidade_emissora']} | "
            f"{item['cidade_destinatario']} | "
            f"UF remetente: {item['uf_remetente']}"
        )