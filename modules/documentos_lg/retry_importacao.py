from time import sleep

from pymysql.err import OperationalError

from .importador import importar


TRANSITORIOS = {
    1205,  # Timeout aguardando bloqueio.
    1213,  # Deadlock.
    2006,  # Conexão encerrada.
    2013,  # Conexão perdida durante a consulta.
}


def importar_com_retry(*args, **kwargs):
    for tentativa in range(1, 4):
        try:
            return importar(*args, **kwargs)

        except OperationalError as exc:
            codigo = exc.args[0] if exc.args else None

            if codigo not in TRANSITORIOS or tentativa == 3:
                raise

            espera = (10, 30)[tentativa - 1]

            print(
                f"[MySQL] Falha transitória {codigo}; "
                f"nova tentativa de importação em {espera}s "
                f"({tentativa + 1}/3).",
                flush=True,
            )
            sleep(espera)
