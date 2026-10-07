import time

import requests


URL_CNPJ = "https://brasilapi.com.br/api/cnpj/v1/{}"


class BrasilAPIRateLimit(Exception):
    pass


class BrasilAPI:
    def consultar(
        self,
        cnpj: str,
        tentativas: int = 4,
    ) -> dict:

        ultimo_erro = None

        for tentativa in range(
            1,
            tentativas + 1,
        ):
            try:
                resposta = requests.get(
                    URL_CNPJ.format(cnpj),
                    timeout=(5, 20),
                )

            except requests.Timeout as exc:
                ultimo_erro = (
                    f"Timeout na tentativa "
                    f"{tentativa}/{tentativas}: {exc}"
                )

                if tentativa < tentativas:
                    time.sleep(
                        2 * tentativa
                    )

                continue

            except requests.ConnectionError as exc:
                ultimo_erro = (
                    f"Erro de conexão na tentativa "
                    f"{tentativa}/{tentativas}: {exc}"
                )

                if tentativa < tentativas:
                    time.sleep(
                        2 * tentativa
                    )

                continue

            except requests.RequestException as exc:
                ultimo_erro = (
                    f"Erro HTTP na tentativa "
                    f"{tentativa}/{tentativas}: {exc}"
                )

                if tentativa < tentativas:
                    time.sleep(
                        2 * tentativa
                    )

                continue

            status_code = resposta.status_code

            # ------------------------------------------
            # SUCESSO
            # ------------------------------------------

            if status_code == 200:
                try:
                    dados = resposta.json()

                except ValueError:
                    return {
                        "status": "ERRO",
                        "dados": None,
                        "erro": "Resposta 200 com JSON inválido",
                    }

                return {
                    "status": "OK",
                    "dados": dados,
                    "http_status": 200,
                }

            # ------------------------------------------
            # NÃO ENCONTRADO
            # ------------------------------------------

            if status_code == 404:
                return {
                    "status": "NAO_ENCONTRADO",
                    "dados": None,
                    "http_status": 404,
                }

            # ------------------------------------------
            # RATE LIMIT
            # ------------------------------------------

            if status_code == 429:
                espera = 5 * tentativa

                ultimo_erro = (
                    f"HTTP 429 na tentativa "
                    f"{tentativa}/{tentativas}"
                )

                if tentativa < tentativas:
                    time.sleep(espera)

                continue

            # ------------------------------------------
            # ERROS TEMPORÁRIOS DO SERVIDOR
            # ------------------------------------------

            if status_code in {
                500,
                502,
                503,
                504,
            }:
                ultimo_erro = (
                    f"HTTP {status_code} na tentativa "
                    f"{tentativa}/{tentativas}"
                )

                if tentativa < tentativas:
                    time.sleep(
                        2 * tentativa
                    )

                continue

            # ------------------------------------------
            # OUTROS STATUS HTTP
            # ------------------------------------------

            return {
                "status": "ERRO",
                "dados": None,
                "http_status": status_code,
                "erro": (
                    f"HTTP {status_code}: "
                    f"{resposta.text[:200]}"
                ),
            }

        # ----------------------------------------------
        # TODAS AS TENTATIVAS FALHARAM
        # ----------------------------------------------

        if ultimo_erro and "HTTP 429" in ultimo_erro:
            raise BrasilAPIRateLimit(
                ultimo_erro
            )

        return {
            "status": "ERRO",
            "dados": None,
            "erro": (
                ultimo_erro
                or "Erro desconhecido na consulta"
            ),
        }


def classificar_regime(
    dados: dict,
) -> str:

    if dados.get("opcao_pelo_mei"):
        return "Simples Nacional MEI"

    if dados.get("opcao_pelo_simples"):
        return "Simples Nacional"

    historico = (
        dados.get("regime_tributario")
        or []
    )

    historico_valido = [
        item
        for item in historico
        if item.get("forma_de_tributacao")
    ]

    if historico_valido:
        ultimo = max(
            historico_valido,
            key=lambda item: int(
                item.get("ano") or 0
            ),
        )

        forma = str(
            ultimo.get(
                "forma_de_tributacao"
            )
            or ""
        ).strip()

        if forma:
            return forma.title()

    return "Não identificado"

def extrair_dados_cache(
    dados: dict,
) -> dict:

    historico = (
        dados.get("regime_tributario")
        or []
    )

    historico_valido = [
        item
        for item in historico
        if item.get("forma_de_tributacao")
    ]

    ano_regime = None

    if historico_valido:
        ultimo = max(
            historico_valido,
            key=lambda item: int(
                item.get("ano") or 0
            ),
        )

        try:
            ano_regime = int(
                ultimo.get("ano")
            )
        except (TypeError, ValueError):
            ano_regime = None

    return {
        "razao_social": (
            dados.get("razao_social")
            or None
        ),
        "ano_regime": ano_regime,
        "optante_simples": (
            dados.get("opcao_pelo_simples")
        ),
        "optante_mei": (
            dados.get("opcao_pelo_mei")
        ),
    }