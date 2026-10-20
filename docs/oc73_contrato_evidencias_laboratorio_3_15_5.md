# OC73 — Contrato de evidências do laboratório 3.15.5

Nenhum valor abaixo deve ser presumido. O administrador autorizado deve preencher,
assinar/referenciar e limitar temporalmente a evidência antes da janela.

## Checklist administrativo obrigatório

- [ ] identificador inequívoco da instância e responsável;
- [ ] porta local autorizada e PID/processo que a escuta;
- [ ] caminho e hash/origem do executável `mysqld`;
- [ ] datadir e volume físico dedicado;
- [ ] comprovação de que volume, serviço e credenciais não são operacionais;
- [ ] ausência de replicação, upstream/downstream ou binlog operacional;
- [ ] produto, distribuição e versão exatos;
- [ ] `server_uuid`, hostname e identidade SQL esperados;
- [ ] conta experimental completa `user@host` e grants exatos;
- [ ] data, expiração e responsável pela evidência;
- [ ] revisão/hash do verificador, adaptador, DDL e testes;
- [ ] autorização separada para DDL e para testes transacionais.

## Driver

Registrar nome do pacote, versão inventariada, repositório/origem, SHA-256 do
artefato/lockfile, cursor, timeouts, autocommit e flags efetivos. Comprovar que
`CLIENT.MULTI_STATEMENTS` não foi negociado. Divergência bloqueia a janela.

## DDL e privilégios

Administrador separado aplica somente o DDL revisado. A conta transacional recebe
os grants table-specific documentados, sem DDL/DELETE/GRANT OPTION/admin. Guardar
`SHOW CREATE TABLE`, índices, FKs e `SHOW GRANTS` saneados.

## Interrupção

Interromper antes de escrita se identidade SQL ou administrativa divergir, pacote
não corresponder ao hash, grants forem desconhecidos/excessivos, houver tabela
fora da allowlist ou isolamento não puder ser provado. Durante testes, interromper
em carga inesperada, conexão externa, replicação, commit ambíguo ou falha de
limpeza; não repetir callback nem reparar estado manualmente.

## Limpeza

A limpeza requer autorização separada, identidade administrativa, alvo literal
no schema lab e inventário antes/depois. A conta transacional não recebe `DROP` ou
`DELETE`. Preservar evidências necessárias antes da remoção.

## Evidências esperadas

Relatório de identidade, contrato administrativo, grants, DDL efetivo, barreiras
de concorrência, estados agregados, rowcounts, erros saneados, callbacks falsos e
prova de zero rede SSW. MariaDB não vale como prova específica de MySQL 5.7.
