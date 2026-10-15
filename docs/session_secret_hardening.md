# Hardening da chave de sessão do SSW Tool

## Decisão

`SSW_SESSION_SECRET_KEY` é obrigatória e não possui fallback. A aplicação valida
a chave durante a importação de `web.main`, antes de instalar o
`SessionMiddleware` e antes de aceitar requisições.

A chave deve ter ao menos 48 caracteres, diversidade suficiente e estimativa
mínima de 192 bits. Valores conhecidos, periódicos, repetitivos, curtos ou com
espaços nas bordas são recusados. A mensagem de falha é deliberadamente genérica
e nunca contém o valor recebido.

O carregamento usa o caminho absoluto `<raiz-do-projeto>/.env`, sem depender do
`directory` corrente do Supervisor. Variáveis já presentes no ambiente do
processo têm precedência (`override=False`). Portanto, não se deve configurar a
mesma variável simultaneamente no Supervisor e no `.env`.

## Geração e armazenamento

Gerar fora do repositório e de canais compartilhados:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Armazenar o resultado como `SSW_SESSION_SECRET_KEY` no `.env` real da VPS ou em
um secret manager que injete o ambiente do Supervisor. Nunca registrar o valor
em Git, tickets, logs ou saída de diagnóstico. Para arquivo local, restringir
proprietário e modo, por exemplo `chmod 600 .env`.

## Diagnóstico do Supervisor

Não existe configuração do Supervisor versionada neste repositório. Antes da
implantação, identificar no host:

1. nome do programa com `sudo supervisorctl status`;
2. arquivo `[program:...]`, usuário, `directory`, `command` e eventuais entradas
   `environment`;
3. interpretador/virtualenv realmente usado pelo `command`;
4. se `SSW_SESSION_SECRET_KEY` está no ambiente do Supervisor ou apenas no
   `.env` da raiz.

Escolher uma única fonte. A opção mais simples para a arquitetura atual é o
`.env` da raiz com permissão restrita, removendo qualquer definição duplicada de
`environment=`. Se o Supervisor precisar injetar o segredo, lembrar que esse
valor prevalece sobre o `.env`.

O startup registra somente `ambiente` e `origem` da configuração. Ele nunca
registra comprimento, hash, prefixo ou conteúdo da chave.

## Pré-flight antes do restart

Executar como o mesmo usuário e com o mesmo Python configurados no Supervisor:

```bash
cd /caminho/real/ssw_tool
sudo -u USUARIO_DO_SUPERVISOR .venv/bin/python -c "from web.session_config import carregar_configuracao_sessao; c=carregar_configuracao_sessao(); print('sessao valida:', c.environment, c.source)"
```

O comando deve imprimir apenas o ambiente e a origem. Se falhar, não reiniciar.
Também validar antes da janela:

```bash
.venv/bin/python -m pytest -q tests/test_session_config.py
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q web
```

Revisar que as flags OC73 continuam desligadas e que nenhuma configuração do
Supervisor será aplicada por `update` antes da aprovação.

## Implantação futura

1. Fazer backup seguro da configuração anterior sem copiar o segredo para Git.
2. Instalar a nova versão sem reiniciar.
3. Instalar a chave e ajustar propriedade/permissões.
4. Executar o pré-flight no usuário e virtualenv efetivos.
5. Usar `supervisorctl reread` e revisar a saída.
6. Reiniciar somente o programa autorizado durante a janela aprovada.
7. Confirmar no log a mensagem sanitizada de configuração validada.
8. Testar login, navegação autenticada, logout e expiração.
9. Confirmar que scheduler e gate OC73 permanecem bloqueados.

## Rotação e rollback

Trocar a chave invalida todos os cookies anteriores. Além disso, o restart limpa
as sessões SSW mantidas em memória. Todos os usuários precisarão autenticar
novamente; isso é esperado e deve ser comunicado antes da janela.

Se o startup falhar, não repetir reinícios em loop. Corrigir a fonte da chave e
reexecutar o pré-flight. Se for necessário rollback:

1. restaurar a versão anterior e a configuração preservada;
2. validar a configuração antes do restart;
3. reiniciar somente o programa afetado;
4. verificar login e logout;
5. manter o scheduler OC73 desligado e o gate de envio bloqueado.

Mesmo restaurando a chave anterior, considerar as sessões invalidadas, pois o
armazenamento server-side em memória foi perdido no restart.
