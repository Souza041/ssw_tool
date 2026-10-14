-- SOMENTE laboratorio isolado oc73_concorrencia_test, porta 3308.
-- Conferir SELECT DATABASE(), @@port, @@datadir antes de executar.
-- Não executar em produção.
ALTER TABLE oc73_tentativas
  ADD COLUMN referencia VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  ADD UNIQUE KEY uq_oc73_referencia (referencia);
-- Registros anteriores ficam NULL e bloqueados para envio/reconciliação automática.
