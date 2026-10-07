import csv
import re
from pathlib import Path


def somente_digitos(valor: str) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def validar_cnpj(cnpj: str) -> bool:
    cnpj = somente_digitos(cnpj)

    if len(cnpj) != 14:
        return False

    if cnpj == cnpj[0] * 14:
        return False

    def calcular_digito(base: str, pesos: list[int]) -> str:
        soma = sum(
            int(numero) * peso
            for numero, peso in zip(base, pesos)
        )

        resto = soma % 11

        return "0" if resto < 2 else str(11 - resto)

    primeiro = calcular_digito(
        cnpj[:12],
        [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2],
    )

    segundo = calcular_digito(
        cnpj[:12] + primeiro,
        [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2],
    )

    return cnpj[-2:] == primeiro + segundo


def validar_cpf(cpf: str) -> bool:
    cpf = somente_digitos(cpf)

    if len(cpf) != 11:
        return False

    if cpf == cpf[0] * 11:
        return False

    for tamanho in (9, 10):
        soma = sum(
            int(cpf[i]) * (tamanho + 1 - i)
            for i in range(tamanho)
        )

        digito = (soma * 10) % 11

        if digito == 10:
            digito = 0

        if digito != int(cpf[tamanho]):
            return False

    return True


def classificar_documento(valor: str) -> str:
    documento = somente_digitos(valor)

    if len(documento) != 14:
        return "INVALIDO"

    # CPF exportado pelo SSW com três zeros à esquerda.
    if documento.startswith("000"):
        cpf = documento[3:]

        if validar_cpf(cpf):
            return "CPF"

    if validar_cnpj(documento):
        return "CNPJ"

    return "INVALIDO"


def ler_fornecedores(
    caminho: Path,
) -> tuple[list[str], list[dict]]:

    ultimo_erro = None

    for encoding in (
        "latin-1",
        "cp1252",
        "utf-8-sig",
    ):
        try:
            with caminho.open(
                "r",
                encoding=encoding,
                newline="",
            ) as arquivo:

                # OP535 possui duas linhas antes do
                # cabeçalho real:
                #
                # RODOBRAS TRANSPORTES RODOVIARI
                # ssw023;RELACAO DE FORNECEDORES
                #
                # A terceira linha contém:
                # CPF/CNPJ;NOME;ATIVO;...

                linha_cabecalho = None

                for linha in arquivo:
                    if linha.upper().startswith(
                        "CPF/CNPJ;"
                    ):
                        linha_cabecalho = linha
                        break

                if not linha_cabecalho:
                    raise ValueError(
                        "Cabeçalho CPF/CNPJ não encontrado "
                        "no relatório OP535."
                    )

                colunas = next(
                    csv.reader(
                        [linha_cabecalho],
                        delimiter=";",
                    )
                )

                colunas = [
                    str(coluna or "").strip()
                    for coluna in colunas
                ]

                reader = csv.DictReader(
                    arquivo,
                    fieldnames=colunas,
                    delimiter=";",
                )

                registros = []

                for row in reader:

                    if not row:
                        continue

                    registro = {
                        str(chave or "").strip():
                        str(valor or "").strip()
                        for chave, valor in row.items()
                        if chave is not None
                    }

                    documento = registro.get(
                        "CPF/CNPJ",
                        ""
                    ).strip()

                    if not documento:
                        continue

                    registros.append(
                        registro
                    )

                return colunas, registros

        except UnicodeDecodeError as exc:
            ultimo_erro = exc
            continue

    raise ValueError(
        "Não foi possível identificar a codificação "
        f"do relatório OP535: {ultimo_erro}"
    )