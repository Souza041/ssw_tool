# OC73 — pré-voo isolado da OP455 CWB

Este procedimento valida somente a navegação até a tela OP455 da unidade CWB.
Ele não gera relatório, não consulta OP156, não baixa arquivo e não acessa OP101,
MySQL, serviço OC73, scheduler ou aplicação web.

## Pré-condições

- autorização específica para uma única execução;
- revisão do commit exato;
- usuário SSW autorizado informado em
  `OC73_PRE_VOO_SSW_USUARIO_AUTORIZADO`, sem incluí-lo na linha de comando;
- `.env` ou ambiente efetivo com os quatro valores abaixo:
  - `OCORRENCIA_73_ENABLED=false`;
  - `OCORRENCIA_73_DRY_RUN=true`;
  - `OCORRENCIA_73_MAX_LANCAMENTOS=0`;
  - `OC73_ENVIO_HABILITADO=NAO`.

A CLI falha antes de criar o cliente se qualquer flag divergir. O usuário é
comparado exatamente antes do login. Nenhum desses valores habilita envio.

## Matriz fechada de transporte

| Ordem | Método | Endpoint | Ação/payload permitido |
|---:|---|---|---|
| 1 | POST | `/bin/ssw0422` | login, `act=L`, campos de credencial já configurados e `f6=TRUE` |
| 2 | POST | `/bin/menu01` | abrir menu, somente `act` vazio |
| 3 | GET | `/bin/ssw0082` | somente `quadro_menu01=CWB` e `dummy` |
| 4 | POST | `/bin/menu01` | somente `act=TRO`, `f2=CWB`, `f3=455` e `dummy` |
| 5 | POST | `/bin/ssw0230` | somente `sequencia=455` e `dummy` |

Cada etapa usa `retries=1`. Ordem, método, endpoint, chaves extras ou valores
divergentes bloqueiam a execução. Depois da quinta chamada, qualquer requisição
adicional é rejeitada. `/bin/ssw0122`, `/bin/ssw1440`, `/bin/ssw0424`, `act=E1`,
`act=II3` e lançamento do código 73 são proibidos explicitamente.

O método `pre_voo_navegacao_ocorrencia_73_cwb()` deixa o contexto interno de
geração não confirmado mesmo quando a tela é válida. Portanto, a mesma instância
de `OP455Report` não pode prosseguir para o submit de relatório.

## Saída

A saída JSON contém apenas classificação, motivo controlado, unidade fixa,
status HTTP, URL sem usuário/query, Content-Type, tamanho, SHA-256, sequência de
endpoints e o contrato sanitizado do formulário `/bin/ssw0230`. Para cada controle,
são apresentados apenas nome e tipo. Valores e opções aparecem exclusivamente
para `act`, `f2`, `f3` e `reg_tipo`. Campos ocultos têm seus nomes inventariados,
mas apenas códigos curtos compatíveis com esses campos são publicados. Valores
não classificados e textos de opções são omitidos. Tokens e demais valores não
são expostos. HTML, cookies, headers de
autenticação, credenciais e usuário não são incluídos.

Exemplo reduzido de saída:

```json
{
  "resultado": "contexto_confirmado",
  "chamadas_http": 5,
  "contrato_formulario": {
    "status": "extraido",
    "metodo": "post",
    "action": "/bin/ssw0230",
    "campos_ocultos": ["act", "token_interno"],
    "controles": [
      {"nome": "act", "tipo": "hidden", "valor": ""},
      {"nome": "f2", "tipo": "input", "valor": "CWB"},
      {"nome": "f3", "tipo": "input", "valor": "A"},
      {"nome": "token_interno", "tipo": "hidden"}
    ],
    "conclusao_payload_submit": "nao_inferida"
  }
}
```

A captura descreve somente o estado inicial do HTML. Ela não presume que uma
opção apresentada seja o valor exigido no submit de geração.

O código de saída é `0` somente para contexto confirmado, `2` para tela não
confirmada e `3` para bloqueio, configuração, usuário ou falha de navegação.

## Comando futuro — NÃO EXECUTADO

Definir `OC73_PRE_VOO_SSW_USUARIO_AUTORIZADO` por mecanismo seguro no processo e,
após autorização específica, executar uma única vez:

```powershell
python -B -m scripts.pre_voo_op455_cwb --autorizar-pre-voo-cwb
```

Não repetir automaticamente em caso de erro, bloqueio ou resultado inconclusivo.
