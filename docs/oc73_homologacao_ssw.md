# Homologação supervisionada da OC73 no SSW

Este roteiro prepara uma validação futura. Ele não autoriza POST nesta sprint e
não substitui aprovação operacional formal.

## Pré-condições de segurança

1. Obter autorização explícita, registrada, para um único lançamento.
2. Manter o scheduler desligado durante toda a homologação.
3. Selecionar um CTRC autorizado e informar responsável, janela e unidade.
4. Confirmar acesso ao MySQL de auditoria e capacidade de consultar OP101.
5. Registrar os valores das flags antes do teste. Não modificar o gate por código.
6. Ter um segundo operador acompanhando a execução e as evidências.

O comando manual é independente de `OCORRENCIA_73_ENABLED` e nunca inicia o
scheduler. A flag global do scheduler deve permanecer `false` durante toda a
homologação.

O CTRC é obrigatório e único:

```powershell
python scripts/testar_ocorrencia_73.py --ctrc CWB123456
```

O argumento apenas fixa `OCORRENCIA_73_CTRC_TESTE` e força
`OCORRENCIA_73_MAX_LANCAMENTOS=1` no processo manual. Ele não altera DRY_RUN,
`OC73_ENVIO_HABILITADO` ou qualquer configuração do scheduler. Repetir
`--ctrc`, usar separadores ou informar mais de um CTRC é recusado.

Com os padrões seguros (`OCORRENCIA_73_DRY_RUN=true` e
`OC73_ENVIO_HABILITADO=NAO`), o comando não pode lançar a ocorrência. Uma
homologação potencialmente real exige, além dos gates existentes, autorização
operacional separada representada por `OC73_HOMOLOGACAO_MANUAL_AUTORIZADA=SIM`.
Essa variável não deve ser persistida nem configurada antes da janela aprovada.
Ela é apenas uma trava técnica e não constitui, registra ou substitui a
autorização operacional humana.
Sua presença não contorna DRY_RUN, o gate global, a reserva, o limite, o REF ou a
confirmação OP101.

## Seleção e verificação do CTRC

- Usar um CTRC indicado pelo responsável operacional, nunca escolhido por busca
  indiscriminada.
- Consultar o histórico OP101 antes do teste e confirmar que não existe OC73.
- Registrar série, número, `seq_ctrc`, família, local e horário da consulta em
  evidência de acesso restrito.
- Se houver OC73 anterior, estado inconsistente ou dúvida de elegibilidade,
  reprovar o candidato e não enviar.

## Reserva e observação

1. Criar a execução e a tentativa pelo fluxo normal do `PortaoOC73`.
2. Capturar a referência persistida antes de qualquer envio. O formato obrigatório
   é `OC73-[0-9A-F]{20}`.
3. Construir a observação `BOT OC73 | REF:<referência>`.
4. Confirmar que a observação contém exatamente um `REF:` e no máximo 70
   caracteres.
5. Guardar evidência da tentativa em estado `reservado`, sem expor credenciais.

## Execução supervisionada futura

Somente após autorização específica para o lançamento:

1. Restringir a execução ao CTRC autorizado.
2. Registrar horário, operador, payload saneado e identificador da tentativa.
3. Realizar no máximo um POST.
4. Guardar status HTTP e resposta saneada apenas como evidência de transporte.
   Resposta HTTP positiva não confirma ocorrência registrada.
5. Consultar novamente o histórico OP101 e procurar a referência completa e
   idêntica no complemento da OC73.
6. Marcar `confirmado` somente após essa correspondência no histórico.

## Timeout, ambiguidade ou falha de consulta

- Marcar a tentativa como `incerto` quando não for possível determinar o efeito
  do POST.
- Nunca retornar a tentativa para `reservado` e nunca repetir o POST
  automaticamente.
- Se a consulta do histórico falhar, manter o estado conservador.
- A reconciliação deve apenas consultar o histórico e confirmar uma referência
  idêntica. Ausência de correspondência não autoriza reenvio.

## Evidências mínimas

- Autorização e CTRC escolhido.
- Histórico OP101 anterior sem OC73.
- ID da execução e da tentativa.
- Referência persistida e observação final, com contagem de caracteres.
- Um único POST, com horário e resposta saneada.
- Histórico OP101 posterior exibindo integralmente a mesma referência.
- Estado final persistido e registro de qualquer incerteza.

## Aprovação

Aprovar somente se houver um único POST, a referência persistida aparecer inteira
e idêntica no histórico OP101, e os estados MySQL refletirem a confirmação.

Reprovar se a referência for truncada ou alterada, houver mais de um POST, a
confirmação depender apenas do HTTP, ocorrer reenvio após incerteza ou qualquer
gate/estado precisar ser contornado.
