# Nexus Local Agent como tarefa persistente no Windows

## O que muda

A tarefa `Nexus Local Agent` executa o `python.exe` do ambiente virtual em sessão não
interativa. `python.exe` não abre uma janela para tarefas executadas pelo Agendador e,
ao contrário de `pythonw.exe`, preserva códigos de saída confiáveis.

A definição instalada possui:

- principal `LocalService` e diretório de trabalho do projeto;
- trigger na inicialização do Windows e `StartWhenAvailable`;
- reinício a cada minuto em encerramento inesperado;
- tempo de execução ilimitado e funcionamento em bateria;
- política `IgnoreNew` no Agendador mais lock exclusivo no processo;
- polling contínuo, backoff até 60 segundos e recuperação automática da VPS;
- log rotativo em `C:\ProgramData\Nexus\logs\local-agent.log`;
- lock em `C:\ProgramData\Nexus\runtime\local-agent.lock`.

O log mantém cinco arquivos de até 2 MiB. Um filtro remove tokens, senhas, cookies,
Authorization, credenciais em URL e CPFs. Payloads de login ou jobs nunca são
registrados. A mensagem `Polling ativo` a cada cinco minutos diferencia processo
saudável de uma tarefa que apenas aparece como `Running`.

`LastTaskResult=267009` equivale a `0x41301`: o Agendador informa que a tarefa ainda
está em execução. Isso não confirma conectividade, carregamento do ambiente ou polling.

## Instalação ou correção

Use PowerShell elevado. A interrupção é explícita para não trocar a definição enquanto
uma instância antiga continua ativa:

```powershell
Set-Location C:\Nexus\ssw_tool_novo

Stop-ScheduledTask -TaskName "Nexus Local Agent" -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

.\deploy\local_agent\install_windows_agent.ps1 `
  -ProjectRoot C:\Nexus\ssw_tool_novo `
  -EnvFile C:\ProgramData\Nexus\local-agent.env `
  -Apply `
  -RegisterTask
```

O instalador preserva `local-agent.env`, token, remote auth e allowlists. Ele cria
somente as pastas de log/lock, aplica ACLs e concede ao `LocalService` leitura do
checkout e escrita nas pastas operacionais `data`, `downloads` e `temp`. Nenhuma tarefa
é iniciada automaticamente pelo instalador.

Se a rede corporativa depender do perfil do usuário interativo, configure de forma
explícita no mesmo arquivo privado, sem exibir o valor em diagnósticos:

```text
NEXUS_LOCAL_AGENT_HTTPS_PROXY=
NEXUS_LOCAL_AGENT_CA_BUNDLE=
```

Deixe ambos vazios para HTTPS direto. O CA bundle, quando usado, deve ficar fora do
repositório; o instalador concede somente leitura ao `LocalService`. Não copie
automaticamente proxy, certificado ou credencial do perfil do usuário.

## Inicialização e diagnóstico

```powershell
Start-ScheduledTask -TaskName "Nexus Local Agent"
Start-Sleep -Seconds 10

.\deploy\local_agent\diagnose_windows_agent.ps1 `
  -ProjectRoot C:\Nexus\ssw_tool_novo `
  -EnvFile C:\ProgramData\Nexus\local-agent.env

Get-Content C:\ProgramData\Nexus\logs\local-agent.log -Tail 40
```

O diagnóstico é offline: valida ambiente, ACLs, executável, argumentos, working
directory, principal, restart, `IgnoreNew`, tempo ilimitado e metadados do log. Ele não
abre SSW nem exibe o token.

Para confirmar uma única instância:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*nexus_local_agent_windows.py*' } |
  Select-Object ProcessId, Name, ExecutablePath, CommandLine
```

Deve existir somente um `python.exe`. Não use `pythonw.exe` e não mantenha execução
manual simultânea; o lock recusará a segunda instância.

## Homologação real no Agendador

Testes automatizados não substituem esta etapa:

1. Confirme no log `Nexus Local Agent iniciado` e depois `Polling ativo` ou comunicação
   restabelecida.
2. Não execute o agente manualmente.
3. Acesse `/login` no Nexus Web e autentique uma conta aprovada.
4. Confirme a abertura do dashboard e a linha `Solicitação de autenticação remota
   processada` no log.
5. Torne a VPS temporariamente indisponível ou use uma janela controlada de reinício já
   aprovada. O log deve registrar uma única indisponibilidade e posteriormente
   `Comunicação com a VPS restabelecida`, sem encerrar a tarefa.
6. Confirme novamente que existe somente um processo do agente.

Não habilite OC73. GCE/OP455 só devem permanecer nas allowlists já aprovadas; esta
correção não altera flags nem executa handlers.

## Recuperação

Se o log não for atualizado ou a definição estiver divergente:

```powershell
Stop-ScheduledTask -TaskName "Nexus Local Agent" -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

.\deploy\local_agent\install_windows_agent.ps1 `
  -ProjectRoot C:\Nexus\ssw_tool_novo `
  -EnvFile C:\ProgramData\Nexus\local-agent.env `
  -Apply `
  -RegisterTask

Start-ScheduledTask -TaskName "Nexus Local Agent"
```

Para interromper sem remover configuração, token ou logs:

```powershell
Stop-ScheduledTask -TaskName "Nexus Local Agent"
```

Não exclua `C:\ProgramData\Nexus\local-agent.env`. O arquivo de lock pode permanecer
no disco; o bloqueio é mantido pelo processo e liberado automaticamente ao encerrar.

