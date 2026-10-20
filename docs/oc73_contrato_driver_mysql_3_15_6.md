# OC73 — Contrato futuro do driver MySQL — Sprint 3.15.6

Contrato exclusivamente **offline**. Não executa importação dinâmica, instalação,
conexão ou inspeção do ambiente. A versão real permanece pendente de inventário.

## Inventário obrigatório

| Item | Evidência futura | Regra |
|---|---|---|
| Pacote | nome canônico | exatamente `PyMySQL` |
| Versão | metadado do ambiente aprovado | versão explícita e revisada; não presumir |
| Origem | índice/repositório e artefato | somente origem corporativa/autorizada |
| Integridade SHA-256 | hash do artefato ou lockfile verificável | 64 caracteres hexadecimais minúsculos |
| Local carregado | caminho do módulo | deve corresponder ao ambiente inventariado |
| Compatibilidade | matriz do pacote e ensaio no laboratório | MySQL Community 5.7.32; Pendente |

Divergência de versão, origem, hash ou caminho é NO-GO; não existe fallback para
outra instalação disponível no host.

## Parâmetros permitidos

- TCP literal, sem socket e sem fallback para `localhost`;
- `autocommit=False`;
- `cursorclass=DictCursor`;
- `charset=utf8mb4`;
- `connect_timeout` entre 1 e 10 segundos;
- `read_timeout` e `write_timeout` entre 1 e 30 segundos;
- `client_flag=0` na chamada experimental;
- nenhum kwarg adicional ou fornecido por entrada não confiável.

O valor solicitado de `client_flag` não comprova sozinho os flags negociados. A
execução futura deve registrar evidência compatível com a API da versão
inventariada e demonstrar ausência de `CLIENT.MULTI_STATEMENTS`. Se isso não for
observável de forma confiável, o resultado é inconclusivo e a janela para.

## Ciclo de conexão

1. validar configuração, credencial e contrato antes de chamar o driver;
2. construir a fábrica sem conexão e capturar valores imutáveis;
3. abrir conexão somente dentro da janela autorizada;
4. criar cursores explicitamente e fechá-los em `finally`;
5. executar transações com commit apenas em resultado inequívoco;
6. em exceção, tentar rollback defensivo sem ocultar o erro original;
7. executar `close` explicitamente em `finally`;
8. tratar falha de rollback/close como evidência e NO-GO, nunca como autorização de retry.

Fechamento do cliente não resolve commit ambíguo. Perda de confirmação após o
commit exige estado indeterminado, zero callback e decisão humana.

## Tratamento de falhas e segredos

- mensagens externas são fixas e saneadas;
- exceções do driver não são encadeadas quando puderem conter endpoint ou senha;
- logs não exibem credenciais, DSN, kwargs completos, cookies ou tokens;
- objetos de credencial usam representação protegida;
- timeouts, desconexão, deadlock e erro de integridade são classificados sem
  liberar repetição automática;
- falha de import/capability encerra antes de conectar.

## Evidência mínima de aprovação futura

Registrar sem credenciais: versão/origem/SHA-256, caminho carregado, capabilities,
kwargs não sensíveis, tipo do cursor, `autocommit`, timeouts, flags, abertura e
fechamento. Driver falso comprova apenas o contrato Python; compatibilidade,
negociação de flags, rollback e `close` reais continuam pendentes do laboratório.
