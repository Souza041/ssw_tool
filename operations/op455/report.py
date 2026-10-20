
import csv
import hashlib
import re

from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit, urlunsplit

from datetime import datetime, timedelta
from pathlib import Path

from operations.op156.queue import OP156Queue
from ssw.client import SSWClient
from ssw.settings import settings
from ssw.utils import data_ddmmaa, dummy

from types import SimpleNamespace


_FRASE_UNIDADE = "informe a unidade"
_CAMPOS_COM_VALOR_DIAGNOSTICO = {"act", "f3", "reg_tipo"}
_CAMPOS_COM_VALOR_CONTRATO_OP455 = {"act", "f2", "f3", "reg_tipo"}


def _normalizar_texto_diagnostico(valor: str | None) -> str:
    return " ".join((valor or "").casefold().split())


def _valor_contrato_op455_nao_sensivel(nome: str, valor: str) -> bool:
    """Aceita somente códigos curtos; textos livres e tokens ficam omitidos."""
    limites = {
        "act": r"[A-Za-z0-9_-]{0,8}",
        "f2": r"[A-Z0-9]{0,5}",
        "f3": r"[A-Za-z0-9_-]{0,8}",
        "reg_tipo": r"[A-Za-z0-9_-]{0,8}",
    }
    padrao = limites.get(nome.casefold())
    return bool(padrao and re.fullmatch(padrao, valor))


class _OP455HTMLDiagnosticoParser(HTMLParser):
    """Extrai metadados; valores de contexto ficam apenas no parser temporário."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cabecalhos: list[dict] = []
        self._cabecalho: dict | None = None
        self._profundidade_cabecalho = 0
        self.formularios: list[dict] = []
        self._formulario: dict | None = None
        self.estrutura_ambigua = False
        self.localizacoes = {
            "texto_visivel": False,
            "script": False,
            "style": False,
            "comentario": False,
            "atributo": False,
        }
        self.campos: list[dict] = []
        self._contexto: str | None = None
        self._campo_select: dict | None = None
        self._opcao_selecionada: dict | None = None

    @staticmethod
    def _contem_frase(valor: str | None) -> bool:
        return _FRASE_UNIDADE in _normalizar_texto_diagnostico(valor)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        nomes_atributos = [nome.casefold() for nome, _ in attrs]
        if len(nomes_atributos) != len(set(nomes_atributos)):
            self.estrutura_ambigua = True
        atributos = {nome.casefold(): valor for nome, valor in attrs}

        if tag == "div" and self._cabecalho is not None:
            self._profundidade_cabecalho += 1
        if tag == "div" and atributos.get("id") == "infodiv":
            if self._cabecalho is not None:
                self.estrutura_ambigua = True
            self._cabecalho = {"texto": [], "fechado": False}
            self._profundidade_cabecalho = 1
            self.cabecalhos.append(self._cabecalho)

        if tag == "form":
            if self._formulario is not None:
                self.estrutura_ambigua = True
            self._formulario = {
                "id": atributos.get("id") or "",
                "method": (atributos.get("method") or "").casefold(),
                "action": atributos.get("action") or "",
                "campos": {},
                "controles": [],
                "fechado": False,
            }
            self.formularios.append(self._formulario)

        if any(
            self._contem_frase(nome) or self._contem_frase(valor)
            for nome, valor in attrs
        ):
            self.localizacoes["atributo"] = True

        if tag in {"script", "style"}:
            self._contexto = tag

        if tag in {"input", "select", "textarea", "button"}:
            nome = atributos.get("name") or ""
            tipo = atributos.get("type") or (
                "select" if tag == "select" else tag
            )
            campo = {
                "nome": nome,
                "tipo": tipo.casefold(),
                "selecionado": "checked" in atributos,
                "opcoes_selecionadas": [],
            }
            nome_normalizado = nome.casefold()
            if nome_normalizado in (
                _CAMPOS_COM_VALOR_DIAGNOSTICO
                | _CAMPOS_COM_VALOR_CONTRATO_OP455
            ):
                campo["valor"] = atributos.get("value") or ""
            if tag == "select" and nome_normalizado in _CAMPOS_COM_VALOR_CONTRATO_OP455:
                campo["opcoes"] = []
            self.campos.append(campo)
            if self._formulario is not None:
                self._formulario["controles"].append(campo)
            if tag == "select":
                self._campo_select = campo

            if (
                tag == "input" and self._formulario is not None
                and self._formulario["id"] == "frm"
                and nome.casefold() in {"f2", "f3"}
            ):
                self._formulario["campos"].setdefault(nome.casefold(), []).append({
                    "id": atributos.get("id"),
                    "value": atributos.get("value"),
                })

        if tag == "option" and self._campo_select is not None:
            nome_select = self._campo_select["nome"].casefold()
            selecionada = "selected" in atributos
            if nome_select in _CAMPOS_COM_VALOR_CONTRATO_OP455:
                opcao = {
                    "selecionada": selecionada,
                    "valor": atributos.get("value") or "",
                }
                self._campo_select["opcoes"].append(opcao)
                if selecionada:
                    self._campo_select["opcoes_selecionadas"].append(opcao)
                self._opcao_selecionada = opcao
            elif selecionada:
                opcao = {"selecionada": True}
                self._campo_select["opcoes_selecionadas"].append(opcao)
                self._opcao_selecionada = opcao

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "form":
            if self._formulario is not None:
                self._formulario["fechado"] = True
            self._formulario = None
        elif tag == "div" and self._cabecalho is not None:
            self._profundidade_cabecalho -= 1
            if self._profundidade_cabecalho == 0:
                self._cabecalho["fechado"] = True
                self._cabecalho = None
        elif tag in {"script", "style"}:
            self._contexto = None
        elif tag == "option":
            self._opcao_selecionada = None
        elif tag == "select":
            self._campo_select = None

    def handle_data(self, data: str) -> None:
        if self._cabecalho is not None and self._contexto is None:
            self._cabecalho["texto"].append(data)
        if self._contem_frase(data):
            self.localizacoes[self._contexto or "texto_visivel"] = True

        if (
            self._opcao_selecionada is not None
            and self._campo_select is not None
            and self._campo_select["nome"].casefold()
            in _CAMPOS_COM_VALOR_CONTRATO_OP455
        ):
            texto = " ".join(data.split())
            if texto:
                self._opcao_selecionada["texto"] = texto

    def handle_comment(self, data: str) -> None:
        if self._contem_frase(data):
            self.localizacoes["comentario"] = True


def _url_sanitizada(url: str | None) -> str:
    partes = urlsplit(url or "")
    host = partes.hostname or ""
    if partes.port:
        host = f"{host}:{partes.port}"
    return urlunsplit((partes.scheme, host, partes.path, "", ""))


class OP455Report:
    def __init__(self, client: SSWClient) -> None:
        self.client = client
        self._contexto_oc73_confirmado_unidade: str | None = None

    def open(self, unidade: str = "MTZ"):
        self._contexto_oc73_confirmado_unidade = None
        unidade = str(unidade or "").strip().upper()
        if not unidade:
            raise ValueError("Unidade não informada para abertura da OP455.")

        # Reproduz a troca de unidade disparada pelo onchange do menu.
        self.client.get(
            "/bin/ssw0082",
            params={
                "quadro_menu01": unidade,
                "dummy": dummy(),
            },
        )
        return self.client.open_option("455", unidade)

    @staticmethod
    def validar_contexto_abertura_ocorrencia_73(
        response, unidade: str,
    ) -> dict:
        """Confirma CWB apenas com o HTML da abertura, sem guardar seu corpo."""
        if unidade != "CWB":
            return {"status": "nao_confirmado", "motivo": "sem_evidencia_html_big"}

        try:
            status_http = int(getattr(response, "status_code", 0) or 0)
        except (TypeError, ValueError):
            status_http = 0
        if not 200 <= status_http < 300:
            return {"status": "nao_confirmado", "motivo": "resposta_abertura_invalida"}
        content_type = str((getattr(response, "headers", {}) or {}).get("Content-Type", ""))
        if content_type.partition(";")[0].strip().casefold() not in {
            "text/html", "application/xhtml+xml",
        }:
            return {"status": "nao_confirmado", "motivo": "resposta_abertura_nao_html"}

        parser = _OP455HTMLDiagnosticoParser()
        try:
            parser.feed(getattr(response, "text", ""))
            parser.close()
        except (TypeError, ValueError):
            return {"status": "nao_confirmado", "motivo": "html_abertura_invalido"}

        if parser.estrutura_ambigua or len(parser.cabecalhos) > 1:
            return {"status": "nao_confirmado", "motivo": "estrutura_ambigua"}
        if not parser.cabecalhos:
            return {"status": "nao_confirmado", "motivo": "dominio_ausente"}
        cabecalho = parser.cabecalhos[0]
        if not cabecalho["fechado"]:
            return {"status": "nao_confirmado", "motivo": "html_incompleto"}
        texto_cabecalho = " ".join(" ".join(cabecalho["texto"]).split())
        dominios = re.findall(
            r"dom[ií]nio\s*:\s*([A-Z0-9]+)\s+([A-Z0-9]+)(?![A-Z0-9_-])",
            texto_cabecalho, flags=re.IGNORECASE,
        )
        if not dominios and not re.search(r"dom[ií]nio\s*:", texto_cabecalho, re.IGNORECASE):
            return {"status": "nao_confirmado", "motivo": "dominio_ausente"}
        if len(dominios) != 1 or tuple(parte.upper() for parte in dominios[0]) != ("ROD", "CWB"):
            return {"status": "nao_confirmado", "motivo": "dominio_divergente"}

        formulario = [
            form for form in parser.formularios
            if form["id"] == "frm"
        ]
        if not formulario:
            return {"status": "nao_confirmado", "motivo": "formulario_ausente"}
        if len(formulario) > 1:
            return {"status": "nao_confirmado", "motivo": "formulario_duplicado"}
        formulario = formulario[0]
        if not formulario["fechado"]:
            return {"status": "nao_confirmado", "motivo": "html_incompleto"}
        if formulario["method"] != "post":
            return {"status": "nao_confirmado", "motivo": "metodo_invalido"}
        if formulario["action"] != "/bin/ssw0230":
            return {"status": "nao_confirmado", "motivo": "action_invalida"}

        for nome, rotulo, identificador, esperado in (
            ("f2", "unidade", "2", "CWB"), ("f3", "tipo", "3", "A"),
        ):
            encontrados = formulario["campos"].get(nome, [])
            if not encontrados:
                return {"status": "nao_confirmado", "motivo": f"campo_{rotulo}_ausente"}
            if len(encontrados) > 1:
                return {"status": "nao_confirmado", "motivo": f"campo_{rotulo}_duplicado"}
            if encontrados[0]["id"] != identificador or encontrados[0]["value"] != esperado:
                return {"status": "nao_confirmado", "motivo": f"{rotulo}_divergente"}
        return {"status": "confirmado"}

    @staticmethod
    def extrair_contrato_formulario_op455(response) -> dict:
        """Extrai somente o contrato sanitizado do formulário aberto em memória."""
        parser = _OP455HTMLDiagnosticoParser()
        try:
            parser.feed(str(getattr(response, "text", "")))
            parser.close()
        except (TypeError, ValueError):
            return {"status": "inconclusivo", "motivo": "html_invalido"}

        formularios = [
            formulario for formulario in parser.formularios
            if formulario["action"] == "/bin/ssw0230"
        ]
        if len(formularios) != 1:
            return {
                "status": "inconclusivo",
                "motivo": (
                    "formulario_ausente" if not formularios
                    else "formulario_duplicado"
                ),
            }

        formulario = formularios[0]
        if not formulario["fechado"]:
            return {"status": "inconclusivo", "motivo": "html_incompleto"}

        controles = []
        campos_ocultos = []
        for campo in formulario["controles"]:
            nome = str(campo.get("nome") or "")
            tipo = str(campo.get("tipo") or "")
            controle = {"nome": nome, "tipo": tipo}
            nome_normalizado = nome.casefold()

            if tipo == "hidden" and nome:
                campos_ocultos.append(nome)
            if nome_normalizado in _CAMPOS_COM_VALOR_CONTRATO_OP455:
                valor = str(campo.get("valor") or "")
                if _valor_contrato_op455_nao_sensivel(nome_normalizado, valor):
                    controle["valor"] = valor
                else:
                    controle["valor_classificacao"] = "omitido_nao_classificado"
                if tipo == "select":
                    controle["opcoes"] = []
                    for opcao in campo.get("opcoes", []):
                        valor_opcao = str(opcao.get("valor") or "")
                        opcao_sanitizada = {
                            "selecionada": bool(opcao.get("selecionada")),
                        }
                        if _valor_contrato_op455_nao_sensivel(
                            nome_normalizado, valor_opcao,
                        ):
                            opcao_sanitizada["valor"] = valor_opcao
                        else:
                            opcao_sanitizada["valor_classificacao"] = (
                                "omitido_nao_classificado"
                            )
                        controle["opcoes"].append(opcao_sanitizada)
            controles.append(controle)

        return {
            "status": "extraido",
            "metodo": formulario["method"],
            "action": formulario["action"],
            "controles": controles,
            "campos_ocultos": sorted(set(campos_ocultos)),
            "valores_expostos_somente_para": sorted(
                _CAMPOS_COM_VALOR_CONTRATO_OP455
            ),
            "textos_opcoes_expostos": False,
            "conclusao_payload_submit": "nao_inferida",
        }

    def pre_voo_navegacao_ocorrencia_73_cwb(self) -> dict:
        """Valida somente a abertura da OP455 CWB e encerra o fluxo.

        Este caminho não confirma o contexto interno usado pelos métodos de
        geração. Assim, mesmo depois de um pré-voo aprovado, uma tentativa de
        solicitar relatório com a mesma instância continua bloqueada.
        """
        self._contexto_oc73_confirmado_unidade = None

        try:
            response = self._abrir_op455_oc73_cwb(unidade="CWB")
        except Exception as exc:
            return {
                "resultado": "erro_navegacao",
                "unidade": "CWB",
                "contexto": None,
                "erro_tipo": type(exc).__name__,
            }

        contexto = self.validar_contexto_abertura_ocorrencia_73(
            response,
            "CWB",
        )
        contrato_formulario = self.extrair_contrato_formulario_op455(response)
        conteudo = getattr(response, "content", None)
        if conteudo is None:
            conteudo = str(getattr(response, "text", "")).encode(
                "utf-8", errors="replace",
            )
        elif not isinstance(conteudo, bytes):
            conteudo = bytes(conteudo)

        try:
            status_http = int(getattr(response, "status_code", 0) or 0)
        except (TypeError, ValueError):
            status_http = 0
        content_type_recebido = str(
            (getattr(response, "headers", {}) or {}).get("Content-Type", "")
        )
        content_type_base = content_type_recebido.partition(";")[0].strip().casefold()
        content_type = (
            content_type_base
            if content_type_base in {"text/html", "application/xhtml+xml"}
            else "outro"
        )

        # Defesa em profundidade: o pré-voo nunca libera o método de submit.
        self._contexto_oc73_confirmado_unidade = None
        return {
            "resultado": (
                "contexto_confirmado"
                if contexto["status"] == "confirmado"
                else "contexto_nao_confirmado"
            ),
            "unidade": "CWB",
            "contexto": contexto,
            "contrato_formulario": contrato_formulario,
            "resposta": {
                "status_http": status_http,
                "url_final": _url_sanitizada(getattr(response, "url", "")),
                "content_type": content_type,
                "tamanho_bytes": len(conteudo),
                "sha256_corpo": hashlib.sha256(conteudo).hexdigest(),
            },
        }

    @staticmethod
    def resumir_resposta_diagnostica(response) -> dict:
        """Produz evidência sanitizada sem devolver corpo ou cabeçalhos sensíveis."""
        conteudo = getattr(response, "content", None)
        if conteudo is None:
            conteudo = str(getattr(response, "text", "")).encode(
                "utf-8", errors="replace"
            )
        elif not isinstance(conteudo, bytes):
            conteudo = bytes(conteudo)

        headers = getattr(response, "headers", {}) or {}
        content_type = str(headers.get("Content-Type", ""))
        status = int(getattr(response, "status_code", 0) or 0)
        tipo_base = content_type.partition(";")[0].strip().casefold()
        html_esperado = tipo_base in {"text/html", "application/xhtml+xml"}

        parser = _OP455HTMLDiagnosticoParser()
        texto = str(getattr(response, "text", ""))
        erro_parser = False
        try:
            parser.feed(texto)
            parser.close()
        except (ValueError, TypeError):
            erro_parser = True

        if status < 200 or status >= 300:
            classificacao = "erro_http"
        elif not conteudo or not html_esperado or erro_parser:
            classificacao = "resposta_inesperada"
        else:
            classificacao = "html_valido"

        return {
            "status_http": status,
            "url_final": _url_sanitizada(getattr(response, "url", "")),
            "content_type": content_type,
            "tamanho_bytes": len(conteudo),
            "sha256_corpo": hashlib.sha256(conteudo).hexdigest(),
            "classificacao_resposta": classificacao,
            "frase_informe_unidade": {
                nome: presente
                for nome, presente in parser.localizacoes.items()
            },
            "campos_formulario": parser.campos,
        }

    @staticmethod
    def _ids_jobs(jobs: list[dict]) -> set[str]:
        return {
            str(job.get("download_id") or "").strip()
            for job in jobs
            if str(job.get("download_id") or "").strip()
        }

    def _snapshot_op156_diagnostico(
        self,
        fila: OP156Queue,
        opcao: str,
        unidade: str,
    ) -> set[str]:
        if self._contexto_oc73_confirmado_unidade != unidade:
            raise ValueError("Contexto da OP455 não confirmado para OP156.")
        response = self.client.post(
            "/bin/ssw1440",
            data={"dummy": dummy()},
            retries=1,
        )
        jobs = fila.extrair_jobs(
            html=response.text,
            opcao_contains=opcao,
            unidade=unidade,
        )
        return self._ids_jobs(jobs)

    def gerar_relatorio(
        self,
        data_inicial: datetime,
        data_final: datetime,
    ) -> str:
        payload = {
            "act": "E1",
            "cod_emp_ctb": "00",
            "f9": data_ddmmaa(data_inicial),
            "f10": data_ddmmaa(data_final),
            "f8": "T",
            "f18": "T",
            "f19": "T",
            "f20": "S",
            "f21": "X",
            "f22": "T",
            "f23": "A",
            "f25": "T",
            "f26": "A",
            "f27": "A",
            "f28": "T",
            "ibscbs": "A",
            "f29": "A",
            "f30": "A",
            "f35": "E",
            "f37": "B",
            "f38": "F",
            "f39": "",
            "basico": "N",
            "dummy": dummy(),
        }

        response = self.client.post("/bin/ssw0230", payload)
        return response.text

    def extrair_arquivo_direto(self, html: str) -> dict | None:
        decoded = unquote(html)

        match = re.search(
            r"abrir\s*\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*\d+\s*,\s*\d+\s*,\s*'([^']+)'\s*,\s*(\d+)\s*\)",
            decoded,
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

    def baixar_arquivo_direto(
        self,
        info: dict,
        output_dir: Path,
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)

        response = self.client.get(
            "/bin/ssw0424",
            params={
                "act": info["arquivo"],
                "filename": info["nome_download"],
                "path": info["pasta"],
                "down": "1",
                "nw": "1",
            },
        )

        response.raise_for_status()

        file_path = output_dir / info["nome_download"]

        file_path.write_bytes(response.content)

        return file_path

    def _extrair_nome_arquivo(self, headers) -> str | None:
        content_disposition = headers.get("Content-Disposition", "")

        match = re.search(r'filename="?([^"]+)"?', content_disposition)

        if match:
            return match.group(1)

        return None

    def gerar_e_baixar(
        self,
        output_dir: Path,
        dias_periodo: int = 7,
        timeout_seconds: int = 180,
    ) -> Path:
        hoje = datetime.now()
        inicio = hoje - timedelta(days=dias_periodo)

        self.open(unidade=self.client.unidade)
        html = self.gerar_relatorio(inicio, hoje)

        if "Informe a unidade" in html:
            raise ValueError("SSW retornou: Informe a unidade. Verifique a unidade usada para abrir a OP455.")

        info_direto = self.extrair_arquivo_direto(html)

        if info_direto:
            return self.baixar_arquivo_direto(
                info=info_direto,
                output_dir=output_dir,
            )

        fila = OP156Queue(self.client)

        return fila.baixar_por_opcao(
            output_dir=output_dir,
            opcao="455 - Fretes Expedidos/Recebidos - CTRCs",
            unidade=self.client.unidade,
            timeout_seconds=timeout_seconds,
            intervalo=5,
        )

    def gerar_e_baixar_por_datas(
        self,
        output_dir: Path,
        data_inicial: str,
        data_final: str,
        timeout_seconds: int = 300,
    ) -> Path:
        self.open()
        html = self.gerar_relatorio_por_datas(
            data_inicial=data_inicial,
            data_final=data_final,
        )

        if "Informe a unidade" in html:
            raise ValueError(
                "SSW retornou: Informe a unidade. Verifique a unidade usada na OP455."
            )

        info_direto = self.extrair_arquivo_direto(html)

        if info_direto:
            return self.baixar_arquivo_direto(
                info=info_direto,
                output_dir=output_dir,
            )

        fila = OP156Queue(self.client)

        return fila.baixar_por_opcao(
            output_dir=output_dir,
            opcao="455 - Fretes Expedidos/Recebidos - CTRCs",
            unidade="MTZ",
            timeout_seconds=timeout_seconds,
            intervalo=5,
        )

    def gerar_relatorio_por_datas(
        self,
        data_inicial: str,
        data_final: str,
        retries: int | None = None,
        *,
        retornar_resposta: bool = False,
    ):
        payload = {
            "act": "E1",
            "cod_emp_ctb": "00",

            "f9": data_inicial,
            "f10": data_final,

            "f8": "T",
            "f18": "T",
            "f19": "T",
            "f20": "S",
            "f21": "X",
            "f22": "T",
            "f23": "A",
            "f25": "T",
            "f26": "A",
            "f27": "A",
            "f28": "T",
            "ibscbs": "A",
            "f29": "A",
            "f30": "A",

            "f35": "E",

            # Dados complementares
            "f37": "F",
            "f38": "B",
            "f39": "",

            "basico": "N",
            "dummy": dummy(),
        }

        if retries is None:
            response = self.client.post("/bin/ssw0230", payload)
        else:
            response = self.client.post(
                "/bin/ssw0230", data=payload, retries=retries,
            )

        return response if retornar_resposta else response.text

    def gerar_relatorio_documentos(
        self,
        data_inicial: str,
        data_final: str,
    ) -> str:
        """Gera a OP455 com o layout exigido pelo módulo Documentos GCE."""
        payload = {
            "act": "E1",
            "cod_emp_ctb": "00",
            "f3": "A",
            "reg_tipo": "E",
            "f5": "R",
            "f8": "T",
            "f9": data_inicial,
            "f10": data_final,
            "f18": "T",
            "f19": "T",
            "f20": "S",
            "f21": "X",
            "f22": "T",
            "f23": "A",
            "f25": "T",
            "f26": "A",
            "f27": "A",
            "f28": "T",
            "ibscbs": "A",
            "f29": "A",
            "f30": "A",
            "f35": "e",
            "f37": "F",
            "f38": "C",
            "f39": "D",
            "basico": "N",
            "dummy": dummy(),
        }
        response = self.client.post("/bin/ssw0230", payload)
        return response.text

    def gerar_e_baixar_documentos(
        self,
        output_dir: Path,
        data_inicial: str,
        data_final: str,
        timeout_seconds: int = 300,
    ) -> Path:
        opcao = "455 - Fretes Expedidos/Recebidos - CTRCs"
        unidade = "MTZ"

        fila = OP156Queue(self.client)

        # Abre obrigatoriamente a OP455 em MTZ.
        self.open(unidade=unidade)

        # Guarda todos os relatórios 455 que já estavam na fila.
        ids_existentes = self.capturar_ids_fila(
            fila=fila,
            opcao=opcao,
            unidade=unidade,
        )

        print(
            "[OP455 DOCUMENTOS] IDs existentes antes da solicitação: "
            f"{sorted(ids_existentes)}"
        )

        # Solicita especificamente o relatório de Documentos.
        html = self.gerar_relatorio_documentos(
            data_inicial=data_inicial,
            data_final=data_final,
        )

        if "Informe a unidade" in html:
            raise ValueError(
                "SSW retornou: Informe a unidade. "
                "Verifique a unidade MTZ na OP455."
            )

        # Alguns relatórios podem vir diretamente na resposta.
        info_direto = self.extrair_arquivo_direto(html)

        if info_direto:
            arquivo = self.baixar_arquivo_direto(
                info=info_direto,
                output_dir=output_dir,
            )

            print(
                "[OP455 DOCUMENTOS] Arquivo baixado diretamente: "
                f"{arquivo.name}"
            )

            return arquivo

        # Se foi para a OP156, aceita SOMENTE um ID novo,
        # criado depois da solicitação acima.
        arquivo = fila.baixar_por_opcao(
            output_dir=output_dir,
            opcao=opcao,
            unidade=unidade,
            timeout_seconds=timeout_seconds,
            intervalo=5,
            ignorar_ids=ids_existentes,
        )

        print(
            "[OP455 DOCUMENTOS] Novo relatório baixado: "
            f"{arquivo.name}"
        )

        return arquivo

    def _abrir_op455_oc73_cwb(self, unidade: str = "CWB"):
        """Navega até a OP455 CWB na sessão existente, sem retentativas."""
        unidade = unidade.strip().upper()

        if unidade != "CWB":
            raise ValueError(
                "Abertura OC73 disponível apenas para CWB."
            )

        self._contexto_oc73_confirmado_unidade = None

        self.client.get(
            "/bin/ssw0082",
            params={
                "quadro_menu01": unidade,
                "dummy": dummy(),
            },
            retries=1,
        )

        self.client.post(
            "/bin/menu01",
            data={
                "act": "TRO",
                "f2": unidade,
                "f3": "455",
                "dummy": dummy(),
            },
            retries=1,
        )

        return self.client.post(
            "/bin/ssw0230",
            data={
                "sequencia": "455",
                "dummy": dummy(),
            },
            retries=1,
        )

    def diagnosticar_geracao_ocorrencia_73(
        self,
        data_inicial: str,
        data_final: str,
        unidade: str = "CWB",
    ) -> dict:
        """Pré-voo e diagnóstico sem download ou persistência."""
        unidade = unidade.strip().upper()
        if unidade not in {"CWB", "BIG"}:
            raise ValueError(f"Unidade não permitida para OC73: {unidade}")

        inicio = datetime.strptime(data_inicial, "%d%m%y").date()
        fim = datetime.strptime(data_final, "%d%m%y").date()
        if inicio > fim:
            raise ValueError(
                "A data inicial não pode ser posterior à data final."
            )

        # O diagnóstico nunca deve herdar uma autorização anterior.
        self._contexto_oc73_confirmado_unidade = None

        opcao = "455 - Fretes Expedidos/Recebidos - CTRCs"
        fila = OP156Queue(self.client)
        resultado = {
            "unidade": unidade,
            "unidade_contexto_op455": None,
            "contexto_op455": None,
            "resultado": "inconclusivo",
            "resposta_op455": None,
            "op156": {
                "consultas_antes": 0,
                "consultas_depois": 0,
                "total_antes": None,
                "total_depois": None,
                "novos_relatorios": None,
                "novo_relatorio_detectado": None,
            },
            "download_executado": False,
        }

        try:
            try:
                resposta_abertura = self._abrir_op455_oc73_cwb(unidade=unidade)
                contexto = self.validar_contexto_abertura_ocorrencia_73(
                    resposta_abertura, unidade,
                )
                resultado["contexto_op455"] = contexto
                if contexto["status"] != "confirmado":
                    resultado["resultado"] = "contexto_op455_nao_confirmado"
                    return resultado
                resultado["unidade_contexto_op455"] = unidade
                self._contexto_oc73_confirmado_unidade = unidade
                ids_antes = self._snapshot_op156_diagnostico(
                    fila=fila,
                    opcao=opcao,
                    unidade=unidade,
                )
            except Exception as exc:
                resultado["resultado"] = "erro_pre_voo"
                resultado["erro_tipo"] = type(exc).__name__
                return resultado
            resultado["op156"]["consultas_antes"] = 1
            resultado["op156"]["total_antes"] = len(ids_antes)

            try:
                response = self.gerar_relatorio_por_datas(
                    data_inicial=data_inicial,
                    data_final=data_final,
                    retries=1,
                    retornar_resposta=True,
                )
            except Exception as exc:
                resultado["resultado"] = "erro_submit"
                resultado["erro_tipo"] = type(exc).__name__
                return resultado

            resumo = self.resumir_resposta_diagnostica(response)
            resultado["resposta_op455"] = resumo
            if resumo["classificacao_resposta"] != "html_valido":
                resultado["resultado"] = resumo["classificacao_resposta"]
                return resultado

            try:
                ids_depois = self._snapshot_op156_diagnostico(
                    fila=fila,
                    opcao=opcao,
                    unidade=unidade,
                )
            except Exception as exc:
                resultado["resultado"] = "erro_op156_posterior"
                resultado["erro_tipo"] = type(exc).__name__
                return resultado
            novos = ids_depois - ids_antes
            resultado["op156"].update({
                "consultas_depois": 1,
                "total_depois": len(ids_depois),
                "novos_relatorios": len(novos),
                "novo_relatorio_detectado": bool(novos),
            })

            localizacoes = resumo["frase_informe_unidade"]
            erro_visivel = localizacoes["texto_visivel"]
            marcador_direto = self.extrair_arquivo_direto(response.text) is not None
            resultado["marcador_arquivo_direto"] = marcador_direto

            if erro_visivel:
                resultado["resultado"] = (
                    "resposta_divergente"
                    if novos or marcador_direto
                    else "erro_unidade_visivel"
                )
            elif novos or marcador_direto:
                resultado["resultado"] = "submit_aceito"
            elif any(localizacoes.values()):
                resultado["resultado"] = "frase_apenas_nao_visivel"

            return resultado
        finally:
            self._contexto_oc73_confirmado_unidade = None

    def gerar_e_baixar_ocorrencia_73(
        self,
        output_dir: Path,
        data_inicial: str,
        data_final: str,
        unidade: str = "CWB",
        timeout_seconds: int = 300,
    ) -> Path:
        """
        Gera e baixa um relatório OP455 específico
        para a automação OC73.

        Unidades permitidas: CWB e BIG.
        """

        unidade = unidade.strip().upper()

        if unidade not in {"CWB", "BIG"}:
            raise ValueError(
                f"Unidade não permitida para OC73: {unidade}"
            )

        inicio = datetime.strptime(data_inicial, "%d%m%y").date()
        fim = datetime.strptime(data_final, "%d%m%y").date()

        if inicio > fim:
            raise ValueError(
                "A data inicial não pode ser posterior à data final."
            )

        opcao = "455 - Fretes Expedidos/Recebidos - CTRCs"
        fila = OP156Queue(self.client)

        # Estabelece CWB/BIG na sessão antes de abrir a OP455.
        resposta_abertura = self._abrir_op455_oc73_cwb(unidade=unidade)
        contexto = self.validar_contexto_abertura_ocorrencia_73(
            resposta_abertura, unidade,
        )
        if contexto["status"] != "confirmado":
            raise ValueError(
                "Contexto da OP455 não confirmado "
                f"(contexto_op455_nao_confirmado:{contexto['motivo']}); "
                "OP156 e submit bloqueados."
            )
        self._contexto_oc73_confirmado_unidade = unidade

        # Captura IDs anteriores para não baixar
        # um relatório antigo por engano.
        ids_existentes = self.capturar_ids_fila(
            fila=fila,
            opcao=opcao,
            unidade=unidade,
            estrito=True,
        )

        # Gerador exclusivo da OC73.
        # A navegação CWB já foi confirmada anteriormente.
        html = self.gerar_relatorio_ocorrencia_73(
            data_inicial=data_inicial,
            data_final=data_final,
            unidade=unidade,
        )

        resumo = self.resumir_resposta_diagnostica(
            SimpleNamespace(
                text=html,
                content=html.encode("utf-8"),
                status_code=200,
                headers={"Content-Type": "text/html"},
                url="https://sistema.ssw.inf.br/bin/ssw0230",
            )
        )

        if resumo["frase_informe_unidade"]["texto_visivel"]:
            raise ValueError(
                f"SSW solicitou informar a unidade {unidade}."
            )

        if fila.extrair_arquivo_direto(html):
            raise ValueError("Resposta direta da OP455 sem sequência OP156 confirmada.")
        arquivo = fila.baixar_por_opcao(
            output_dir=output_dir,
            opcao=opcao,
            unidade=unidade,
            timeout_seconds=timeout_seconds,
            intervalo=5,
            ignorar_ids=ids_existentes,
            estrito=True,
        )

        self.validar_layout_ocorrencia_73(arquivo)

        return arquivo

    def gerar_relatorio_ocorrencia_73(
        self,
        data_inicial: str,
        data_final: str,
        unidade: str = "CWB",
    ) -> str:
        """Solicita OP455 com filtros exclusivos da ocorrência 73."""

        unidade = unidade.strip().upper()

        if unidade != "CWB":
            raise ValueError(
                "Geração OC73 disponível somente para CWB nesta etapa."
            )

        if self._contexto_oc73_confirmado_unidade != unidade:
            raise ValueError(
                "Contexto CWB não confirmado antes da geração OC73."
            )

        # Autorização de uso único: uma confirmação permite
        # somente uma tentativa de geração.
        # Consumir antes do POST também protege contra exceções.
        self._contexto_oc73_confirmado_unidade = None

        payload = {
            "act": "E1",
            "cod_emp_ctb": "00",

            # Unidade e tipo específicos da OC73
            "f2": unidade,
            "f3": "R",
            "reg_tipo": "R",
            # Período
            "f9": data_inicial,
            "f10": data_final,

            # Filtros
            "f8": "T",
            "f18": "T",
            "f19": "T",
            "f20": "S",
            "f21": "T",
            "f22": "T",
            "f23": "A",
            "f25": "T",
            "f26": "A",
            "f27": "A",
            "f28": "T",
            "ibscbs": "A",
            "f29": "A",
            "f30": "A",

            # Exportação
            "f35": "E",

            # Dados complementares OC73
            "f37": "B",
            "f38": "",
            "f39": "",

            "basico": "N",
            "dummy": dummy(),
        }

        response = self.client.post(
            "/bin/ssw0230",
            data=payload,
            retries=1,
        )

        return response.text

    def capturar_ids_fila(
        self,
        fila: OP156Queue,
        opcao: str,
        unidade: str | None = None,
        *,
        estrito: bool = False,
    ) -> set[str]:
        """
        Captura os IDs existentes na OP156 antes de solicitar
        um novo relatório.

        Assim, a automação ignora relatórios anteriores.
        """

        if estrito and self._contexto_oc73_confirmado_unidade != unidade:
            raise ValueError("Contexto OP455 não confirmado para snapshot OP156.")
        html = fila.abrir_fila(retries=1) if estrito else fila.abrir_fila()

        jobs = fila.extrair_jobs(
            html=html,
            opcao_contains=opcao,
            unidade=unidade,
            **({"estrito": True, "filtrar_usuario": False} if estrito else {}),
        )

        ids = {
            str(job["download_id"]).strip()
            for job in jobs
            if str(job.get("download_id") or "").strip()
        }

        if estrito:
            print(f"[OP455] Sequências anteriores na OP156: {len(ids)}")
        else:
            print(f"[OP455] IDs existentes antes da solicitação: {sorted(ids)}")

        return ids

    def validar_layout_ocorrencia_73(
        self,
        arquivo: Path,
    ) -> None:
        """
        Garante que o arquivo baixado possui o layout completo
        necessário para o BOT da ocorrência 73.
        """

        obrigatorias = {
            "SERIE/NUMERO CTRC",
            "CLIENTE PAGADOR",
            "UNIDADE EMISSORA",
        }

        cidades_aceitas = {
            "CIDADE DO DESTINATARIO",
            "CIDADE DE ENTREGA",
        }

        conteudo = arquivo.read_bytes()

        texto = None

        for encoding in (
            "utf-8-sig",
            "cp1252",
            "latin1",
        ):
            try:
                texto = conteudo.decode(encoding)
                break
            except UnicodeDecodeError:
                continue

        if texto is None:
            raise ValueError(
                "Não foi possível identificar a codificação "
                f"do relatório: {arquivo.name}"
            )

        linhas = list(
            csv.reader(
                texto.splitlines(),
                delimiter=";",
            )
        )

        if len(linhas) < 2:
            raise ValueError(
                "O relatório OP455 não possui cabeçalho válido: "
                f"{arquivo.name}"
            )

        cabecalho = {
            str(coluna or "")
            .replace("\xa0", " ")
            .strip()
            .upper()
            for coluna in linhas[1]
        }

        faltando = obrigatorias - cabecalho

        possui_cidade = bool(
            cidades_aceitas & cabecalho
        )

        if faltando or not possui_cidade:
            detalhes = []

            if faltando:
                detalhes.append(
                    "faltando: "
                    + ", ".join(sorted(faltando))
                )

            if not possui_cidade:
                detalhes.append(
                    "faltando coluna de cidade do destinatário"
                )

            raise ValueError(
                "O arquivo baixado pela OP156 não corresponde "
                "ao layout completo da ocorrência 73. "
                + "; ".join(detalhes)
                + f". Arquivo: {arquivo.name}. "
                + f"Total de colunas: {len(cabecalho)}."
            )

        print(
            "[OP455] Layout validado: "
            f"{arquivo.name} | "
            f"{len(cabecalho)} colunas"
        )
