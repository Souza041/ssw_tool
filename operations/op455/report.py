
import csv
import re 

from urllib.parse import unquote

from datetime import datetime, timedelta
from pathlib import Path

from operations.op156.queue import OP156Queue
from ssw.client import SSWClient
from ssw.settings import settings
from ssw.utils import data_ddmmaa, dummy


class OP455Report:
    def __init__(self, client: SSWClient) -> None:
        self.client = client

    def open(self, unidade: str = "MTZ") -> None:
        self.client.open_option("455", unidade)

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
            "f37": "B",
            "f38": "F",
            "basico": "N",
            "dummy": dummy(),
        }

        response = self.client.post("/bin/ssw0230", payload)
        return response.text

    def gerar_relatorio_ocorrencia_73(
        self,
        data_inicial: str,
        data_final: str,
    ) -> str:
        """
        Gera a OP455 exatamente na configuração usada pelo BOT
        de lançamento da ocorrência 73.

        As datas devem estar no formato DDMMAA.
        """

        payload = {
            "act": "E1",
            "cod_emp_ctb": "00",

            # E = unidade expedidora
            "f3": "E",

            # Todos os documentos
            "f8": "T",

            # Período de emissão
            "f9": data_inicial,
            "f10": data_final,

            # Configuração confirmada pelo HAR
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
            "f35": "E",
            "f37": "N",
            "basico": "N",
            "dummy": dummy(),
        }

        response = self.client.post(
            "/bin/ssw0230",
            data=payload,
        )

        return response.text

    def gerar_e_baixar_ocorrencia_73(
        self,
        output_dir: Path,
        data_referencia: str,
        timeout_seconds: int = 300,
    ) -> Path:
        """
        Abre a OP455 em MTZ, captura os relatórios já existentes
        na OP156, gera uma nova solicitação e baixa somente um
        novo ID.

        O arquivo também é validado antes de ser devolvido.
        """

        opcao = (
            "455 - Fretes Expedidos/Recebidos - CTRCs"
        )

        unidade = "MTZ"

        fila = OP156Queue(self.client)

        # Abre obrigatoriamente em MTZ.
        self.open(
            unidade=unidade
        )

        # Captura todos os IDs antigos antes de gerar.
        ids_existentes = self.capturar_ids_fila(
            fila=fila,
            opcao=opcao,
            unidade=unidade,
        )

        html = self.gerar_relatorio_ocorrencia_73(
            data_inicial=data_referencia,
            data_final=data_referencia,
        )

        if "Informe a unidade" in html:
            raise ValueError(
                "SSW retornou 'Informe a unidade' ao gerar "
                "a OP455 em MTZ."
            )

        info_direto = self.extrair_arquivo_direto(
            html
        )

        if info_direto:
            arquivo = self.baixar_arquivo_direto(
                info=info_direto,
                output_dir=output_dir,
            )

            self.validar_layout_ocorrencia_73(
                arquivo
            )

            return arquivo

        arquivo = fila.baixar_por_opcao(
            output_dir=output_dir,
            opcao=opcao,
            unidade=unidade,
            timeout_seconds=timeout_seconds,
            intervalo=5,
            ignorar_ids=ids_existentes,
        )

        self.validar_layout_ocorrencia_73(
            arquivo
        )

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
    
        