# LG POD — instalação e homologação

Módulo integrado à branch consolidada `codex/whirlpool-clean`.
A tela fica em `/lg-pod`, no mesmo menu de Documentos GCE.

## Instalar no projeto

1. Acrescente as variáveis de `.env.lg_pod.example` ao seu `.env` existente. Preencha acesso ao banco `lg_pod`, usuário e senha LG. Mantenha `LG_POD_ENVIO_HABILITADO=false` para a primeira consulta. Não substitua o `.env` inteiro.
2. Após autorização separada para homologação, instale as tabelas e o Chromium no ambiente apropriado:

```bash
python -m scripts.instalar_lg_pod
python -m playwright install chromium
```

Não execute `integrar_lg_pod.py` nesta branch: router e menu já estão integrados, e o utilitário geraria arquivos `.antes_lg_pod` desnecessários.

O instalador cria **somente as duas tabelas novas** no banco apontado por `LG_POD_DB_NAME`. Usa MySQL/InnoDB e `utf8mb4`; não faz conexão com os bancos das outras automações. O script de DDL é `modules/lg_pod/schema.sql`. Nenhuma instalação de tabela foi executada durante a integração do código.

Playwright, PyMySQL, dotenv, FastAPI e Jinja2 já constam no `requirements.txt` enviado. No Linux, caso faltem bibliotecas do Chromium, use `python -m playwright install --with-deps chromium` com as permissões apropriadas. Instale o navegador com o mesmo usuário que executa a aplicação.

3. Reinicie a aplicação pelo procedimento habitual. Na VPS com Supervisor:

```bash
sudo supervisorctl restart ssw_web
```

4. Entre com a sessão habitual do SSW Tool e abra **LG - Mapeamento POD**. O módulo usa credenciais LG próprias do `.env`; não consulta o SSW para mapear.

## Primeira consulta e primeiro envio

- Escolha um período retroativo curto e mantenha **Consultar sem enviar**.
- Confira os nomes, NFs, séries, ignorados e erros no relatório.
- A consulta preenche apenas o período e o número da NF. Data do canhoto, observação e protocolo vêm do portal; não são inventados nem buscados no SSW.
- Para homologar o envio, ajuste `LG_POD_ENVIO_HABILITADO=true`, reinicie a aplicação e escolha um período pequeno em **Mapear e enviar para aprovação**.
- Confirme no portal o resultado da primeira execução. O retorno “Enviado” significa envio para aprovação, não aprovação final da LG.

A data de emissão da NF é um filtro separado da criação do comprovante. `LG_POD_NF_DATA_INICIAL=2025-01-01` é uma janela inicial ampla; ajuste se houver notas mais antigas. O fim dessa janela é sempre hoje em São Paulo.

## Regras e retomada

- Novo mapeamento: nome começa com `ROBR`, nome tem NF reconhecível, status textual `PENDING`, código interno `0` e transportador igual ao configurado.
- NFs ficam em texto: zeros à esquerda são preservados.
- A busca deve retornar exatamente uma nota e corresponder à NF e ao transportador. Duplicidade ou ausência vira erro de consulta.
- A tabela da nota é dividida em duas partes no HTML; o parser une pelo caminho do registro.
- A paginação do JDA carrega blocos de 50 linhas. O cliente carrega a lista completa, confere IDs e totais, e repete isso após redesenhar a lista. Nunca escolhe por posição.
- O mapeamento exige retorno `SUCCESS` com a NF e série esperadas, e uma nova consulta deve mostrar `MAPPED`/código `2` com somente aquela NF vinculada.
- Só um mapeamento confirmado pelo próprio histórico pode ser retomado diretamente no envio. Comprovantes `MAPPED` sem esse histórico não entram no novo processamento.
- O envio exige a mensagem de sucesso da própria resposta de `btnApprove`; HTTP 200 ou desaparecimento da lista, isoladamente, não bastam.
- Intenções de mapear/enviar são gravadas antes do clique. Se a conexão cair sem confirmação, o item fica em `CONFERIR` e não é repetido automaticamente. Uma interrupção depois do retorno do portal mas antes do registro no banco também exige conferência.
- Cada execução usa um bloqueio MySQL mantido na conexão. Se outro processo estiver executando, a nova tentativa fica `BLOQUEADA`; se perder o bloqueio, para antes da próxima escrita.
- O relatório é compartilhado com os usuários autenticados, como no módulo GCE. Os formulários exigem sessão válida e token CSRF.

### Resolver resultado incerto

Confira o comprovante no portal e use o **Registro** mostrado na linha do relatório. Estes comandos apenas registram a conferência no banco; não enviam nada à LG.

Se o envio estiver confirmado:

```bash
python -m scripts.resolver_lg_pod --item 42 --resultado enviado --motivo "Envio confirmado no portal por RESPONSAVEL em DATA/HORA"
```

Somente se o comprovante ainda estiver `PENDING`, sem mapeamento e sem envio:

```bash
python -m scripts.resolver_lg_pod --item 42 --resultado liberar --motivo "POD PENDING sem mapeamento, conferido por RESPONSAVEL em DATA/HORA"
```

Se estiver `MAPPED` após uma tentativa incerta, confira e conclua o envio pelo portal, depois registre `enviado`. Não libere um comprovante mapeado para repetir o mapeamento.

## Rotina diária e retroativo pelo terminal

Consulta de hoje, sem alterações no portal:

```bash
python -m scripts.executar_lg_pod
```

Consulta retroativa:

```bash
python -m scripts.executar_lg_pod --inicio 2026-11-01 --fim 2026-11-04
```

Mapear e enviar no período escolhido, com envio habilitado no `.env`:

```bash
python -m scripts.executar_lg_pod --inicio 2026-11-01 --fim 2026-11-04 --executar
```

Quando o acumulado estiver alinhado, a rotina diária usa apenas:

```bash
python -m scripts.executar_lg_pod --executar
```

Sem datas, a data corrente é calculada a cada execução em `America/Sao_Paulo`. Esse comando pode ser colocado no cron/Agendador de Tarefas no horário operacional escolhido. **Este pacote não instala agendamento automaticamente.** Use o Python do mesmo ambiente da aplicação, diretório de trabalho na raiz do projeto e um horário posterior à chegada diária dos comprovantes. O histórico vai para as mesmas tabelas da tela.

## Validação realizada

- Testes unitários do parser e fluxo, com fixtures pequenas extraídas dos HARs e cenários simulados de erro, retomada, duplicidade e perda de bloqueio.
- Testes HTTP da tela: sessão expirada, CSRF, início da execução e escape de texto vindo do portal.
- Integração do menu/router executada duas vezes, sem duplicar conteúdo.
- Template renderizado e inspecionado no Chromium, sem transbordamento horizontal em desktop.
- Reprodução offline no Chromium com JavaScript e respostas capturados, para consulta, carregamento das páginas, mapeamento e envio para aprovação (respostas gravadas). O último fragmento dos HARs foi capturado depois do mapeamento; no ensaio de paginação um ID sintético substituiu o registro já movido, para não misturar estados de momentos distintos.

Para repetir os testes, com as dependências do projeto e `httpx` instaladas:

```bash
python -m unittest discover -s tests/lg_pod -v
```

**Ainda precisa ser homologado no seu ambiente:** autenticação LG atual, MySQL real, certificado/proxy, envio para aprovação e execução com dados reais. Não houve login, DDL nem envio em produção durante a implementação.

## Arquivos e reversão

O pacote não contém `.env`, credenciais, cookies, HARs completos ou dados dos outros módulos. Não substitui `web/main.py` nem `base.html` por cópias: a integração é feita pelo script.

Para reverter, remova as duas linhas que mencionam `lg_pod_router` de `web/main.py` e o link `/lg-pod` de `base.html`, depois reinicie. Preserve as tabelas se quiser manter o histórico. Os backups podem ser usados se os arquivos não receberam outras mudanças desde a integração.
