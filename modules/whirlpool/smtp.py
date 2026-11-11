from modules.whirlpool.schemas import EstadoEnvio


class EnvioWhirlpoolDesabilitado(RuntimeError):
    pass


class WhirlpoolSMTP:
    """Porta SMTP da Whirlpool; envio real permanece bloqueado nesta Sprint."""

    def enviar(self, *_args, **_kwargs) -> EstadoEnvio:
        raise EnvioWhirlpoolDesabilitado(
            "Envio SMTP Whirlpool não foi habilitado para esta versão."
        )
