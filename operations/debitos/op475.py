import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal

from ssw.client import SSWClient
from ssw.utils import dummy

from .common import decodificar_html, extrair_inputs, texto_limpo


class OP475Despesas:
    EVENTO_PADRAO = "5501"
    EVENTO_DESCRICAO_PADRAO = "INDENIZACAO DE MERCADORIAS"

    MAX_TENTATIVAS_SERIE = 20

    MODELO_DOCUMENTO = "97"
    DESCR_MODELO = "Recibo"

    CFOP_ENTRADA = "1949"
    CFOP_DESCRICAO = (
        "OUTRA ENTRADA DE MERC OU PRESTACAO DE SERV NAO ESPECIFICADA"
    )

    def __init__(
        self,
        client: SSWClient,
        nome_unidade: str = "RODOBRAS TRANSP RODOVIARIOS",
    ) -> None:
        self.client = client
        self.nome_unidade = nome_unidade

    # =========================================================
    # HELPERS
    # =========================================================

    @staticmethod
    def _normalizar_texto(texto: str) -> str:
        valor = str(texto or "").lower()

        valor = unicodedata.normalize(
            "NFKD",
            valor,
        )

        return "".join(
            c
            for c in valor
            if not unicodedata.combining(c)
        )

    @staticmethod
    def _valor_tela(valor: str) -> str:
        """
        Converte:
            1164,43
        para:
            1.164,43

        Igual ao valor que o navegador enviou no Playwright.
        """

        texto = str(valor or "").strip()

        texto = (
            texto
            .replace("R$", "")
            .replace(" ", "")
        )

        if "," in texto:
            texto_decimal = (
                texto
                .replace(".", "")
                .replace(",", ".")
            )
        else:
            texto_decimal = texto

        numero = Decimal(texto_decimal)

        formatado = f"{numero:,.2f}"

        return (
            formatado
            .replace(",", "@")
            .replace(".", ",")
            .replace("@", ".")
        )

    # =========================================================
    # ABERTURA
    # =========================================================

    def open(
        self,
        unidade: str | None = None,
    ) -> None:
        unidade = unidade or self.client.unidade

        # 1. Troca para a opção 475
        self.client.post(
            "/bin/menu01",
            {
                "act": "TRO",
                "f2": unidade,
                "f3": "475",
                "dummy": dummy(),
            },
        )

        # 2. Solicita abertura da operação
        self.client.post(
            "/bin/ssw0094",
            {
                "sequencia": "475",
                "dummy": dummy(),
            },
        )

        # 3. IMPORTANTE:
        # reproduz o GET que o navegador realiza ao carregar
        # efetivamente a página da OP475.
        response = self.client.get(
            "/bin/ssw0094",
        )

        if response.status_code != 200:
            raise RuntimeError(
                "Não foi possível carregar a tela inicial "
                f"da OP475. HTTP={response.status_code}"
            )

        pagina = texto_limpo(response.text)

        if "Programação de Despesas" not in pagina and \
        "Programacao de Despesas" not in pagina:
            raise RuntimeError(
                "A OP475 não foi carregada corretamente. "
                f"Resposta: {pagina[:400]}"
            )

        self.client.session.headers["Referer"] = (
            f"{self.client.base_url}/bin/ssw0094"
        )

    # =========================================================
    # FILIAL
    # =========================================================

    def consultar_filial(
        self,
        unidade: str,
    ) -> str:
        unidade = (
            str(unidade or "")
            .strip()
            .lower()
        )

        response = self.client.get(
            "/bin/ssw0094",
            params={
                "get_filial": unidade,
                "dummy": dummy(),
            },
        )

        try:
            root = ET.fromstring(
                response.text
            )

            nome = (
                root.findtext("_0")
                or ""
            ).strip()

        except ET.ParseError as exc:
            raise RuntimeError(
                "Resposta inválida ao consultar "
                f"unidade {unidade}: "
                f"{texto_limpo(response.text)[:300]}"
            ) from exc

        if not nome:
            raise RuntimeError(
                "SSW não retornou o nome da unidade "
                f"{unidade.upper()}."
            )

        return nome

    # =========================================================
    # EVENTO
    # =========================================================

    def consultar_evento(
        self,
        cnpj: str,
    ) -> tuple[str, str]:

        response = self.client.get(
            "/bin/ssw0094",
            params={
                "get_evento_chave": "S",
                "chave_evento": cnpj,
                "dummy": dummy(),
            },
        )

        codigo = ""

        try:
            root = ET.fromstring(
                response.text
            )

            codigo = (
                root.findtext("_0")
                or ""
            ).strip()

        except ET.ParseError:
            pass

        codigo = (
            codigo
            or self.EVENTO_PADRAO
        )

        response_desc = self.client.get(
            "/bin/ssw0094",
            params={
                "get_evento": codigo,
                "dummy": dummy(),
            },
        )

        descricao = ""

        try:
            root = ET.fromstring(
                response_desc.text
            )

            descricao = (
                root.findtext("_0")
                or ""
            ).strip()

        except ET.ParseError:
            pass

        if (
            not descricao
            and codigo == self.EVENTO_PADRAO
        ):
            descricao = (
                self.EVENTO_DESCRICAO_PADRAO
            )

        return codigo, descricao

    def _carregar_dados_fornecedor(
        self,
        cnpj: str,
        contexto: dict,
    ) -> None:
        response = self.client.get(
            "/bin/ssw0094",
            params={
                "codigo": contexto["codigo"],
                "forn_ajax": cnpj,
                "filial_sigla": contexto["filial_sigla"],
                "chave_nfe_display": contexto.get(
                    "chave_nfe_display",
                    "",
                ),
                "modelo_doc_fiscal": self.MODELO_DOCUMENTO,
                "p_act": "undefined",
                "dummy": dummy(),
            },
        )

        if response.status_code != 200:
            raise RuntimeError(
                "Falha ao carregar dados do fornecedor "
                "na inicialização da OP475."
            )

    # =========================================================
    # ACT=INC
    # =========================================================

    def _iniciar_inclusao(
        self,
        cnpj: str,
        evento: str,
        descricao_evento: str,
        unidade_lancamento: str,
        nome_unidade_lancamento: str,
    ) -> dict:

        hoje = datetime.now().strftime(
            "%d%m%y"
        )

        unidade_base = (
            self.client.unidade
            .strip()
            .upper()
        )

        response = self.client.post(
            "/bin/ssw0094",
            {
                "act": "INC",
                "f1": "S",

                # Unidade escolhida na tela
                "f3": (
                    unidade_lancamento
                    .lower()
                ),
                "unidade3":
                    nome_unidade_lancamento,

                "chave_nfe": cnpj,

                "f5": evento,
                "evento":
                    descricao_evento,

                # Campos que permanecem
                # na unidade base da sessão
                "f11": unidade_base,
                "unidade11":
                    self.nome_unidade,

                "f16": unidade_base,
                "unidade16":
                    self.nome_unidade,

                "f17": hoje,
                "f18": hoje,

                "f21": "T",
                "f22": "V",

                "sigla_unidade26":
                    unidade_base,
                "unidade26":
                    self.nome_unidade,

                "dt_pg_inic27": hoje,
                "dt_pg_fim28": hoje,

                "estorna": "",
                "altera_data_pgto": "",
                "orig_dest": "",

                "dummy": dummy(),
            },
        )

        contexto = extrair_inputs(
            response.text
        )

        obrigatorios = (
            "filial_sigla",
            "cod_fil_pgto",
            "agora",
            "flag_morto",
            "codigo",
        )

        faltando = [
            campo
            for campo in obrigatorios
            if not str(
                contexto.get(campo) or ""
            ).strip()
        ]

        if faltando:
            raise RuntimeError(
                "OP475 não retornou contexto "
                "obrigatório: "
                + ", ".join(faltando)
            )

        filial = str(
            contexto["filial_sigla"]
        ).strip().upper()

        if filial != (
            unidade_lancamento
            .strip()
            .upper()
        ):
            raise RuntimeError(
                "SSW abriu OP475 em unidade "
                "divergente | "
                f"esperada={unidade_lancamento} | "
                f"retornada={filial}"
            )

        self._carregar_dados_fornecedor(
            cnpj,
            contexto,
        )

        self._criar_temp(contexto)

        return contexto

    def _criar_temp(
        self,
        contexto: dict,
    ) -> None:
        """
        Reproduz criaTemp() executado pelo JavaScript da OP475.

        Essa chamada cria estado temporário server-side necessário
        antes de consiste_nota / consiste_parcela.
        Sem ela, o SSW retorna "Dados da sessão inválidos".
        """

        params = [
            ("act", "CRIA_TEMP"),
            ("nro_lancto", contexto.get("nro_lancto", "")),
            ("agora", contexto["agora"]),
            ("filial_sigla", contexto["filial_sigla"]),

            # O próprio JavaScript manda flag_morto duas vezes.
            # Vamos reproduzir literalmente.
            ("flag_morto", contexto["flag_morto"]),
            ("flag_morto", contexto["flag_morto"]),

            ("altera_nfse", contexto.get("altera_nfse", "")),
            ("ser_nfse", contexto.get("ser_nfse", "")),
            ("nro_nfse", contexto.get("nro_nfse", "")),
            ("chave_nfse", contexto.get("chave_nfse", "")),
            (
                "data_emissao_nfse",
                contexto.get("data_emissao_nfse", ""),
            ),
            (
                "seq_desp_nota_nfse",
                contexto.get("seq_desp_nota_nfse", ""),
            ),

            ("dummy", dummy()),
        ]

        response = self.client.get(
            "/bin/ssw0094",
            params=params,
        )

        if response.status_code != 200:
            raise RuntimeError(
                "Falha ao criar contexto temporário da OP475 | "
                f"HTTP={response.status_code}"
            )

    # =========================================================
    # CONSISTE NOTA
    # =========================================================

    def _consistir_nota(
        self,
        *,
        cnpj: str,
        serie: int,
        nf: str,
        valor_tela: str,
        data_emissao: str,
        contexto: dict,
    ) -> tuple[str, str]:

        payload = {
            "consiste_nota": "S",

            "f4": str(serie),
            "f5": nf,

            "f7":
                self.MODELO_DOCUMENTO,

            "f12": "",

            "f14":
                self.CFOP_ENTRADA,

            # exatamente como browser
            "f15": valor_tela,

            "f16": data_emissao,
            "f17": data_emissao,

            "f18": "",
            "f19": "",
            "f20": "",
            "f21": "",
            "f22": "",
            "f23": "",
            "f24": "",
            "f25": "",
            "f26": "",

            "f27": "undefined",
            "f28": "undefined",
            "f29": "undefined",

            "cod_cfop_saida": "",

            "cfop_entrada":
                self.CFOP_ENTRADA,

            "cgc_forn": cnpj,

            "itens": "N",

            "chave_nfe_display": "",
            "produtos": "",

            "codigo":
                contexto["codigo"],

            "cod_fil_pgto":
                contexto["cod_fil_pgto"],

            #
            # Playwright confirmou que
            # AQUI entra vazio.
            #
            "seq_desp_nota": "",

            "nro_lancto": "",

            "agora":
                contexto["agora"],

            "filial_sigla":
                contexto["filial_sigla"],

            "base_calc_inss": "",

            "flag_morto":
                contexto["flag_morto"],

            "orig_dest": "",

            "p_act": "undefined",

            "dummy": dummy(),
        }

        response = self.client.post(
            "/bin/ssw0094",
            payload,
            retries=1,
        )

        try:
            root = ET.fromstring(
                response.text
            )

        except ET.ParseError as exc:
            raise RuntimeError(
                "Resposta inválida ao "
                "consistir nota: "
                f"{texto_limpo(response.text)[:500]}"
            ) from exc

        status = (
            root.findtext("_0")
            or ""
        ).strip()

        if status.upper() != "OK":
            raise RuntimeError(
                "SSW recusou consistência "
                f"da NF {nf}/série {serie}: "
                f"{status}"
            )

        resumo = (
            root.findtext("_2")
            or ""
        ).strip()

        seq = (
            root.findtext("_3")
            or ""
        ).strip()

        if not seq:
            raise RuntimeError(
                "SSW não retornou "
                "seq_desp_nota."
            )

        return seq, resumo

    # =========================================================
    # CONSISTE PARCELA
    # =========================================================

    def _consistir_parcela(
        self,
        *,
        cnpj: str,
        vencimento: str,
        competencia: str,
        valor_tela: str,
        historico: str,
        contexto: dict,
    ) -> tuple[str, str]:

        response = self.client.get(
            "/bin/ssw0094",
            params={
                "consiste_parcela": "S",

                "f2": cnpj,

                "nro_parcela": "01",

                "data_vcto":
                    vencimento,

                "data_pgto":
                    vencimento,

                #
                # No navegador apareceu:
                # "0826 "
                #
                "mes_competencia":
                    competencia.rstrip()
                    + " ",

                "vlr_parcela":
                    valor_tela,

                "juros_parcela":
                    "0,00",

                #
                # Browser mandou vazio.
                #
                "desconto_parcela":
                    "",

                "historico":
                    historico,

                "cod_barras": "",
                "cgc_beneficiario": "",
                "link_pix": "",
                "fornecedor43": "",

                "seq_desp_parcela": "",
                "nro_lancto": "",

                "agora":
                    contexto["agora"],

                "filial_sigla":
                    contexto["filial_sigla"],

                "flag_morto":
                    contexto["flag_morto"],

                "estorna": "",
                "altera_data_pgto": "",

                "chamador": "INC2",

                "dummy": dummy(),
            },
        )

        try:
            root = ET.fromstring(
                response.text
            )

        except ET.ParseError as exc:
            raise RuntimeError(
                "Resposta inválida ao "
                "consistir parcela: "
                f"{texto_limpo(response.text)[:500]}"
            ) from exc

        status = (
            root.findtext("_0")
            or ""
        ).strip()

        if status.upper() != "OK":
            raise RuntimeError(
                "SSW recusou parcela: "
                f"{status}"
            )

        resumo = (
            root.findtext("_2")
            or ""
        ).strip()

        seq = (
            root.findtext("_3")
            or ""
        ).strip()

        if not seq:
            raise RuntimeError(
                "SSW não retornou "
                "seq_desp_parcela."
            )

        return seq, resumo

    # =========================================================
    # GRAVAÇÃO INC2
    # =========================================================

    def _gravar(
        self,
        *,
        cnpj: str,
        nf: str,
        serie: int,
        valor_tela: str,
        vencimento: str,
        competencia: str,
        historico: str,
        data_emissao: str,
        contexto: dict,
        seq_nota: str,
        resumo_nota: str,
        seq_parcela: str,
        resumo_parcela: str,
    ) -> dict:

        payload = {
            "act": "INC2",

            "filial_sigla":
                contexto["filial_sigla"],

            "codigo":
                contexto["codigo"],

            "f2": cnpj,

            "forn_inscr":
                contexto.get(
                    "forn_inscr",
                    "",
                ),

            "forn_endereco":
                contexto.get(
                    "forn_endereco",
                    "",
                ),

            "forn_cep":
                contexto.get(
                    "forn_cep",
                    "",
                ),

            "forn_cidade":
                contexto.get(
                    "forn_cidade",
                    "",
                ),

            "forn_fone":
                contexto.get(
                    "forn_fone",
                    "",
                ),

            "qtde_notas":
                resumo_nota,

            "seq_desp_nota":
                seq_nota,

            "f4": str(serie),
            "f5": nf,

            "f7":
                self.MODELO_DOCUMENTO,

            "descr_modelo":
                self.DESCR_MODELO,

            "f14":
                self.CFOP_ENTRADA,

            "cfop_entrada":
                self.CFOP_DESCRICAO,

            "f15":
                valor_tela,

            "f16":
                data_emissao,

            "f17":
                data_emissao,

            "qtde_parcelas":
                resumo_parcela,

            "seq_desp_parcela":
                seq_parcela,

            "nro_parcela": "01",

            "data_vcto":
                vencimento,

            "data_pgto":
                vencimento,

            "mes_competencia":
                competencia.rstrip()
                + " ",

            "vlr_parcela":
                valor_tela,

            "juros_parcela":
                "0,00",

            "historico":
                historico,

            "estorna": "",
            "altera_data_pgto": "",
            "orig_dest": "",

            "incluindo": "S",

            "agora":
                contexto["agora"],

            "flag_morto":
                contexto["flag_morto"],

            "qtde_parcelas_hidd":
                "1",

            "qtde_notas_hidd":
                "1",

            "cfop_creditavel":
                contexto.get(
                    "cfop_creditavel",
                    "N",
                ),

            "lista_inat":
                contexto.get(
                    "lista_inat",
                    "N",
                ),

            "chamador": "",
            "seq_ficha_frete": "",
            "embarcador": "",

            "cod_fil_pgto":
                contexto["cod_fil_pgto"],

            "dummy": dummy(),
        }

        response = self.client.post(
            "/bin/ssw0094",
            payload,
            retries=1,
        )

        decoded = decodificar_html(
            response.text
        )

        mensagem = texto_limpo(
            decoded
        )

        normalizada = (
            self._normalizar_texto(
                mensagem
            )
        )

        duplicado = (
            "ja existe um lancamento" in normalizada
            and
            "com mesmo documento" in normalizada
        )

        if duplicado:
            # Primeiro tenta capturar diretamente do HTML.
            match = re.search(
                r">([A-Z]{2,4}\d+)</a>\)\s*com\s+mesmo\s+documento",
                decoded,
                flags=re.I,
            )

            # Fallback pelo texto já limpo.
            if not match:
                match = re.search(
                    r"\(([A-Z]{2,4}\d+)\)",
                    mensagem,
                    flags=re.I,
                )

            existente = (
                match.group(1)
                if match
                else ""
            )

            print(
                "[OP475] Duplicidade detectada | "
                f"NF={nf} | "
                f"série={serie} | "
                f"lançamento existente={existente or '-'}"
            )

            return {
                "status": "DUPLICADO",
                "lancamento_existente": existente,
                "mensagem": mensagem,
            }

        # tenta obter lançamento
        inputs = extrair_inputs(
            decoded
        )

        lancamento = (
            inputs.get("f12")
            or inputs.get("nro_lancto")
            or ""
        )

        if not lancamento:
            matches = re.findall(
                r"(?:"
                r"nro_lancto(?:%3D|=)"
                r"|"
                r"Lan(?:&ccedil;|ç)amento"
                r"\s+[A-Z]{2,4}\s+"
                r")"
                r"(\d+)",
                decoded,
                flags=re.I,
            )

            if matches:
                lancamento = matches[-1]

        if not lancamento:
            raise RuntimeError(
                "Não foi possível confirmar "
                "o número do lançamento OP475. "
                f"Resposta: {mensagem[:700]}"
            )

        return {
            "status": "OK",
            "lancamento": lancamento,
            "mensagem": mensagem,
        }

    # =========================================================
    # FLUXO PRINCIPAL
    # =========================================================

    def lancar(
        self,
        *,
        cnpj: str,
        nf: str,
        serie: int,
        valor: str,
        vencimento: str,
        competencia: str,
        historico: str,
        unidade_lancamento: str | None = None,
    ) -> dict:

        unidade = (
            unidade_lancamento
            or self.client.unidade
        ).strip().upper()

        data_emissao = (
            datetime.now()
            .strftime("%d%m%y")
        )

        valor_tela = (
            self._valor_tela(valor)
        )

        #
        # Ordem exatamente igual ao browser:
        #
        # 1. Unidade
        # 2. CNPJ/evento
        #
        nome_unidade = (
            self.consultar_filial(
                unidade
            )
        )

        evento, descricao_evento = (
            self.consultar_evento(
                cnpj
            )
        )

        serie_atual = max(
            1,
            int(serie),
        )

        for tentativa in range(
            self.MAX_TENTATIVAS_SERIE
        ):
            print(
                "[OP475] "
                f"NF={nf} | "
                f"tentando série "
                f"{serie_atual}"
            )

            #
            # Nova inclusão para cada tentativa.
            #
            contexto = (
                self._iniciar_inclusao(
                    cnpj=cnpj,
                    evento=evento,
                    descricao_evento=
                        descricao_evento,
                    unidade_lancamento=
                        unidade,
                    nome_unidade_lancamento=
                        nome_unidade,
                )
            )

            #
            # Aqui reproduzimos o clique
            # "Gravar lançamento":
            #
            # consiste_nota()
            #
            seq_nota, resumo_nota = (
                self._consistir_nota(
                    cnpj=cnpj,
                    serie=serie_atual,
                    nf=nf,
                    valor_tela=
                        valor_tela,
                    data_emissao=
                        data_emissao,
                    contexto=contexto,
                )
            )

            #
            # consiste_parcela('INC2')
            #
            seq_parcela, resumo_parcela = (
                self._consistir_parcela(
                    cnpj=cnpj,
                    vencimento=
                        vencimento,
                    competencia=
                        competencia,
                    valor_tela=
                        valor_tela,
                    historico=
                        historico,
                    contexto=contexto,
                )
            )

            #
            # verifEnvia('INC2', 0)
            #
            resultado = self._gravar(
                cnpj=cnpj,
                nf=nf,
                serie=serie_atual,
                valor_tela=
                    valor_tela,
                vencimento=
                    vencimento,
                competencia=
                    competencia,
                historico=
                    historico,
                data_emissao=
                    data_emissao,
                contexto=contexto,
                seq_nota=
                    seq_nota,
                resumo_nota=
                    resumo_nota,
                seq_parcela=
                    seq_parcela,
                resumo_parcela=
                    resumo_parcela,
            )

            if resultado["status"] == "DUPLICADO":
                existente = resultado.get(
                    "lancamento_existente",
                    "",
                )

                print(
                    "[OP475] "
                    f"NF={nf} | "
                    f"série={serie_atual} ocupada | "
                    f"lançamento={existente or '-'}"
                )

                serie_atual += 1
                continue

            return {
                "sucesso": True,
                "lancamento":
                    resultado["lancamento"],
                "evento":
                    evento,
                "evento_descricao":
                    descricao_evento,
                "serie":
                    serie_atual,
                "mensagem":
                    (
                        "Lançamento "
                        f"{resultado['lancamento']} "
                        "criado na OP475 com "
                        f"série {serie_atual}."
                    ),
            }

        raise RuntimeError(
            f"Não foi encontrada série livre para NF {nf} "
            f"após {self.MAX_TENTATIVAS_SERIE} tentativas."
        )