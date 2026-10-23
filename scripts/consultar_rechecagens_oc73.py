"""Lista a fila BIG sem consultar o SSW nem modificar registros."""
import argparse
import sys
from pathlib import Path
from pprint import pprint

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / '.env', override=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execucao', type=int)
    args = parser.parse_args()
    if args.execucao is not None and args.execucao <= 0:
        parser.error('--execucao deve ser positivo')
    from modules.ocorrencia_73.repository import Ocorrencia73Repository
    sql = '''SELECT id, execucao_id, data_referencia, serie, numero,
        status, consultas, ultimo_codigo, proxima_consulta_em, limite_em,
        execucao_envio_id, tentativa_id, detalhe
        FROM oc73_rechecagens_big'''
    params = ()
    if args.execucao is not None:
        sql += ' WHERE execucao_id=%s'
        params = (args.execucao,)
    sql += ' ORDER BY id DESC LIMIT 100'
    with Ocorrencia73Repository().transacao() as conexao:
        with conexao.cursor() as cursor:
            cursor.execute(sql, params)
            for row in cursor.fetchall():
                pprint(row)


if __name__ == '__main__':
    main()
