"""Filtro exclusivo por PAGADOR. Não modifica a base bruta."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re

PAGADOR = 'LG ELECTRONICS DO BRASIL'


def corresponde(valor):
    # Os sufixos observados são marcadores do SSW, não parte do nome-base.
    nome = re.sub(r'\s+\((?:B2|B\.)\)\s*$', '', valor.strip(), flags=re.I)
    return ' '.join(nome.upper().split()) == PAGADOR


def filtrar(origem, saida=None):
    origem = Path(origem)
    saida = Path(saida) if saida else origem.with_name(origem.stem + '_LG.csv')
    if origem.resolve() == saida.resolve():
        raise ValueError('A saída deve ser diferente do arquivo bruto.')
    saida.parent.mkdir(parents=True, exist_ok=True)
    parcial = saida.with_suffix(saida.suffix + '.part')
    total = mantidos = 0
    nomes = {}
    header = None
    try:
        # Não substitui um resultado anterior silenciosamente.
        if saida.exists() or saida.with_suffix('.filtro.json').exists():
            raise FileExistsError('Já existe uma saída para esta base. Use outro nome com --saida.')
        with origem.open(encoding='latin1', newline='') as src, parcial.open('x', encoding='utf-8-sig', newline='') as dst:
            reader = csv.reader(src, delimiter=';')
            writer = csv.writer(dst, delimiter=';')
            for row in reader:
                if not row:
                    continue
                tipo = row[0]
                if tipo == '1':
                    if header is not None:
                        raise ValueError('Cabeçalho repetido; layout precisa ser conferido.')
                    header = row
                    if row.count('PAGADOR') != 1:
                        raise ValueError('Coluna PAGADOR ausente ou ambígua.')
                    idx = row.index('PAGADOR')
                    writer.writerow(header[1:])
                elif tipo == '2':
                    if header is None or len(row) != len(header):
                        raise ValueError('Linha de dados incompatível com o cabeçalho.')
                    total += 1
                    if corresponde(row[idx]):
                        writer.writerow(row[1:])
                        mantidos += 1
                        nomes[row[idx]] = nomes.get(row[idx], 0) + 1
                elif tipo not in ('\x13', '0', '9'):
                    raise ValueError('Tipo de registro desconhecido; layout precisa ser conferido.')
        if header is None:
            raise ValueError('Cabeçalho não encontrado.')
        # Retira somente os registros técnicos 0/1/2/9 da representação CSV.
        # Totais do arquivo bruto não são copiados para a base filtrada.
        resumo = {'origem': origem.name, 'saida': saida.name, 'campo': 'PAGADOR',
                  'nome_base': PAGADOR, 'sufixos_aceitos': ['(B2)', '(B.)'],
                  'registros_lidos': total, 'registros_mantidos': mantidos,
                  'registros_excluidos': total - mantidos, 'pagadores_mantidos': nomes,
                  'sha256_origem': hashlib.sha256(origem.read_bytes()).hexdigest(),
                  'sha256_csv': hashlib.sha256(parcial.read_bytes()).hexdigest()}
        parcial.replace(saida)
        saida.with_suffix('.filtro.json').write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding='utf-8')
        return saida, resumo
    except Exception:
        parcial.unlink(missing_ok=True)
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('arquivo', type=Path)
    p.add_argument('--saida', type=Path)
    args = p.parse_args()
    try:
        dest, resumo = filtrar(args.arquivo, args.saida)
    except (OSError, ValueError, csv.Error) as exc:
        p.exit(1, f'Falha no filtro: {exc}\n')
    print(f"Filtro concluído: {resumo['registros_mantidos']} de {resumo['registros_lidos']} registros.")
    print(dest)


if __name__ == '__main__':
    main()
