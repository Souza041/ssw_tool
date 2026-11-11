"""Diagnóstico somente leitura: python -m scripts.diagnosticar_lg_pod_db."""
from modules.lg_pod.repository import Repository
from modules.lg_pod.db_worker import error_code


def main():
    try:
        with Repository() as repo:
            info = repo.query('''SELECT DATABASE() AS banco, VERSION() AS versao,
                @@SESSION.wait_timeout AS wait_timeout,
                @@SESSION.net_read_timeout AS net_read_timeout,
                @@SESSION.net_write_timeout AS net_write_timeout''', one=True)
            for name,value in info.items():
                print(f'{name}: {value}',flush=True)
            for table in ('lg_pod_execucoes','lg_pod_itens'):
                row = repo.query('SELECT COUNT(*) AS total FROM ' + table, one=True)
                print(f'{table}: {row["total"]} registros',flush=True)
        return 0
    except Exception as exc:
        print(f'Diagnóstico interrompido: {type(exc).__name__}, código={error_code(exc)}',flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
