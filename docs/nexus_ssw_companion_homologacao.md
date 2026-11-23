# Homologação local — Nexus SSW Companion

Estado: piloto interativo, isolado do Nexus Web e do Local Agent. Este procedimento
não deve ser instalado como serviço ou tarefa agendada.

## Limites de segurança

- Chromium visível, executado na sessão Windows do funcionário;
- contexto novo, temporário e descartado ao terminar;
- somente a origem HTTPS oficial do SSW pode receber navegação principal;
- toda navegação iniciada pelo aplicativo é validada antes de ser aberta;
- após a autenticação, a origem do frame principal é monitorada continuamente e
  qualquer saída da origem oficial descarta o contexto inteiro;
- o operador digita credenciais e resolve o desafio diretamente no SSW;
- o Companion não preenche campos, lê senhas ou automatiza o reCAPTCHA;
- cookies, perfil, estado do navegador e tokens não são exportados;
- mudança de URL não é evidência de login;
- nenhuma rota Nexus, fila do agente ou automação produtiva participa do piloto.

## Pré-requisitos

No PowerShell, usando o Python 64 bits do projeto:

```powershell
Set-Location C:\Nexus\ssw_tool_novo
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

O download do Chromium é uma preparação explícita da estação, não realizada pelo
Companion.

## Proxy corporativo

Os dois CLIs leem explicitamente o `.env` da raiz do projeto. Eles não dependem de
variáveis globais do Windows e não exportam os valores para outros processos:

```dotenv
PROXY_SERVER=http://proxy.corporativo:3128
PROXY_USER=usuario-do-proxy
PROXY_PASS=senha-do-proxy
```

`PROXY_PASS` nunca é gravada no contrato ou nos logs. O valor é mantido somente em
memória e enviado diretamente à opção oficial `proxy` de `chromium.launch`.
Ambientes sem `PROXY_SERVER`, `PROXY_USER` e `PROXY_PASS` continuam abrindo o
Chromium sem proxy.

Para indicar outro arquivo privado explicitamente:

```powershell
.\.venv\Scripts\python.exe -m scripts.configurar_nexus_ssw_companion --env-file C:\Nexus\ssw_tool_novo\.env
```

Falhas HTTP 407 e falhas de túnel/conexão geram apenas mensagens sanitizadas.

## Diagnóstico de inicialização e navegação

O Companion aguarda somente o `commit` da navegação inicial. A autenticação humana
não fica bloqueada esperando `DOMContentLoaded`, que pode permanecer pendente
enquanto o proxy ou o desafio legítimo do site prepara a página.

O log sanitizado registra exclusivamente:

- proxy aplicado e presença de autenticação, como booleanos;
- início do Playwright, Chromium e contexto temporário;
- início e commit da navegação;
- status HTTP numérico, quando fornecido pelo Playwright;
- categorias `proxy_authentication`, `connectivity`, `tls`, `timeout`, `cancelled`,
  `http_status` ou `policy_origin`.

Não são registrados URL, servidor do proxy, usuário, senha, headers, corpos ou
conteúdo da página. Redirecionamentos HTTPS entre `tms.ssw.inf.br` e
`sistema.ssw.inf.br` são aceitos. Não há interceptação global de recursos: uma
saída do frame principal é detectada após o commit e provoca o descarte da sessão.
Essa proteção detectiva não equivale a um bloqueio preventivo de rede.

Comando único recomendado para repetir o configurador com diagnóstico:

```powershell
Set-Location C:\Nexus\ssw_tool_novo; .\.venv\Scripts\python.exe -m scripts.configurar_nexus_ssw_companion --env-file C:\Nexus\ssw_tool_novo\.env --output C:\ProgramData\Nexus\ssw-companion-contract.json --log-file "$env:LOCALAPPDATA\Nexus\logs\ssw-companion.log" --navigation-timeout 60 --human-login-timeout 900
```

## Contrato DOM automático

O configurador reconhece somente a estrutura SSW comprovada para este piloto:

- formulário `#frm` contendo exatamente os campos `f1`, `f2`, `f3` e `f4`;
- domínio em `f1` e identidade em `f3`, lidos antes da submissão;
- menu oficial em `/bin/menu01`;
- marcador do menu `input[name="f3"][maxlength="3"]`;
- unidade autenticada em `f2` no menu.

CPF (`f2` no login), senha (`f4`) e valores do reCAPTCHA não são lidos. O contrato
é criado fora do repositório em:

`C:\ProgramData\Nexus\ssw-companion-contract.json`

Estrutura gerada e validada:

```json
{
  "version": 2,
  "login": {
    "form_selector": "#frm:has(input[name='f1']):has(input[name='f2']):has(input[name='f3']):has(input[name='f4'])",
    "domain_selector": "input[name='f1']",
    "cpf_selector": "input[name='f2']",
    "identity_selector": "input[name='f3']",
    "password_selector": "input[name='f4']"
  },
  "menu": {
    "path": "/bin/menu01",
    "marker_selector": "input[name=\"f3\"][maxlength=\"3\"]",
    "unit_selector": "input[name='f2']"
  }
}
```

O loader aceita esse conjunto de chaves e seletores de forma estrita. Alteração da
estrutura do SSW causa falha segura, em vez de produzir um contrato aproximado.

## Configurador automático

O configurador abre o login oficial no mesmo Chromium temporário usado pela
homologação. Ele reconhece os campos conhecidos, aguarda o operador digitar as
credenciais e resolver manualmente o reCAPTCHA, e detecta o menu sem pedir
seletores nem uma confirmação textual.

```powershell
Set-Location C:\Nexus\ssw_tool_novo
.\.venv\Scripts\python.exe -m scripts.configurar_nexus_ssw_companion
```

Se a tela inicial permanecer vazia, o configurador permite até duas recargas
controladas. Essa recuperação é bloqueada de forma irreversível assim que surgir o
formulário, qualquer campo `f1`–`f4` ou uma estrutura de CAPTCHA. Depois de entrar
no estado `AGUARDANDO_LOGIN_HUMANO`, o ciclo apenas observa a página por até 900
segundos: não executa `reload`, novo `goto` ou recriação de página/contexto. O
timeout de navegação inicial é independente desse prazo humano.

O configurador não clica, preenche ou resolve o desafio. O menu só é aceito pela
combinação de origem oficial, caminho, marcador estrutural e unidade; identidade e
domínio são vinculados aos valores que o próprio operador informou antes do envio.
Se o prazo humano expirar, o contexto temporário é encerrado sem recarregar.

O contrato é gravado de forma atômica. No Windows, a herança de ACL é removida e
somente o usuário interativo atual recebe leitura e gravação. Caso já exista um
arquivo, ele será preservado, salvo se o operador digitar exatamente
`SOBRESCREVER`.

Para escolher outro destino explicitamente:

```powershell
.\.venv\Scripts\python.exe -m scripts.configurar_nexus_ssw_companion --output C:\ProgramData\Nexus\ssw-companion-contract.json
```

## Comando exato — autenticação, sem sondagem

```powershell
Set-Location C:\Nexus\ssw_tool_novo
.\.venv\Scripts\python.exe -m scripts.nexus_ssw_companion --contract C:\ProgramData\Nexus\ssw-companion-contract.json
```

1. Informe localmente domínio, usuário e unidade esperados.
2. No Chromium, digite as credenciais diretamente na página do SSW.
3. Resolva manualmente o reCAPTCHA.
4. Aguarde o Companion reconhecer automaticamente o menu autenticado.

O log sanitizado fica em
`%LOCALAPPDATA%\Nexus\logs\ssw-companion.log` e contém somente transições e
categorias de erro.

## Sondagem somente leitura

O repositório ainda não contém um contrato DOM real e aprovado do menu atual do
SSW. Portanto, nenhuma sondagem real fica habilitada por padrão.

Depois de validar com o fornecedor uma evidência somente leitura já presente na
página, acrescente ao contrato:

```json
"readonly_probe": {
  "selector": "SELETOR_APROVADO_DA_EVIDENCIA",
  "pattern": "^(?P<value>EVIDENCIA_ESPERADA)$",
  "authorized": true
}
```

Execute então, de forma supervisionada:

```powershell
.\.venv\Scripts\python.exe -m scripts.nexus_ssw_companion --contract C:\ProgramData\Nexus\ssw-companion-contract.json --readonly-probe
```

A sondagem apenas lê texto já renderizado. Ela não clica, envia formulários, abre
opções, baixa arquivos ou executa código dentro da página. O conteúdo observado não
é gravado no log.

## Interpretação

Sucesso comprova somente que, naquela estação, identidade e unidade puderam ser
confirmadas no mesmo contexto que recebeu a autenticação humana. Não autentica o
Nexus e não autoriza reutilizar a sessão fora desse navegador.

