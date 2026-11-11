# Whirlpool — Sprint 1

Esta versão implementa somente upload privado, validação, simulação, histórico e
download autenticado. Não há envio SMTP nem navegação Playwright ativa.

## Armazenamento

`WHIRLPOOL_DATA_DIR` define o diretório privado. O padrão é `var/whirlpool`, fora
de `/downloads` e de `web/static`. O processo do Supervisor precisa de permissão
de leitura e escrita nesse diretório. Arquivos de entrada e saída não são servidos
por `StaticFiles`; o download passa por uma rota que valida a sessão e o dono da
execução.

## Planilha

Abas obrigatórias:

- `BASE`: `OI`, `Transportes`, `COLETAS`, `Chave`, `plantas`, `data de coleta`,
  `NF`, `Cliente`, `Cidade`, `UF` e `SIGLA`;
- `email`: `SIGLA` e `E-MAIL`.

A simulação cria uma cópia com `STATUS WHIRLPOOL` e `CHAVE IDEMPOTENCIA`. Ela não
marca novos registros como enviados e não chama portal ou SMTP.

## Dependências legadas e VPS

A automação Windows original usa Playwright síncrono, `win32com`, `pythoncom`,
Outlook, `input()` e diretórios locais configurados. `win32com`/Outlook e
interações de terminal não fazem parte do módulo Nexus. A futura automação de
portal deverá usar Chromium headless e proxy opcional.

Playwright ainda não é dependência desta Sprint. Antes da fase de portal, revisar
e fixar uma versão compatível e, na VPS Ubuntu 24.04, instalar seus requisitos e
o Chromium com o comando oficial correspondente à versão aprovada.

## Homologação local

```powershell
python -m pytest -q tests/test_whirlpool.py
python -m pytest -q tests/test_session_config.py tests/test_op001.py tests/test_op001_transporte.py
python -m uvicorn web.main:app --host 127.0.0.1 --port 8000
```

O último comando exige a configuração de sessão já adotada pelo Nexus. Acessar
`/whirlpool/emails` após login SSW. Não configurar SMTP ou Playwright nesta fase.
