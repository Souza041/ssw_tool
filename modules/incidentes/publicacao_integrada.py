
from pathlib import Path
from typing import Callable, Any


class FalhaPublicacaoIntegrada(RuntimeError):
    def __init__(self, etapa: str, resultado: dict, causa: Exception):
        self.etapa = etapa
        self.resultado = resultado
        super().__init__(f"Falha na etapa {etapa}: {causa}")


def finalizar_publicacao_incidentes(
    *,
    arquivo_auditoria: str | Path,
    arquivo_volume: str | Path,
    importar_mysql: bool,
    total: int,
    extraidos: list,
    sem_dados: list,
    falhas_extracao: list,
    importar_auditoria: Callable[..., Any],
    importar_snapshot_validado: Callable[..., dict],
    incident_publisher_factory: Callable,
    volume_publisher_factory: Callable,
    log: Callable[[str], None],
) -> dict:
    """
    Finaliza a publicacao dos incidentes.

    Quando importar_mysql=True:
      1. Valida e importa o snapshot no MySQL sem publica-lo.
      2. Publica o Excel de auditoria.
      3. Publica o Excel de volume.

    Quando importar_mysql=False:
      Mantem o comportamento legado dos dois publishers.

    Nao oferece transacao distribuida entre Excel e MySQL.
    """

    resultado = {
        "mysql": {
            "status": "DESATIVADO",
            "importado": False,
            "publicado": False,
        },
        "auditoria_excel": {
            "status": "PENDENTE",
        },
        "volume_excel": {
            "status": "PENDENTE",
        },
    }

    if importar_mysql:
        log("MySQL: iniciando validacao antes da publicacao Excel...")

        try:
            resultado_mysql = importar_snapshot_validado(
                arquivo=arquivo_auditoria,
                total=total,
                extraidos=extraidos,
                sem_dados=sem_dados,
                falhas_extracao=falhas_extracao,
                importar_auditoria=importar_auditoria,
                log=log,
                permitir_sem_dados=False,
            )
        except Exception as exc:
            resultado["mysql"] = {
                "status": "ERRO",
                "importado": False,
                "publicado": False,
            }
            raise FalhaPublicacaoIntegrada(
                "MYSQL",
                resultado,
                exc,
            ) from exc

        resultado["mysql"] = resultado_mysql

        if not resultado_mysql.get("importado", False):
            motivo = resultado_mysql.get("motivo") or "Importacao bloqueada"
            raise FalhaPublicacaoIntegrada(
                "MYSQL_BLOQUEADO",
                resultado,
                RuntimeError(motivo),
            )

        log(
            "MySQL: snapshot importado como VALIDADA. "
            "Publicacao Excel autorizada."
        )

    try:
        publicacao = incident_publisher_factory().publish(
            arquivo_auditoria
        )
        resultado["auditoria_excel"] = {
            "status": "PUBLICADA",
            **publicacao,
        }
    except Exception as exc:
        resultado["auditoria_excel"] = {"status": "ERRO"}
        raise FalhaPublicacaoIntegrada(
            "AUDITORIA_EXCEL",
            resultado,
            exc,
        ) from exc

    log(
        "Base publicada para o dashboard: "
        f"{publicacao['current']}"
    )
    log(
        "Copia historica gerada: "
        f"{publicacao['history']}"
    )

    if publicacao["history_removed"] > 0:
        log(
            "Historicos antigos removidos: "
            f"{publicacao['history_removed']}"
        )

    try:
        publicacao_volume = volume_publisher_factory().publish(
            arquivo_volume
        )
        resultado["volume_excel"] = {
            "status": "PUBLICADA",
            **publicacao_volume,
        }
    except Exception as exc:
        resultado["volume_excel"] = {"status": "ERRO"}
        raise FalhaPublicacaoIntegrada(
            "VOLUME_EXCEL",
            resultado,
            exc,
        ) from exc

    log(
        "Base de volume publicada para o dashboard: "
        f"{publicacao_volume['current']}"
    )
    log(
        "Historico de volume gerado: "
        f"{publicacao_volume['history']}"
    )

    if publicacao_volume["history_removed"] > 0:
        log(
            "Historicos antigos de volume removidos: "
            f"{publicacao_volume['history_removed']}"
        )

    log("Publicacao integrada finalizada.")

    return resultado
