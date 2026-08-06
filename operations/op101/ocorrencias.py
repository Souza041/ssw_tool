import re
import time

from dataclasses import asdict, dataclass
from datetime import date, datetime
from html import unescape


@dataclass
class CTRCConsultado:
    encontrado: bool
    serie: str
    numero: str
    seq_ctrc: str = ""
    local: str = ""
    familia: str = ""
    mensagem: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OcorrenciaHistorico:
    codigo: str
    descricao: str
    data_hora: str = ""
    familia: str = ""
    unidade: str = ""
    usuario: str = ""
    complemento: str = ""
    ocorrencia_ssw: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResultadoLancamento:
    success: bool
    codigo: str
    mensagem: str
    resposta: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
    
class OP101Ocorrencias:
    """
    Consulta CTRCs na OP101.

    Nesta etapa, esta classe somente:
    - abre a OP101;
    - consulta o CTRC;
    - abre o documento;
    - extrai seq_ctrc, local e FAMILIA.

    Nenhuma ocorrência é lançada aqui.
    """

    def __init__(self, client):
        self.client = client

    @staticmethod
    def _dummy() -> str:
        return str(int(time.time() * 1000))

    @staticmethod
    def _normalizar_serie(valor: str) -> str:
        return str(valor or "").strip().upper()

    @staticmethod
    def _normalizar_numero(valor: str) -> str:
        texto = str(valor or "").strip()

        # Remove caracteres que não sejam números.
        return re.sub(r"\D", "", texto)

    @staticmethod
    def _data_ssw(valor: date | str) -> str:
        if isinstance(valor, date):
            return valor.strftime("%d%m%y")

        texto = str(valor or "").strip()

        if not re.fullmatch(r"\d{6}", texto):
            raise ValueError(
                "A data da OP101 deve estar no formato DDMMAA."
            )

        return texto

    @staticmethod
    def _data_tela_ssw(
        valor: date | str,
    ) -> str:
        if isinstance(valor, date):
            return (
                f"{valor.day}/"
                f"{valor.month}/"
                f"{str(valor.year)[2:]}"
            )

        texto = str(valor or "").strip()

        if re.fullmatch(r"\d{6}", texto):
            return (
                f"{int(texto[0:2])}/"
                f"{int(texto[2:4])}/"
                f"{texto[4:6]}"
            )

        if re.fullmatch(
            r"\d{1,2}/\d{1,2}/\d{2}",
            texto,
        ):
            return texto

        raise ValueError(
            "Data inválida para a tela da OP101."
        )

    def abrir(self, unidade: str) -> None:
        """
        Abre a OP101 explicitamente na unidade informada.

        Para CTRCs JOI, a unidade utilizada será JOI.
        """

        unidade = self._normalizar_serie(unidade)

        if not unidade:
            raise ValueError(
                "Unidade não informada para abertura da OP101."
            )

        # Troca explicitamente a unidade da sessão.
        self.client.get(
            "/bin/ssw0082",
            params={
                "quadro_menu01": unidade,
                "dummy": self._dummy(),
            },
        )

        # Abre a opção 101.
        self.client.post(
            "/bin/menu01",
            {
                "act": "TRO",
                "f2": unidade,
                "f3": "101",
                "dummy": self._dummy(),
            },
        )

        # Carrega a tela da OP101.
        self.client.post(
            "/bin/ssw0053",
            {
                "sequencia": "101",
                "dummy": self._dummy(),
            },
        )

    def pesquisar_chave(
        self,
        serie: str,
        numero: str,
        data_inicial: date | str,
        data_final: date | str,
    ) -> str:
        """
        Executa a pesquisa rápida usada pela OP101.

        Esse GET foi confirmado no HAR:
        /bin/ssw0385
        """

        serie = self._normalizar_serie(serie)
        numero = self._normalizar_numero(numero)

        data_ini_ssw = self._data_ssw(data_inicial)
        data_fim_ssw = self._data_ssw(data_final)

        response = self.client.get(
            "/bin/ssw0385",
            params={
                "dd_select": "ctrc",
                "dd_chave": numero,
                "dd_f_t_data_ini": data_ini_ssw,
                "dd_f_t_data_fin": data_fim_ssw,
                "dd_f_t_ser_ctrc": serie,
            },
        )

        return response.text

    def abrir_ctrc(
        self,
        serie: str,
        numero: str,
        data_inicial: date | str,
        data_final: date | str,
    ) -> str:
        """
        Abre o CTRC após a pesquisa.

        Reproduz o POST act=P1 capturado no HAR.
        """

        serie = self._normalizar_serie(serie)
        numero = self._normalizar_numero(numero)

        data_ini_ssw = self._data_ssw(data_inicial)
        data_fim_ssw = self._data_ssw(data_final)

        response = self.client.post(
            "/bin/ssw0053",
            {
                "act": "P1",
                "t_ser_ctrc": serie,
                "t_nro_ctrc": numero,
                "t_data_ini": data_ini_ssw,
                "t_data_fin": data_fim_ssw,
                "dd_f_t_data_ini": "",
                "dd_f_t_data_fin": "",
                "dd_f_t_ser_ctrc": "",
                "dd_f_t_ser_nf": "",
                "dd_f_t_nro_pedido": "",
                "data_ini_inf": "30/12/99",
                "data_fin_inf": "30/12/99",
                "seq_ctrc": "0",
                "local": "",
                "FAMILIA": "",
                "dummy": self._dummy(),
            },
        )

        return response.text

    @staticmethod
    def _extrair_input_hidden(
        html: str,
        nome: str,
    ) -> str:
        """
        Extrai inputs mesmo quando o HTML do SSW não usa aspas
        em todos os atributos.
        """

        padroes = [
            rf"""
                <input
                [^>]*
                name\s*=\s*["']?{re.escape(nome)}["']?
                [^>]*
                value\s*=\s*["']([^"']*)["']
            """,
            rf"""
                <input
                [^>]*
                name\s*=\s*["']?{re.escape(nome)}["']?
                [^>]*
                value\s*=\s*([^\s>]+)
            """,
            rf"""
                <input
                [^>]*
                value\s*=\s*["']([^"']*)["']
                [^>]*
                name\s*=\s*["']?{re.escape(nome)}["']?
            """,
        ]

        for padrao in padroes:
            match = re.search(
                padrao,
                html,
                flags=re.IGNORECASE | re.VERBOSE,
            )

            if match:
                return match.group(1).strip()

        return ""

    @classmethod
    def extrair_dados_ctrc(
        cls,
        html: str,
    ) -> dict:
        """
        Extrai os identificadores internos necessários para
        abrir a tela de ocorrências posteriormente.
        """

        seq_ctrc = cls._extrair_input_hidden(
            html,
            "seq_ctrc",
        )

        local = cls._extrair_input_hidden(
            html,
            "local",
        )

        familia = cls._extrair_input_hidden(
            html,
            "FAMILIA",
        )

        # Fallback: alguns layouts expõem seq_ctrc somente
        # dentro de JavaScript ou links do DACTE.
        if not seq_ctrc:
            match = re.search(
                r"seq_ctrc\s*=\s*(\d+)",
                html,
                flags=re.IGNORECASE,
            )

            if match:
                seq_ctrc = match.group(1)

        if not familia:
            match = re.search(
                r"FAMILIA\s*=\s*([A-Z0-9_-]+)",
                html,
                flags=re.IGNORECASE,
            )

            if match:
                familia = match.group(1).upper()

        return {
            "seq_ctrc": seq_ctrc,
            "local": local,
            "familia": familia,
        }

    @staticmethod
    def _pagina_indica_nao_encontrado(
        html: str,
    ) -> bool:
        texto = str(html or "").lower()

        indicadores = (
            "ctrc não encontrado",
            "ctrc nao encontrado",
            "documento não encontrado",
            "documento nao encontrado",
            "nenhum registro encontrado",
            "não foram encontrados",
            "nao foram encontrados",
        )

        return any(
            indicador in texto
            for indicador in indicadores
        )

    def abrir_ocorrencias(
        self,
        *,
        serie: str,
        numero: str,
        seq_ctrc: str,
        local: str,
        familia: str,
        data_referencia: date | str,
    ) -> str:
        """
        Abre o histórico de ocorrências do CTRC pela OP101.

        Esta função apenas consulta. Nenhuma ocorrência é lançada.
        """

        serie = self._normalizar_serie(serie)
        numero = self._normalizar_numero(numero)
        seq_ctrc = self._normalizar_numero(seq_ctrc)

        if not seq_ctrc:
            raise ValueError(
                "seq_ctrc não informado para abrir ocorrências."
            )

        data_tela = self._data_tela_ssw(
            data_referencia
        )

        response = self.client.post(
            "/bin/ssw0053",
            {
                "act": "O",
                "aviso_resgate": "#aviso_resgate#",
                "dd_f_t_data_ini": "",
                "dd_f_t_data_fin": "",
                "dd_f_t_ser_ctrc": "",
                "dd_f_t_ser_nf": "",
                "dd_f_t_nro_pedido": "",
                "g_ctrc_ser_ctrc": serie,
                "g_ctrc_nro_ctrc": numero,
                "gw_nro_nf_ini": "0",
                "g_ctrc_nf_vol_ini": "0",
                "gw_ctrc_nr_sscc": "",
                "g_ctrc_nro_ctl_form": "0",
                "gw_ctrc_parc_nro_ctrc_parc": "0",
                "g_ctrc_c_chave_fis": "",
                "gw_gaiola_codigo": "0",
                "gw_pallet_codigo": "0",
                "local": local or "Q",
                "data_ini_inf": data_tela,
                "data_fin_inf": data_tela,
                "seq_ctrc": seq_ctrc,
                "FAMILIA": familia or "ROD",
                "dummy": self._dummy(),
            },
        )

        return response.text

    def consultar_ctrc(
        self,
        serie: str,
        numero: str,
        data_referencia: date | str,
    ) -> CTRCConsultado:
        """
        Consulta completa e segura da OP101.

        Para emissões do dia, usa a própria data de referência
        como início e fim da pesquisa.
        """

        serie = self._normalizar_serie(serie)
        numero = self._normalizar_numero(numero)
        data_ssw = self._data_ssw(data_referencia)

        if not serie:
            raise ValueError(
                "Série do CTRC não informada."
            )

        if not numero:
            raise ValueError(
                "Número do CTRC não informado."
            )

        self.abrir(unidade=serie)

        self.pesquisar_chave(
            serie=serie,
            numero=numero,
            data_inicial=data_ssw,
            data_final=data_ssw,
        )

        html = self.abrir_ctrc(
            serie=serie,
            numero=numero,
            data_inicial=data_ssw,
            data_final=data_ssw,
        )

        if self._pagina_indica_nao_encontrado(html):
            return CTRCConsultado(
                encontrado=False,
                serie=serie,
                numero=numero,
                mensagem="CTRC não encontrado na OP101.",
            )

        dados = self.extrair_dados_ctrc(html)

        seq_ctrc = dados["seq_ctrc"]

        if not seq_ctrc or seq_ctrc == "0":
            return CTRCConsultado(
                encontrado=False,
                serie=serie,
                numero=numero,
                local=dados["local"],
                familia=dados["familia"],
                mensagem=(
                    "A OP101 abriu a resposta, mas não foi "
                    "possível extrair o seq_ctrc."
                ),
            )

        return CTRCConsultado(
            encontrado=True,
            serie=serie,
            numero=numero,
            seq_ctrc=seq_ctrc,
            local=dados["local"] or "Q",
            familia=dados["familia"] or "ROD",
            mensagem="CTRC localizado com sucesso.",
        )

    @staticmethod
    def _limpar_campo_xml(
        valor: str,
    ) -> str:
        texto = unescape(
            str(valor or "")
        )

        texto = re.sub(
            r"<[^>]+>",
            "",
            texto,
        )

        texto = texto.replace(
            "\xa0",
            " ",
        )

        texto = " ".join(
            texto.split()
        )

        return texto.strip()

    @classmethod
    def listar_ocorrencias(
        cls,
        html: str,
    ) -> list[OcorrenciaHistorico]:
        """
        Converte as linhas XML da tela de ocorrências em objetos.

        Na tela da OP101:
        - f0: data/hora
        - f1: família
        - f2: unidade
        - f4/f12: usuário
        - f5: código e descrição da ocorrência
        - f6: informações complementares
        - f10: ocorrência equivalente no SSW
        """

        html = unescape(
            str(html or "")
        )

        registros_xml = re.findall(
            r"<r>(.*?)</r>",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )

        ocorrencias = []

        for registro_xml in registros_xml:
            campos = {}

            for indice in range(13):
                match = re.search(
                    rf"<f{indice}>(.*?)</f{indice}>",
                    registro_xml,
                    flags=re.IGNORECASE | re.DOTALL,
                )

                campos[f"f{indice}"] = (
                    cls._limpar_campo_xml(
                        match.group(1)
                    )
                    if match
                    else ""
                )

            campo_ocorrencia = campos["f5"]

            match_codigo = re.match(
                r"^\s*(\d+)\s*-\s*(.*)$",
                campo_ocorrencia,
            )

            if not match_codigo:
                continue

            codigo = match_codigo.group(1).strip()
            descricao = match_codigo.group(2).strip()

            usuario = campos["f12"]

            if not usuario:
                usuario = re.sub(
                    r"#u#|#/u#|\|\d+",
                    "",
                    campos["f4"],
                    flags=re.IGNORECASE,
                ).strip()

            ocorrencias.append(
                OcorrenciaHistorico(
                    codigo=codigo,
                    descricao=descricao,
                    data_hora=campos["f0"],
                    familia=campos["f1"],
                    unidade=campos["f2"],
                    usuario=usuario,
                    complemento=campos["f6"],
                    ocorrencia_ssw=campos["f10"],
                )
            )

        return ocorrencias

    @staticmethod
    def encontrar_ocorrencia(
        ocorrencias: list[OcorrenciaHistorico],
        codigo: str | int,
    ) -> OcorrenciaHistorico | None:
        codigo_procurado = str(
            codigo
        ).strip()

        for ocorrencia in ocorrencias:
            if ocorrencia.codigo == codigo_procurado:
                return ocorrencia

        return None

    @classmethod
    def possui_ocorrencia_73(
        cls,
        ocorrencias: list[OcorrenciaHistorico],
    ) -> bool:
        return (
            cls.encontrar_ocorrencia(
                ocorrencias,
                73,
            )
            is not None
        )

    @staticmethod
    def _data_lancamento_ssw(
        valor: date | str,
    ) -> str:
        if isinstance(valor, date):
            return valor.strftime("%d%m%y")

        texto = str(valor or "").strip()

        if re.fullmatch(r"\d{6}", texto):
            return texto

        if re.fullmatch(
            r"\d{1,2}/\d{1,2}/\d{2,4}",
            texto,
        ):
            partes = texto.split("/")

            dia = int(partes[0])
            mes = int(partes[1])
            ano = int(partes[2])

            if ano >= 2000:
                ano -= 2000

            return f"{dia:02d}{mes:02d}{ano:02d}"

        raise ValueError(
            "Data inválida para lançamento da ocorrência."
        )


    @staticmethod
    def _hora_lancamento_ssw(
        valor: str | None = None,
    ) -> str:
        if valor:
            texto = re.sub(
                r"\D",
                "",
                str(valor),
            )

            if len(texto) != 4:
                raise ValueError(
                    "Hora deve estar no formato HHMM."
                )

            return texto

        return datetime.now().strftime("%H%M")

    def consultar_codigo_ocorrencia(
        self,
        codigo: str | int,
    ) -> str:
        codigo = str(codigo).strip()

        response = self.client.get(
            "/bin/ssw0385",
            params={
                "tipo": "ocorrencia",
                "key": codigo,
                "dummy": self._dummy(),
            },
        )

        return response.text

    def lancar_ocorrencia_73(
        self,
        *,
        seq_ctrc: str,
        familia: str,
        data_ocorrencia: date | str,
        hora_ocorrencia: str | None = None,
        observacao: str = (
            "LANCAMENTO AUTOMATICO - BOT OCORRENCIA 73"
        ),
        local: str = "",
    ) -> ResultadoLancamento:
        """
        Lança a ocorrência 73 no CTRC.

        Deve ser chamado apenas após confirmar que a ocorrência
        ainda não existe no histórico.
        """

        seq_ctrc = self._normalizar_numero(
            seq_ctrc
        )

        familia = str(
            familia or "ROD"
        ).strip().upper()

        if not seq_ctrc:
            raise ValueError(
                "seq_ctrc não informado para lançamento."
            )

        data_ssw = self._data_lancamento_ssw(
            data_ocorrencia
        )

        hora_ssw = self._hora_lancamento_ssw(
            hora_ocorrencia
        )

        # Reproduz a consulta feita pela tela antes do lançamento.
        self.consultar_codigo_ocorrencia(
            73
        )

        response = self.client.post(
            "/bin/ssw0122",
            {
                "act": "II3",
                "f3": "73",
                "ocor_descr": (
                    "ENTREGA SERA REALIZADA AMANHA"
                ),
                "f4": data_ssw,
                "f5": hora_ssw,
                "f6": observacao,
                "f8": "N",
                "f11": "N",
                "dd_f_t_data_ini": "",
                "dd_f_t_data_fin": "",
                "dd_f_t_ser_ctrc": "",
                "dd_f_t_ser_nf": "",
                "dd_f_t_nro_pedido": "",
                "seq_instr": "0",
                "detalhe_oco": "",
                "detalhe_ins": "",
                "tipoFoto": "instr_foto",
                "nomeFoto": "",
                "extraFoto": "",
                "nomeFotoUsed": "",
                "ctrl_gelo_tp_produto": "",
                "ctrl_gelo_tp_gelo": "",
                "ctrl_gelo_data_inicio": "",
                "ctrl_gelo_hora_inicio": "",
                "ctrl_gelo_prazo": "",
                "seq_ctrc": seq_ctrc,
                "data_ini_inf": (
                    self._data_tela_ssw(
                        data_ocorrencia
                    )
                ),
                "data_fin_inf": "30/12/99",
                "local": local or "",
                "FAMILIA": familia,
                "dummy": self._dummy(),
            },
        )

        texto = str(
            response.text or ""
        )

        texto_normalizado = texto.lower()

        indicadores_erro = (
            "erro",
            "não foi possível",
            "nao foi possivel",
            "ocorrência inválida",
            "ocorrencia invalida",
            "campo obrigatório",
            "campo obrigatorio",
        )

        if any(
            indicador in texto_normalizado
            for indicador in indicadores_erro
        ):
            return ResultadoLancamento(
                success=False,
                codigo="73",
                mensagem=(
                    "SSW retornou erro ao lançar ocorrência 73."
                ),
                resposta=texto[:2000],
            )

        return ResultadoLancamento(
            success=True,
            codigo="73",
            mensagem=(
                "Ocorrência 73 enviada ao SSW."
            ),
            resposta=texto[:2000],
        )

    def confirmar_ocorrencia_73(
        self,
        *,
        serie: str,
        numero: str,
        seq_ctrc: str,
        local: str,
        familia: str,
        data_referencia: date | str,
    ) -> OcorrenciaHistorico | None:
        html = self.abrir_ocorrencias(
            serie=serie,
            numero=numero,
            seq_ctrc=seq_ctrc,
            local=local,
            familia=familia,
            data_referencia=data_referencia,
        )

        historico = self.listar_ocorrencias(
            html
        )

        return self.encontrar_ocorrencia(
            historico,
            73,
        )