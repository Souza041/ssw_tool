import re
from datetime import datetime
from html import unescape

from ssw.client import SSWClient
from ssw.utils import dummy

from .common import decodificar_html, extrair_inputs, somente_digitos, texto_limpo


class OP506Indenizacao:
    def __init__(self, client: SSWClient) -> None:
        self.client = client

    def open(self, unidade: str | None = None) -> None:
        unidade = unidade or self.client.unidade
        self.client.post(
            "/bin/menu01",
            {"act": "TRO", "f2": unidade, "f3": "506", "dummy": dummy()},
        )
        self.client.post(
            "/bin/ssw0496",
            {"sequencia": "506", "dummy": dummy()},
        )

    @staticmethod
    def parse_ctrc(ctrc: str) -> tuple[str, str]:
        texto = (
            str(ctrc or "")
            .strip()
            .upper()
            .replace(" ", "")
        )

        #
        # A série do CTRC possui 3 caracteres,
        # podendo conter letras e números.
        #
        # Exemplos:
        #   CWB193958-1 -> série CWB
        #   SC2750790-9 -> série SC2
        #
        match = re.fullmatch(
            r"([A-Z][A-Z0-9]{2})(\d+)(?:-(\d+))?",
            texto,
        )

        if not match:
            raise ValueError(
                f"CTRC inválido: {ctrc}. "
                "Exemplos esperados: "
                "CWB193958-1 ou SC2750790-9"
            )

        serie = match.group(1)

        numero = (
            match.group(2)
            + (match.group(3) or "")
        )

        return serie, numero

    @staticmethod
    def _opcoes_ctrc(html_texto: str) -> list[dict]:
        decoded = decodificar_html(html_texto)
        opcoes = []
        pattern = re.compile(
            r'<btn\s+lb=["\']([^"\']*?Inclus(?:ão|&atilde;o):\s*(\d{2}/\d{2}/\d{4}))[^>]*?\bvl=["\']?(\d+)',
            flags=re.I,
        )
        for match in pattern.finditer(decoded):
            data = datetime.strptime(match.group(2), "%d/%m/%Y")
            opcoes.append({
                "label": unescape(match.group(1)),
                "data": data,
                "seq_ctrc": match.group(3),
            })
        return opcoes

    def localizar_ctrc(self, ctrc: str) -> dict:
        serie, numero_busca = self.parse_ctrc(ctrc)
        response = self.client.post(
            "/bin/ssw0496",
            {
                "act": "ENV",
                "ser_ctrc": serie.lower(),
                "nro_ctrc": numero_busca,
                "dummy": dummy(),
            },
        )

        opcoes = self._opcoes_ctrc(response.text)
        if opcoes:
            escolhida = max(opcoes, key=lambda item: item["data"])
            contexto = extrair_inputs(response.text)
            contexto.update({
                "act": "ENV",
                "btn_ctrc": escolhida["seq_ctrc"],
                "ser_ctrc": serie.lower(),
                "nro_ctrc": numero_busca,
                "dummy": dummy(),
            })
            response = self.client.post("/bin/ssw0496", contexto)

        html_final = response.text
        inputs = extrair_inputs(html_final)

        seq_ctrc = inputs.get("seq_ctrc", "")
        if not seq_ctrc:
            # Algumas respostas mantêm o seq apenas em scripts/links.
            match = re.search(r"\bseq_ctrc\s*[=:]\s*[\"']?(\d+)", decodificar_html(html_final), flags=re.I)
            seq_ctrc = match.group(1) if match else ""

        if not seq_ctrc:
            mensagem = texto_limpo(html_final)[:600]
            raise RuntimeError(f"Não foi possível abrir o CTRC {ctrc} na OP506. {mensagem}")

        return {
            "html": html_final,
            "inputs": inputs,
            "seq_ctrc": seq_ctrc,
            "serie": inputs.get("ser_ctrc", serie).upper(),
            "nro_ctrc": inputs.get("nro_ctrc", ""),
            "cod_fil_emit": inputs.get("cod_fil_emit", ""),
            "seq_cliente_emit": inputs.get("seq_cliente_emit", ""),
        }

    def consultar_lancamento(self, lancamento: str) -> dict:
        response = self.client.get(
            "/bin/ssw0496",
            params={"get_desp": str(lancamento), "dummy": dummy()},
        )
        try:
            dados = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"SSW não retornou JSON ao consultar lançamento {lancamento}: "
                f"{texto_limpo(response.text)[:300]}"
            ) from exc

        if not dados.get("success"):
            raise RuntimeError(f"Lançamento {lancamento} não encontrado na OP506.")
        return dados

    def consultar_unidade(self, unidade: str) -> str:
        response = self.client.get(
            "/bin/ssw0496",
            params={"get_fil": unidade.lower(), "dummy": dummy()},
        )
        match = re.search(r"<_0>(.*?)</_0>", response.text, flags=re.I | re.S)
        if not match:
            raise RuntimeError(f"Unidade {unidade} não reconhecida pela OP506.")
        return texto_limpo(match.group(1))

    def _tratar_arquivo_morto(
        self,
        response,
        payload: dict,
    ):
        decoded = decodificar_html(
            response.text
        )

        mensagem = texto_limpo(
            decoded
        )

        normalizada = mensagem.upper()

        arquivo_morto = (
            "ARQUIVO MORTO" in normalizada
            and
            "RESTAUR" in normalizada
        )

        if not arquivo_morto:
            return response

        #
        # O SSW retorna:
        #
        # <btn lb="Sim" vl="S">
        # <ret nm="btn_1654" vl="PAG">
        #
        # portanto a confirmação equivale a:
        #
        # btn_1654=S
        #
        inputs = extrair_inputs(
            response.text
        )

        payload_confirmacao = dict(
            inputs
        )

        #
        # Preserva os dados originais porque alguns campos
        # podem não voltar completos como inputs no card.
        #
        payload_confirmacao.update(
            payload
        )

        payload_confirmacao["act"] = "PAG"
        payload_confirmacao["btn_1654"] = "S"
        payload_confirmacao["dummy"] = dummy()

        return self.client.post(
            "/bin/ssw0496",
            payload_confirmacao,
            retries=1,
        )

    def indenizar(
        self,
        *,
        ctrc: str,
        lancamento: str,
        descricao_mercadoria: str,
        motivo: str,
        valor: str,
        unidade_responsavel: str,
    ) -> dict:
        ctrc_info = self.localizar_ctrc(ctrc)
        despesa = self.consultar_lancamento(lancamento)
        unidade_resp = unidade_responsavel.strip().upper()
        nome_unidade_resp = self.consultar_unidade(unidade_resp)

        data_pag = str(despesa.get("data_pag") or "").strip()
        if not data_pag:
            raise RuntimeError(f"Lançamento {lancamento} não retornou data de pagamento.")

        payload = {
            "act": "PAG",
            "info_comp": str(descricao_mercadoria or "").strip(),
            "t_motivo_indenizacao": str(motivo or "").strip(),
            "nro_lancto_desp": str(lancamento),
            "fil_pgto": str(despesa.get("fil_sigla") or "").strip().upper(),
            "unidade_pgto": str(despesa.get("fil_nome") or "").strip(),
            "cod_evento": str(despesa.get("evt_cod") or "5501").strip(),
            "descr_evento": str(despesa.get("evt_desc") or "INDENIZACAO DE MERCADORIAS").strip(),
            "cgc_benef": somente_digitos(despesa.get("forn_cgc")),
            "nome_benef": str(despesa.get("forn_nome") or "").strip(),
            "vlr_indeniz": valor,
            "data_indeniz": data_pag,
            "vlr_for1": valor,
            "par_for1": "1",
            "data_for1": data_pag,
            "fil_resp": unidade_resp.lower(),
            "unidade_resp": nome_unidade_resp,
            "newPage": "S",
            "seq_ctrc": ctrc_info["seq_ctrc"],
            "cod_fil_emit": ctrc_info["cod_fil_emit"],
            "nro_ctrc": ctrc_info["nro_ctrc"],
            "ser_ctrc": ctrc_info["serie"],
            "seq_cliente_emit": ctrc_info["seq_cliente_emit"],
            "historico": "",
            "nro_lancto": "0",
            "dummy": dummy(),
        }

        response = self.client.post(
            "/bin/ssw0496",
            payload,
            retries=1,
        )

        #
        # Alguns CTRCs estão arquivados no Arquivo Morto.
        # Nesse caso o SSW pede confirmação para restaurá-los
        # antes de registrar a indenização.
        #
        response = self._tratar_arquivo_morto(
            response,
            payload,
        )

        mensagem = texto_limpo(
            response.text
        )
        normalizada = mensagem.upper()

        confirmou = (
            "INDENIZAÇÕES REGISTRADAS" in normalizada
            or "INDENIZACOES REGISTRADAS" in normalizada
        ) and str(lancamento) in mensagem

        if not confirmou:
            raise RuntimeError(
                f"Não foi possível confirmar a indenização "
                f"do CTRC {ctrc}. "
                f"Resposta SSW: {mensagem[:700]}"
            )

        return {
            "sucesso": True,
            "ctrc": ctrc,
            "seq_ctrc": ctrc_info["seq_ctrc"],
            "lancamento": str(lancamento),
            "mensagem": f"CTRC {ctrc} indenizado usando o lançamento {lancamento}.",
        }
