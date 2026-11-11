# LG POD — auditoria e rotina diária na VPS

Rotina definida: SC, PR e SP em paralelo, TODOS OS DIAS às 01:00 de America/Sao_Paulo. A consulta automática usa exclusivamente o dia anterior: por exemplo, 12/11 às 01h consulta 11/11 a 11/11. Não inclui o dia atual.

O pacote adiciona relatórios à tela e prepara serviço/timer systemd. Não instala um agendamento no ChatGPT. O timer só estará ativo na VPS depois do comando --ativar abaixo. Não há alteração de banco ou senhas no pacote.

## 1. Copiar e preparar a VPS

Aguarde os testes/execuções LG terminarem antes de substituir o módulo. Faça backup de modules/lg_pod e scripts LG. Extraia este pacote na raiz /home/automacao/bots/ssw_tool. É cumulativo do módulo SC/PR/SP e preserva as correções de consultas/retries/locks/painel.

As credenciais das três contas e do banco precisam estar no .env DESSA instalação da VPS. Mantenha esse arquivo privado do usuário automacao; o ZIP não contém nem substitui seu .env. Para envio real, as flags das três contas precisam estar habilitadas. Use as configurações de rede/proxy adequadas à VPS; não copie um endereço de proxy da rede local sem conferir a conectividade do servidor.

Na VPS:

```bash
cd /home/automacao/bots/ssw_tool
/home/automacao/venv/bin/python -m pip install -r requirements-lg-pod-auditoria.txt
```

A dependência nova de exportação Excel é openpyxl. O arquivo também garante as dependências básicas do cliente HTTP, sem --upgrade.

Se o LG POD ainda não está registrado no web/main.py dessa VPS, registre o router na aplicação existente (uma única vez):

```python
from modules.lg_pod.routes import router as lg_pod_router
# Depois da criação de app:
app.include_router(lg_pod_router)
```

Não substitua web/main.py inteiro. Caso /lg-pod já exista, preserve o registro atual. Para exibir as novas opções de relatório, reinicie a aplicação web pelo procedimento habitual após encerrar as tarefas em andamento. No ambiente com Supervisor:

```bash
sudo supervisorctl restart ssw_web
```

O timer diário é independente de ssw_web; não precisa da tela aberta e continua funcionando quando a web é reiniciada.

## 2. Validar e ativar o agendamento

Preflight sem entrar no portal e sem criar execuções: verifica as três configurações de envio, dependências, leitura do banco/tabelas e permissão de gravar auditoria.

```bash
/home/automacao/venv/bin/python -m scripts.executar_lg_pod_diario --check --executar
```

Para o primeiro teste na VPS, é possível rodar a rotina sem --executar: consulta apenas ontem e gera os relatórios, sem mapear/enviar.

```bash
/home/automacao/venv/bin/python -u -m scripts.executar_lg_pod_diario
```

Com os testes concluídos e a configuração válida, instalar e habilitar:

```bash
sudo /home/automacao/venv/bin/python -m scripts.instalar_agendamento_lg_pod --ativar
```

O instalador valida as unidades, executa novamente o preflight com o usuário automacao, instala lg-pod-diario.service/.timer e mostra o próximo horário. Ativar o timer NÃO dispara um envio manual imediatamente: a primeira execução ocorre na próxima 01h de Brasília. Caminhos padrão:

- Projeto: /home/automacao/bots/ssw_tool
- Python: /home/automacao/venv/bin/python
- Usuário do serviço: automacao

Se forem diferentes, use --projeto, --python e --usuario. O serviço não é executado como root. Sem --ativar, o instalador prepara as unidades e deixa o timer desabilitado. Os arquivos em deploy/lg_pod permitem revisar o resultado antes de instalar; o instalador os gera a partir da mesma configuração.

O instalador não reinstala durante uma execução diária ativa. Não mantenha outro cron/Agendador externo iniciando a mesma rotina: o timer é a entrada diária, e os locks por login continuam protegendo contra sobreposição com execuções manuais.

## 3. Operação e logs

```bash
systemctl list-timers --all lg-pod-diario.timer
systemctl status lg-pod-diario.timer
sudo journalctl -u lg-pod-diario.service -n 100 --no-pager
```

O serviço usa as três contas em paralelo, cada uma com seu GET_LOCK, sessão HTTP e conexão/keepalive. Não existe restart automático do lote após erro; apenas os retries de leitura já homologados. Map/btnApprove incertos continuam CONFERIR. O timer seguinte só acontece no próximo dia.

Resultados do processo:

- 0: todas as execuções CONCLUIDA.
- 2: finalização com COM_PENDENCIAS, sem falha técnica; systemd aceita esse código, e a auditoria preserva as pendências.
- 1: falha técnica/configuração, ERRO, BLOQUEADA ou outra situação incompleta. Consulte o journal e o painel.

O relatório pode manter pendências herdadas de datas antigas; isso não faz o bot consultar/mapear fora do período de ontem. O serviço tem limite de 8h. Se for encerrado antes da exportação, os itens já registrados permanecem no banco e podem ser exportados pela tela. A auditoria automática é gravada quando o grupo termina.

O timer usa Persistent=false: se a VPS estiver desligada no horário, não dispara um lote fora de hora ao voltar. Para recuperar um dia passado, use uma referência explícita; não existe backfill automático de dias perdidos:

```bash
/home/automacao/venv/bin/python -u -m scripts.executar_lg_pod_diario --referencia 2026-11-10
```

Esse exemplo consulta sem enviar. Para mapear/enviar uma recuperação conferida, acrescente --executar. A referência deve ser anterior ao dia atual de Brasília. Execuções e pendências anteriores continuam protegidas pelo histórico.

Para desativar futuras execuções:

```bash
sudo systemctl disable --now lg-pod-diario.timer
```

Isso desativa o timer, mas não cancela um lote que já esteja rodando. Aguarde o lote encerrar antes de atualizar/reverter arquivos.

## 4. Exportar relatórios na tela

No relatório de uma execução, os links Excel/CSV exportam TODOS os registros que atendem aos filtros de situação e busca atuais, independentemente da página aberta.

A seção Exportar por período seleciona as DATAS DE INÍCIO DAS EXECUÇÕES, em Brasília, e permite Todas/SC/PR/SP. Esse intervalo de auditoria é diferente do período dos comprovantes consultados. Exemplo: uma rotina iniciada em 12/11 às 01h terá data de execução 12/11 e período dos comprovantes 11/11 a 11/11.

Excel contém:

- Execuções: usuário/transportador, período, modo, solicitante, situação, início/fim, contagens totais e itens exportados.
- Comprovantes: execução, NF com zeros preservados como texto, série, POD, arquivo, chave, status observado no portal, etapa, mensagem e atualização.
- Critérios: geração em Brasília, filtros e notas de interpretação.

CSV usa UTF-8 com BOM e separador ponto e vírgula. Mantém os zeros nos bytes do arquivo; se abrir CSV no Excel, importe a coluna NF como texto. O XLSX já preserva isso automaticamente. Para execução sem itens selecionados, CSV inclui uma linha descritiva sem IDs de item, enquanto o Excel mantém o resumo.

ENVIADO indica envio para aprovação, não aprovação final da LG. Status portal é o último estado observado do item; a etapa da automação registra o resultado da tentativa. Uma NF/POD pode aparecer em várias execuções: o relatório mantém essas tentativas para auditoria e não as soma como documentos únicos.

Os downloads exigem sessão autenticada, são somente leitura e não acionam o portal. Conteúdo do portal é exportado como texto, com proteção contra fórmulas em planilhas. Limites por exportação: 200 execuções, 100.000 itens e até 366 dias no filtro por período. Exceder os limites gera erro e pede filtros menores; não baixa um arquivo silenciosamente truncado.

Os dados vêm de leituras do banco. Em uma execução ainda ativa, contagens e itens podem mudar entre consultas; para auditoria de encerramento, prefira execuções finalizadas.

## 5. Arquivos automáticos na VPS

Ao terminar o grupo diário, são criados:

```text
/home/automacao/bots/ssw_tool/downloads/lg_pod/AAAA-MM-DD/ID_DO_LOTE/
  auditoria.xlsx
  auditoria.csv
  manifesto.json
```

AAAA-MM-DD é a referência dos comprovantes (ontem). Cada tentativa gera uma pasta própria, sem sobrescrever auditorias antigas. O manifesto contém referência, horário, IDs/status/contagens das execuções e hashes SHA-256 dos arquivos. Não é uma assinatura digital; os hashes servem para comparação de integridade.

Os arquivos são gravados de forma atômica e com acesso restrito ao usuário do serviço. Falhas técnicas após o grupo terminar também geram relatórios, desde que o banco e o diretório estejam acessíveis. Se uma precondição falhar antes de criar o grupo, haverá log no journal, sem criar um relatório fictício de execução.

## 6. Validação realizada

195 testes Python passaram (173 anteriores + 22 de relatórios/agendamento). Foram validados exportação de todas as páginas, filtros parametrizados, limites sem truncamento, zeros da NF, fórmulas literais, autenticação, proteção de erros, arquivos/hash, D-1 com virada de dia/mês/ano em Brasília, ausência de chamadas ao portal no preflight e códigos de saída distintos para pendência e falha.

Também passaram testes em navegador de desktop/celular, links de exportação acompanhando filtros, formulário de auditoria, polling e continuidade das três contas. O calendário foi normalizado por systemd-analyze: 01h America/Sao_Paulo corresponde a 04h UTC na data testada. As unidades passaram em systemd-analyze verify com caminhos locais de teste; o instalador valida novamente os caminhos reais na VPS.

Toda validação foi local, com dados fictícios e banco/portal substituídos. Não houve acesso SSH à sua VPS, ativação de timer remoto ou envio real à LG nesta implementação. A instalação/ativação remota ainda precisa ser executada pelos comandos acima.

Documentação técnica de referência: [systemd.timer](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html) e [systemd.time](https://www.freedesktop.org/software/systemd/man/258/systemd.time.html).

Para reproduzir os testes opcionais na raiz:

```bash
PYTHONPATH=.:tests/lg_pod python -m unittest test_routes test_visual test_lg_pod test_http_portal test_database_lifecycle test_session_timeout test_mapping_recovery test_pagination_recovery test_http_retries test_multiaccount test_sp test_audit_scheduler -q
```
