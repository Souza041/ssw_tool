import csv
import json
import logging
import re
import time
import unicodedata
from html import unescape
from pathlib import Path
from urllib.parse import unquote

from ssw.client import SSWClient
from ssw.settings import settings
from ssw.utils import dummy

logger = logging.getLogger(__name__)


def _valor_log_op156(valor: object, *, numerico: bool = False) -> str:
    texto = "".join(
        " " if unicodedata.category(caractere).startswith("C") else caractere
        for caractere in str(valor or "")
    )
    texto = " ".join(texto.split())
    if numerico:
        return texto if re.fullmatch(r"[0-9]{1,20}", texto) else "[redacted]"
    if (
        re.search(r"[<>]|://|@", texto)
        or re.search(
            r"\b(?:senha|password|token|cookie|authorization|bearer|"
            r"secret|segredo|sess[aã]o|cpf)\b", texto, re.IGNORECASE,
        )
    ):
        return "[redacted]"
    return texto[:80]


def _registrar_rejeicao_op156(motivo: str, job: dict | None = None, **dados) -> None:
    try:
        contexto = {"motivo": motivo, **dados}
        if job is not None:
            contexto.update({
                "sequencia": _valor_log_op156(job.get("sequencia"), numerico=True),
                "situacao_original": _valor_log_op156(job.get("situacao")),
                "situacao_normalizada": _valor_log_op156(
                    _normalizar_estado(job.get("situacao")),
                ),
                "acao_original": _valor_log_op156(job.get("acao")),
                "acao_normalizada": _valor_log_op156(_normalizar_estado(job.get("acao"))),
                "download_id": _valor_log_op156(job.get("download_id"), numerico=True),
                "unidade": _valor_log_op156(job.get("unidade")),
            })
        logger.warning("op156_selecao_rejeitada %s", json.dumps(contexto, ensure_ascii=False))
    except Exception:
        # Uma falha no registro não substitui a rejeição original.
        pass


class RelatorioSemDados(Exception):
    pass


class FilaOP156Invalida(ValueError):
    pass


class FilaOP156Ambigua(ValueError):
    pass


def _normalizar_estado(valor: str) -> str:
    texto = unicodedata.normalize("NFKD", valor or "")
    return " ".join("".join(c for c in texto if not unicodedata.combining(c)).casefold().split())


class OP156Queue:
    def __init__(self, client: SSWClient) -> None:
        self.client = client

    def usuario_atual(self) -> str:
        return getattr(self.client, "usuario", None) or settings.usuario

    def abrir_fila(self, *, retries: int | None = None) -> str:
        if retries is None:
            response = self.client.post("/bin/ssw1440", {"dummy": dummy()})
        else:
            response = self.client.post(
                "/bin/ssw1440", data={"dummy": dummy()}, retries=retries,
            )
        return response.text

    def limpar_html(self, value: str) -> str:
        value = unquote(unescape(value or ""))
        value = re.sub(r"<.*?>", "", value)
        return value.strip()

    def extrair_jobs(
        self,
        html: str,
        opcao_contains: str,
        unidade: str | None = None,
        *,
        estrito: bool = False,
        filtrar_usuario: bool = True,
    ) -> list[dict]:
        if estrito:
            return self._extrair_jobs_estritos(
                html, opcao_contains, unidade, filtrar_usuario=filtrar_usuario,
            )
        html = unquote(unescape(html or ""))

        registros = re.findall(r"<r>(.*?)</r>", html, flags=re.I | re.S)

        jobs = []

        for registro in registros:
            campos = {}

            for idx in range(10):
                match = re.search(
                    rf"<f{idx}>(.*?)</f{idx}>",
                    registro,
                    flags=re.I | re.S,
                )

                campos[f"f{idx}"] = self.limpar_html(match.group(1)) if match else ""

            opcao = campos["f1"]
            usuario = campos["f3"]
            unidade_job = campos["f4"]
            situacao = campos["f6"]
            acao = campos["f8"]

            if opcao_contains not in opcao:
                continue

            usuario_ref = self.usuario_atual()

            if usuario_ref and usuario_ref.lower() not in usuario.lower():
                continue

            if unidade and unidade_job.upper() != unidade.upper():
                continue

            jobs.append({
                "sequencia": campos["f0"],
                "opcao": opcao,
                "data_hora": campos["f2"],
                "usuario": usuario,
                "unidade": unidade_job,
                "situacao": situacao,
                "acao": acao,
                "download_id": campos["f0"],
            })

        jobs.sort(
            key=lambda job: (
                int(job["sequencia"])
                if str(job["sequencia"]).isdigit()
                else 0
            ),
            reverse=True,
        )

        return jobs

    def _extrair_jobs_estritos(
        self, html: str, opcao: str, unidade: str | None,
        *, filtrar_usuario: bool,
    ) -> list[dict]:
        if not isinstance(html, str) or not html.strip():
            raise FilaOP156Invalida("Resposta OP156 vazia ou inválida.")
        html = unquote(unescape(html))
        registros = re.findall(r"<r>(.*?)</r>", html, flags=re.I | re.S)
        aberturas = re.findall(r"<r>", html, flags=re.I)
        fechamentos = re.findall(r"</r>", html, flags=re.I)
        if not registros or len(registros) != len(aberturas) or len(registros) != len(fechamentos):
            raise FilaOP156Invalida("Lista OP156 ausente ou malformada.")

        jobs = []
        usuario_ref = getattr(self.client, "usuario", None)
        for registro in registros:
            campos = {}
            for idx in (0, 1, 3, 4, 6, 8):
                encontrados = re.findall(
                    rf"<f{idx}>(.*?)</f{idx}>", registro, flags=re.I | re.S,
                )
                if len(encontrados) != 1:
                    raise FilaOP156Invalida("Campo obrigatório da OP156 ausente ou duplicado.")
                campos[idx] = self.limpar_html(encontrados[0])
            sequencia = campos[0]
            if not re.fullmatch(r"[0-9]+", sequencia):
                raise FilaOP156Invalida("Sequência OP156 inválida.")
            if not campos[1] or not campos[4] or not campos[6]:
                raise FilaOP156Invalida("Registro OP156 incompleto.")
            if _normalizar_estado(campos[1]) != _normalizar_estado(opcao):
                continue
            if unidade and campos[4].upper() != unidade.upper():
                continue
            if filtrar_usuario and usuario_ref:
                if not campos[3]:
                    raise FilaOP156Invalida("Usuário ausente em candidato OP156.")
                if campos[3].strip().casefold() != usuario_ref.strip().casefold():
                    continue
            jobs.append({
                "sequencia": sequencia,
                "opcao": campos[1],
                "usuario": campos[3],
                "unidade": campos[4],
                "situacao": campos[6],
                "acao": campos[8],
                "download_id": sequencia,
            })
        if len({job["sequencia"] for job in jobs}) != len(jobs):
            raise FilaOP156Ambigua("Sequência OP156 duplicada.")
        return jobs

    def aguardar_download_id(
        self,
        opcao_contains: str,
        unidade: str | None = None,
        timeout_seconds: int = 300,
        intervalo: float = 5,
        ignorar_ids: set[str] | None = None,
        *,
        estrito: bool = False,
    ) -> str:
        if estrito:
            return self._aguardar_download_id_estrito(
                opcao_contains, unidade, timeout_seconds, intervalo, ignorar_ids,
            )
        deadline = time.time() + timeout_seconds
        tentativa = 0

        ignorar_ids = ignorar_ids or set()

        while time.time() < deadline:
            tentativa += 1

            html = self.abrir_fila()
            jobs = self.extrair_jobs(
                html=html,
                opcao_contains=opcao_contains,
                unidade=unidade,
            )

            if tentativa == 1 or tentativa % 5 == 0:
                print(f"[OP156] Aguardando {opcao_contains}... tentativa={tentativa}")

            for job in jobs:
                if job["download_id"] in ignorar_ids:
                    continue

                situacao = job["situacao"].lower()
                acao = job["acao"].lower()

                if "conclu" in situacao and "baixar" in acao:
                    print(f"[OP156] Relatório pronto para download: {job['download_id']}")
                    return job["download_id"]

                if "conclu" in situacao and "nenhum registro" in acao:
                    raise RelatorioSemDados(f"{opcao_contains}: nenhum registro encontrado.")

            time.sleep(intervalo)

        raise TimeoutError(f"Relatório {opcao_contains} não ficou pronto dentro do tempo limite.")

    def _aguardar_download_id_estrito(
        self, opcao: str, unidade: str | None, timeout_seconds: int,
        intervalo: float, ignorar_ids: set[str] | None,
    ) -> str:
        if ignorar_ids is None or not unidade or timeout_seconds <= 0 or intervalo <= 0:
            raise ValueError("Snapshot, unidade e tempos válidos são obrigatórios.")
        if any(not re.fullmatch(r"[0-9]+", item) for item in ignorar_ids):
            raise ValueError("Snapshot OP156 contém sequência inválida.")
        maior_anterior = max((int(item) for item in ignorar_ids), default=-1)
        deadline = time.monotonic() + timeout_seconds
        while True:
            html = self.abrir_fila(retries=1)
            try:
                jobs = self.extrair_jobs(
                    html, opcao, unidade, estrito=True, filtrar_usuario=True,
                )
            except (FilaOP156Invalida, FilaOP156Ambigua) as exc:
                _registrar_rejeicao_op156("lista_op156_invalida" if isinstance(
                    exc, FilaOP156Invalida,
                ) else "lista_op156_ambigua")
                raise
            candidatos = [job for job in jobs if job["sequencia"] not in ignorar_ids]
            if any(int(job["sequencia"]) <= maior_anterior for job in candidatos):
                _registrar_rejeicao_op156(
                    "sequencia_anterior_ausente", candidatos=len(candidatos),
                )
                raise FilaOP156Ambigua("Sequência antiga ausente do snapshot inicial.")
            if len(candidatos) > 1:
                _registrar_rejeicao_op156(
                    "multiplas_sequencias_novas", candidatos=len(candidatos),
                )
                raise FilaOP156Ambigua("Múltiplas sequências novas da OP156.")
            if candidatos:
                job = candidatos[0]
                estado = _normalizar_estado(job["situacao"])
                acao = _normalizar_estado(job["acao"])
                if estado in {"processando", "em processamento"} and not acao:
                    pass
                elif estado == "aguardando" and acao == "excluir":
                    pass
                elif estado in {"concluido", "concluida"}:
                    if acao == "nenhum registro encontrado":
                        raise RelatorioSemDados("OP156 concluiu sem registros.")
                    if acao == "baixar" or re.fullmatch(
                        rf"dow{re.escape(job['sequencia'])}", acao,
                    ):
                        download_id = str(job.get("download_id") or "")
                        if download_id != job["sequencia"] or not re.fullmatch(
                            r"[0-9]+", download_id,
                        ):
                            _registrar_rejeicao_op156("download_id_invalido", job)
                            raise FilaOP156Invalida("Download ID da OP156 inválido.")
                        return download_id
                    _registrar_rejeicao_op156("acao_conclusao_desconhecida", job)
                    raise FilaOP156Invalida("Ação de conclusão OP156 desconhecida.")
                else:
                    _registrar_rejeicao_op156("estado_invalido", job)
                    raise FilaOP156Invalida("Estado OP156 inválido ou terminal sem download.")
            restante = deadline - time.monotonic()
            if restante <= 0:
                raise TimeoutError("Relatório novo da OP156 não concluiu no prazo.")
            time.sleep(min(intervalo, restante))
    
    def extrair_arquivo_direto(self, html: str) -> dict | None:
        html = unquote(unescape(html or ""))

        match = re.search(
            r"abrir\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*,\s*\d+\s*,\s*\d+\s*,\s*['\"]([^'\"]+)['\"]\s*,\s*(\d+)\s*\)",
            html,
            re.IGNORECASE,
        )

        if not match:
            return None

        return {
            "arquivo": match.group(1),
            "nome_download": match.group(2),
            "pasta": match.group(3),
            "tipo": match.group(4),
        }

    def baixar_arquivo(
        self,
        download_id: str,
        output_dir: Path,
        *,
        estrito: bool = False,
    ) -> Path:
        if estrito and not re.fullmatch(r"[0-9]+", str(download_id)):
            raise FilaOP156Invalida("Sequência de download inválida.")
        if not estrito:
            output_dir.mkdir(parents=True, exist_ok=True)
        payload = {"act": f"DOW{download_id}", "dummy": dummy()}
        if estrito:
            response_dow = self.client.post(
                "/bin/ssw1440", data=payload, retries=1,
            )
        else:
            response_dow = self.client.post("/bin/ssw1440", payload)

        response_dow.raise_for_status()
        if estrito and response_dow.status_code != 200:
            raise FilaOP156Invalida("Resposta DOW inesperada.")

        info = self.extrair_arquivo_direto(response_dow.text)

        if not info:
            if estrito:
                raise FilaOP156Invalida("DOW não identificou arquivo direto.")
            output_dir.mkdir(parents=True, exist_ok=True)
            file_path = output_dir / f"ssw_{download_id}.sswweb"
            file_path.write_bytes(response_dow.content)
            return file_path

        if estrito:
            nome = info["nome_download"]
            nome_op455 = re.fullmatch(r"CSVROD(\d+)\.sswweb", nome)
            if (
                len(nome) > 128
                or not re.fullmatch(r"[A-Za-z0-9_-]+\.sswweb", nome)
                or info["arquivo"] != nome
                or info["pasta"] != "/usr/aws/jobs/ROD/"
                or nome_op455 is None
                or int(nome_op455.group(1)) != int(download_id)
            ):
                raise FilaOP156Invalida("Nome ou localização do arquivo inválido.")

        params = {
            "act": info["arquivo"],
            "filename": info["nome_download"],
            "path": info["pasta"],
            "down": "1",
            "nw": "1",
        }
        if estrito:
            response_file = self.client.get(
                "/bin/ssw0424", params=params, retries=1,
            )
        else:
            response_file = self.client.get("/bin/ssw0424", params=params)

        response_file.raise_for_status()
        if estrito:
            tipo = str(response_file.headers.get("Content-Type", ""))
            conteudo = response_file.content
            if response_file.status_code != 200 or tipo.partition(";")[0].casefold() != "application/ssw":
                raise FilaOP156Invalida("Resposta do download não é application/ssw.")
            if not isinstance(conteudo, bytes) or not conteudo:
                raise FilaOP156Invalida("Arquivo OP156 vazio ou inválido.")
            if conteudo.lstrip().lower().startswith((b"<html", b"<!doctype", b"<script")):
                raise FilaOP156Invalida("Download retornou HTML.")
            linhas = conteudo.splitlines()
            if len(linhas) < 2 or b";" not in linhas[1]:
                raise FilaOP156Invalida("Arquivo não possui estrutura tabular esperada.")
            cabecalho = {
                coluna.replace("\xa0", " ").strip().upper()
                for coluna in next(csv.reader(
                    [linhas[1].decode("latin1")], delimiter=";",
                ))
            }
            obrigatorias = {"SERIE/NUMERO CTRC", "CLIENTE PAGADOR", "UNIDADE EMISSORA"}
            cidades = {"CIDADE DO DESTINATARIO", "CIDADE DE ENTREGA"}
            if not obrigatorias <= cabecalho or not cidades & cabecalho:
                raise FilaOP156Invalida("Arquivo não corresponde ao layout OP455 OC73.")

        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / info["nome_download"]
        if estrito:
            try:
                with file_path.open("xb") as destino:
                    destino.write(response_file.content)
            except FileExistsError as exc:
                raise FilaOP156Invalida("Arquivo local já existe.") from exc
        else:
            file_path.write_bytes(response_file.content)

        return file_path

    def aguardar_e_baixar(
        self,
        output_dir: Path,
        opcao_contains: str,
        unidade: str | None = None,
        timeout_seconds: int = 300,
        intervalo: float = 5,
        ignorar_ids: set[str] | None = None,
        *,
        estrito: bool = False,
    ) -> Path:
        argumentos = dict(
            opcao_contains=opcao_contains,
            unidade=unidade,
            timeout_seconds=timeout_seconds,
            intervalo=intervalo,
            ignorar_ids=ignorar_ids,
        )
        if estrito:
            argumentos["estrito"] = True
        download_id = self.aguardar_download_id(**argumentos)

        if estrito:
            return self.baixar_arquivo(download_id, output_dir, estrito=True)
        return self.baixar_arquivo(download_id=download_id, output_dir=output_dir)
    
    def baixar_por_opcao(
        self,
        output_dir: Path,
        opcao: str,
        unidade: str | None = None,
        timeout_seconds: int = 300,
        intervalo: float = 5,
        ignorar_ids: set[str] | None = None,
        *,
        estrito: bool = False,
    ) -> Path:
        """
        Atalho para aguardar um relatório da OP156 e realizar o download.
        Exemplo:
            baixar_por_opcao(output_dir, "455")
            baixar_por_opcao(output_dir, "930")
            baixar_por_opcao(output_dir, "488")
        """
        argumentos = dict(
            output_dir=output_dir,
            opcao_contains=opcao,
            unidade=unidade,
            timeout_seconds=timeout_seconds,
            intervalo=intervalo,
            ignorar_ids=ignorar_ids,
        )
        if estrito:
            argumentos["estrito"] = True
        return self.aguardar_e_baixar(**argumentos)
