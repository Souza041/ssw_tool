
from pathlib import Path
from typing import Callable, Any


def importar_snapshot_validado(
    *,
    arquivo: str | Path,
    total: int,
    extraidos: list,
    sem_dados: list,
    falhas_extracao: list,
    importar_auditoria: Callable[..., Any],
    log: Callable[[str], None],
    permitir_sem_dados: bool = False,
) -> dict:
    """
    Controla a importacao segura de snapshots de incidentes.

    Nunca publica automaticamente no MySQL.

    A importacao so acontece quando:
    - Existe pelo menos um CNPJ configurado.
    - Nenhuma extracao falhou.
    - Todos os CNPJs tiveram um desfecho registrado.
    - Nenhum CNPJ ficou sem dados, salvo autorizacao explicita.
    - O arquivo de auditoria existe e nao esta vazio.
    - O dry-run foi concluido sem erros.
    """

    caminho = Path(arquivo)

    def bloqueado(motivo: str) -> dict:
        log(f"MySQL: importacao bloqueada: {motivo}")
        return {
            "status": "BLOQUEADO",
            "motivo": motivo,
            "importado": False,
        }

    if total <= 0:
        return bloqueado("Nenhum CNPJ configurado para extracao.")

    if falhas_extracao:
        return bloqueado(
            f"Falhas de extracao identificadas: {falhas_extracao!r}"
        )

    quantidade_extraidos = len(extraidos)
    quantidade_sem_dados = len(sem_dados)

    if quantidade_extraidos + quantidade_sem_dados != total:
        return bloqueado(
            "Cobertura incompleta de CNPJs. "
            f"Esperados={total}, "
            f"extraidos={quantidade_extraidos}, "
            f"sem_dados={quantidade_sem_dados}."
        )

    # Protecao adicional contra CNPJs duplicados.
    identificadores = [
        (str(grupo), str(cnpj))
        for grupo, cnpj in extraidos + sem_dados
    ]

    if len(set(identificadores)) != len(identificadores):
        return bloqueado(
            "Foram identificados CNPJs duplicados nos resultados."
        )

    if sem_dados and not permitir_sem_dados:
        return bloqueado(
            "Existem CNPJs sem dados. "
            "A importacao exige autorizacao explicita."
        )

    if not caminho.is_file():
        return bloqueado(
            f"Arquivo de auditoria nao encontrado: {caminho}"
        )

    if caminho.stat().st_size <= 0:
        return bloqueado(
            f"Arquivo de auditoria vazio: {caminho}"
        )

    log(
        "MySQL: validando snapshot de auditoria "
        "em modo dry-run..."
    )

    try:
        resultado_simulacao = importar_auditoria(
            caminho,
            publicar=False,
            dry_run=True,
        )
    except Exception as exc:
        log(f"MySQL: falha na simulacao: {exc}")
        raise

    log(
        "MySQL: dry-run concluido. "
        f"Resultado: {resultado_simulacao}"
    )

    log(
        "MySQL: gravando snapshot como VALIDADA, "
        "sem publicacao automatica..."
    )

    try:
        resultado_importacao = importar_auditoria(
            caminho,
            publicar=False,
            dry_run=False,
        )
    except Exception as exc:
        log(f"MySQL: falha na importacao: {exc}")
        raise

    log(
        "MySQL: snapshot importado com sucesso. "
        "A versao ativa nao foi alterada."
    )

    return {
        "status": "VALIDADA",
        "motivo": None,
        "importado": True,
        "resultado": resultado_importacao,
        "simulacao": resultado_simulacao,
        "total_cnpjs": total,
        "extraidos": quantidade_extraidos,
        "sem_dados": quantidade_sem_dados,
        "publicado": False,
    }
