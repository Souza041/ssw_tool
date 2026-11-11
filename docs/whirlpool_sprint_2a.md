# Whirlpool — Sprint 2A

Esta etapa adiciona somente autenticação, consulta e download no portal em
`PORTAL_DRY_RUN`. SMTP permanece bloqueado e a planilha não recebe
`EMAIL ENVIADO = OK`.

## Controles

- `WHIRLPOOL_PORTAL_ENABLED=true` e `WHIRLPOOL_PORTAL_DRY_RUN=true` são
  necessários simultaneamente;
- o usuário SSW autenticado deve constar, por correspondência exata e sem
  diferença entre maiúsculas/minúsculas, em `WHIRLPOOL_PORTAL_AUTHORIZED_USERS`;
- cada execução exige a confirmação textual `HOMOLOGAR PORTAL SEM ENVIO`;
- `WHIRLPOOL_PORTAL_MAX_TRANSPORTS` limita o lote;
- travas de arquivo impedem concorrência sobre o mesmo par planta/transporte
  entre processos no mesmo host e diretório de dados;
- PDFs são validados, limitados em tamanho e armazenados sob
  `var/whirlpool/files/<execução>/portal/<job>`;
- downloads passam por rota autenticada e nunca por `/downloads`.

## Variáveis

As URLs e as duas credenciais do portal são lidas exclusivamente das variáveis
`WHIRLPOOL_PORTAL_URL_1`, `WHIRLPOOL_PORTAL_URL_2`,
`WHIRLPOOL_PORTAL_USER_1`, `WHIRLPOOL_PORTAL_PASSWORD_1`,
`WHIRLPOOL_PORTAL_USER_2` e `WHIRLPOOL_PORTAL_PASSWORD_2`. Proxy é opcional.
Não registrar esses valores em arquivos versionados ou logs.

## Preparação da VPS Ubuntu 24.04

Com a versão revisada do código e dentro do ambiente virtual:

```bash
python -m pip install -r requirements.txt
python -m playwright install --with-deps chromium
python -m pytest -q tests/test_whirlpool.py tests/test_whirlpool_portal.py
```

Antes da homologação, validar permissões restritas de `WHIRLPOOL_DATA_DIR`,
configurar as variáveis no ambiente efetivo do Supervisor e reiniciar somente
após revisão operacional. O primeiro teste real deve usar uma planilha aprovada,
um lote pequeno e uma janela supervisionada.

## Desativação

Definir ambos como `false` e reiniciar o processo:

```text
WHIRLPOOL_PORTAL_ENABLED=false
WHIRLPOOL_PORTAL_DRY_RUN=false
```

Os PDFs privados já baixados devem seguir a política de retenção aprovada; não
devem ser movidos para diretórios públicos.
