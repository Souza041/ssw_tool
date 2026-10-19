
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


_FRASE_UNIDADE = "informe a unidade"
_CAMPOS_COM_VALOR_DIAGNOSTICO = {"act", "f3", "reg_tipo"}


def _normalizar_texto_diagnostico(valor: str | None) -> str:
    return " ".join((valor or "").casefold().split())


class _OP455HTMLDiagnosticoParser(HTMLParser):
    """Extrai somente metadados seguros; nunca conserva o HTML recebido."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
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
        atributos = {nome.casefold(): valor for nome, valor in attrs}

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
            if nome.casefold() in _CAMPOS_COM_VALOR_DIAGNOSTICO:
                campo["valor"] = atributos.get("value") or ""
            self.campos.append(campo)
            if tag == "select":
                self._campo_select = campo

        if tag == "option" and self._campo_select is not None:
            if "selected" in atributos:
                opcao = {"selecionada": True}
                if self._campo_select["nome"].casefold() in _CAMPOS_COM_VALOR_DIAGNOSTICO:
                    opcao["valor"] = atributos.get("value") or ""
                self._campo_select["opcoes_selecionadas"].append(opcao)
                self._opcao_selecionada = opcao

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"script", "style"}:
            self._contexto = None
        elif tag == "option":
            self._opcao_selecionada = None
        elif tag == "select":
            self._campo_select = None

    def handle_data(self, data: str) -> None:
        if self._contem_frase(data):
            self.localizacoes[self._contexto or "texto_visivel"] = True

        if (
            self._opcao_selecionada is not None
            and self._campo_select is not None
            and self._campo_select["nome"].casefold()
            in _CAMPOS_COM_VALOR_DIAGNOSTICO
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

    def open(self, unidade: str = "MTZ"):
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
    ) -> str:
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

        response = self.client.post(
            "/bin/ssw0230",
            payload,
        )

        return response.text

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

    def _payload_relatorio_ocorrencia_73(
        self,
        data_inicial: str,
        data_final: str,
    ) -> dict:
        return {
            "act": "E1",
            "cod_emp_ctb": "00",

            # Tipo confirmado pelo HAR e pelo fluxo OP455 anterior.
            # CWB/BIG é estabelecida na sessão antes da abertura da OP455.
            "f3": "E",

            # Todos os documentos
            "f8": "T",

            # Período de emissão
            "f9": data_inicial,
            "f10": data_final,

            # Demais configurações do OP455
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

            # Excel e dados complementares
            "f35": "E",
            "f37": "B",

            "basico": "N",
            "dummy": dummy(),
        }

    def _solicitar_relatorio_ocorrencia_73(
        self,
        data_inicial: str,
        data_final: str,
        retries: int | None = None,
    ):
        argumentos = {
            "data": self._payload_relatorio_ocorrencia_73(
                data_inicial=data_inicial,
                data_final=data_final,
            ),
        }
        if retries is not None:
            argumentos["retries"] = retries
        return self.client.post("/bin/ssw0230", **argumentos)

    def gerar_relatorio_ocorrencia_73(
        self,
        data_inicial: str,
        data_final: str,
    ) -> str:
        """
        Gera o relatório OP455 para os fluxos CWB e BIG.

        Os filtros de UF do remetente e última ocorrência
        serão aplicados posteriormente no parser.
        """
        response = self._solicitar_relatorio_ocorrencia_73(
            data_inicial=data_inicial,
            data_final=data_final,
        )

        return response.text

    def diagnosticar_geracao_ocorrencia_73(
        self,
        data_inicial: str,
        data_final: str,
        unidade: str = "CWB",
    ) -> dict:
        """Faz um submit consultivo, sem polling, download ou persistência."""
        unidade = unidade.strip().upper()
        if unidade not in {"CWB", "BIG"}:
            raise ValueError(f"Unidade não permitida para OC73: {unidade}")

        inicio = datetime.strptime(data_inicial, "%d%m%y").date()
        fim = datetime.strptime(data_final, "%d%m%y").date()
        if inicio > fim:
            raise ValueError(
                "A data inicial não pode ser posterior à data final."
            )

        opcao = "455 - Fretes Expedidos/Recebidos - CTRCs"
        fila = OP156Queue(self.client)
        resultado = {
            "unidade": unidade,
            "unidade_contexto_op455": unidade,
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
            self.open(unidade=unidade)
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
            response = self._solicitar_relatorio_ocorrencia_73(
                data_inicial=data_inicial,
                data_final=data_final,
                retries=1,
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
        self.open(unidade=unidade)

        # Captura IDs anteriores para não baixar
        # um relatório antigo por engano.
        ids_existentes = self.capturar_ids_fila(
            fila=fila,
            opcao=opcao,
            unidade=unidade,
        )

        html = self.gerar_relatorio_ocorrencia_73(
            data_inicial=data_inicial,
            data_final=data_final,
        )

        if "Informe a unidade" in html:
            raise ValueError(
                f"SSW solicitou informar a unidade {unidade}."
            )

        info_direto = self.extrair_arquivo_direto(html)

        if info_direto:
            arquivo = self.baixar_arquivo_direto(
                info=info_direto,
                output_dir=output_dir,
            )
        else:
            arquivo = fila.baixar_por_opcao(
                output_dir=output_dir,
                opcao=opcao,
                unidade=unidade,
                timeout_seconds=timeout_seconds,
                intervalo=5,
                ignorar_ids=ids_existentes,
            )

        self.validar_layout_ocorrencia_73(arquivo)

        return arquivo

    def capturar_ids_fila(
        self,
        fila: OP156Queue,
        opcao: str,
        unidade: str | None = None,
    ) -> set[str]:
        """
        Captura os IDs existentes na OP156 antes de solicitar
        um novo relatório.

        Assim, a automação ignora relatórios anteriores.
        """

        html = fila.abrir_fila()

        jobs = fila.extrair_jobs(
            html=html,
            opcao_contains=opcao,
            unidade=unidade,
        )

        ids = {
            str(job["download_id"]).strip()
            for job in jobs
            if str(job.get("download_id") or "").strip()
        }

        print(
            "[OP455] IDs existentes antes da solicitação: "
            f"{sorted(ids)}"
        )

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
