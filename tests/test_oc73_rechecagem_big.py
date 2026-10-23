"""Testes determinísticos: sem SSW real, MySQL real ou espera de relógio."""
import json
import os
import threading
import unittest
from contextlib import contextmanager
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from modules.ocorrencia_73 import service
from modules.ocorrencia_73.parser import filtrar_registros
from modules.ocorrencia_73.rechecagem_big import RechecagemBIG
from modules.ocorrencia_73.rechecagem_politica import (
    TIMEZONE, janela_rechecagem, proxima_rechecagem,
)


def dt(texto):
    return datetime.fromisoformat(texto).replace(tzinfo=TIMEZONE)


def item():
    return dict(serie="JOI", numero="123", fluxo="BIG", uf_expedidor="SC",
                ultima_ocorrencia="77", data_inicial_pesquisa="2026-10-22",
                data_final_pesquisa="2026-10-22")


class RepoMemoria:
    def __init__(self):
        self.rows = {}
        self.tentativas = {}
        self.envios = []
        self.finalizados = []
        self.lock = threading.Lock()
        self.initial_attempts = 0
        self.reads = 0
        self.posse = True
        self.falhar_finalizacao = False

    @contextmanager
    def trava_rechecagem_big(self, execucao_id):
        acquired = self.lock.acquire(False)
        try:
            yield lambda: acquired and self.posse
        finally:
            if acquired:
                self.lock.release()

    def registrar_rechecagem_big(self, **kw):
        if self.rows:
            return 1
        self.rows[1] = dict(
            id=1, execucao_id=kw['execucao_id'], data_referencia=kw['data_referencia'],
            serie=kw['item']['serie'], numero=kw['item']['numero'],
            item_json=json.dumps(kw['item']), status='aguardando',
            limite_envios=kw['limite_envios'], proxima_consulta_em=kw['proxima_consulta_em'],
            limite_em=kw['limite_em'], execucao_envio_id=None,
        )
        return 1

    def listar_rechecagens_big(self, agora):
        self.reads += 1
        return [dict(r) for r in self.rows.values()
                if r['status'] == 'aguardando' and r['proxima_consulta_em'] <= agora]

    def buscar_rechecagem_big(self, identificador):
        return dict(self.rows[identificador])

    def atualizar_rechecagem_big(self, identificador, **kw):
        row = self.rows[identificador]
        row['status'] = kw['status']
        row['detalhe'] = kw['detalhe']
        if kw['proxima'] is not None:
            row['proxima_consulta_em'] = kw['proxima']

    def buscar_tentativa(self, serie, numero):
        return self.tentativas.get((serie, numero))

    def contar_tentativas_rechecagem_big(self, execucao_id):
        return self.initial_attempts + len(self.tentativas)

    def iniciar_envio_rechecagem_big(self, pendencia):
        self.rows[pendencia['id']]['execucao_envio_id'] = 99
        return 99

    def reservar_ctrc(self, **kw):
        key = (kw['serie'], kw['numero'])
        if key in self.tentativas:
            return dict(self.tentativas[key], reserva_criada=False)
        row = dict(id=1, status='reservado', execucao_id=kw['execucao_id'],
                   referencia='OC73-' + 'A' * 20)
        self.tentativas[key] = row
        return dict(row, reserva_criada=True)

    def iniciar_envio(self, tentativa_id, execucao_id):
        row = self.tentativas[('JOI', '123')]
        row['status'] = 'envio_iniciado'
        return True

    def confirmar_lancamento(self, **kw):
        self.tentativas[('JOI', '123')]['status'] = 'confirmado'

    def marcar_incerto(self, **kw):
        self.tentativas[('JOI', '123')]['status'] = 'incerto'

    def finalizar_execucao(self, **kw):
        if self.falhar_finalizacao:
            raise RuntimeError('falha simulada apos confirmacao')
        self.finalizados.append(kw)


class OP101Fake:
    def __init__(self):
        self.codigo = '77'
        self.historia = None
        self.envios = []
        self.consultas = []
        self.fail_send = False
        self.confirmar = True
        self.ao_consultar = lambda: None

    def consultar_ctrc(self, **kw):
        self.consultas.append(kw)
        self.ao_consultar()
        return SimpleNamespace(encontrado=True, seq_ctrc='1234', local='Q', familia='ROD',
                               to_dict=lambda: {'encontrado': True})

    def abrir_ocorrencias(self, **kw):
        return 'fixture'

    def listar_ocorrencias(self, html, estrito=False):
        if not estrito:
            raise AssertionError('Parser estrito obrigatorio')
        if self.historia is not None:
            return self.historia
        return [SimpleNamespace(codigo=self.codigo, data_hora='23/10/26 00:16',
                                to_dict=lambda: {'codigo': self.codigo})]

    def encontrar_ocorrencia(self, historico, codigo):
        return next((o for o in historico if o.codigo == str(codigo)), None)

    def lancar_ocorrencia_73(self, **kw):
        self.envios.append(kw)
        if self.fail_send:
            raise TimeoutError('Resposta perdida')
        return SimpleNamespace(mensagem='enviado')

    def confirmar_ocorrencia_73(self, **kw):
        if not self.confirmar:
            return None
        return dict(codigo='73', complemento='BOT OC73 | REF:' + kw['referencia'])


class RechecagemTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'OC73_RECHECAGEM_BIG_ENABLED': 'SIM'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.repo = RepoMemoria()
        self.op = OP101Fake()
        self.now = dt('2026-10-22T23:30:00')
        self.client = SimpleNamespace(session=SimpleNamespace(close=lambda: None))
        self.worker = RechecagemBIG(
            self.repo, client_factory=lambda: self.client,
            op101_factory=lambda c: self.op, clock=lambda: self.now,
        )

    def registrar(self, **overrides):
        kwargs = dict(item=item(), execucao_id=8, data_referencia=date(2026,10,22),
                      limite_envios=3, dry_run=False, envio_habilitado=True)
        kwargs.update(overrides)
        return self.worker.registrar(**kwargs)

    def rodar(self, **overrides):
        kwargs = dict(dry_run=False, envio_habilitado=True, max_lancamentos=3)
        kwargs.update(overrides)
        return self.worker.executar_pendentes(**kwargs)

    def test_registra_proxima_10min(self):
        self.assertEqual(self.registrar(), 'aguardando_oc64')
        self.assertEqual(self.repo.rows[1]['proxima_consulta_em'], datetime(2026,10,22,23,40))

    def test_flag_desligada_nao_registra(self):
        os.environ['OC73_RECHECAGEM_BIG_ENABLED'] = 'NAO'
        self.registrar()
        self.assertFalse(self.repo.rows)
        self.assertEqual(self.rodar(), [])
        self.assertEqual(self.repo.reads, 0)

    def test_dry_run_sem_mysql(self):
        self.assertEqual(self.registrar(dry_run=True), 'aguardando_oc64_simulacao')
        self.assertFalse(self.repo.rows)
        self.rodar(dry_run=True)
        self.assertEqual(self.repo.reads, 0)

    def test_envio_desabilitado_nao_registra(self):
        self.assertEqual(self.registrar(envio_habilitado=False), 'bloqueado_seguranca')
        self.assertFalse(self.repo.rows)

    def test_manual_ctrc_restrito(self):
        self.assertEqual(self.registrar(ctrc_teste='JOI999'), 'ignorado_fora_teste')
        self.assertFalse(self.repo.rows)

    def test_fora_da_janela(self):
        self.now = dt('2026-10-22T20:00:00')
        self.assertEqual(self.registrar(), 'rechecagem_fora_janela')

    def test_uf_errada_nao_entra(self):
        self.assertEqual(self.registrar(item=dict(item(), uf_expedidor='PR')),
                         'bloqueado_ultima_ocorrencia')
        self.assertFalse(self.repo.rows)

    def test_limite_zero_nao_registra(self):
        self.assertEqual(self.registrar(limite_envios=0), 'ignorado_limite')

    def test_77_reagenda_sem_enviar(self):
        self.registrar()
        self.now = dt('2026-10-22T23:40:00')
        self.assertEqual(self.rodar()[0]['status'], 'aguardando')
        self.assertEqual(self.repo.rows[1]['proxima_consulta_em'], datetime(2026,10,22,23,50))
        self.assertFalse(self.op.envios)

    def test_nao_consulta_antes_da_hora(self):
        self.registrar()
        self.now = dt('2026-10-22T23:35:00')
        self.assertEqual(self.rodar(), [])
        self.assertFalse(self.op.consultas)

    def test_quinta_continua_sexta_data_atual_no_envio(self):
        self.registrar()
        self.now = dt('2026-10-23T00:20:00')
        self.op.codigo = '64'
        self.assertEqual(self.rodar()[0]['status'], 'confirmado')
        envio = self.op.envios[0]
        self.assertEqual(envio['data_ocorrencia'], date(2026,10,23))
        self.assertEqual(envio['hora_ocorrencia'], '0020')
        self.assertEqual(self.op.consultas[0]['data_inicial'], date(2026,10,22))
        self.assertEqual(self.op.consultas[0]['data_final'], date(2026,10,23))

    def test_64_na_ultima_rodada(self):
        self.registrar()
        self.now = dt('2026-10-23T00:30:05')
        self.op.codigo = '64'
        self.assertEqual(self.rodar()[0]['status'], 'confirmado')

    def test_77_na_ultima_rodada_expira(self):
        self.registrar()
        self.now = dt('2026-10-23T00:30:05')
        self.assertEqual(self.rodar()[0]['status'], 'expirado')
        self.assertFalse(self.op.envios)
        self.assertEqual(self.rodar(), [])

    def test_depois_0030_nao_consulta_ssw(self):
        self.registrar()
        self.now = dt('2026-10-23T00:31:00')
        self.assertEqual(self.rodar()[0]['status'], 'expirado')
        self.assertFalse(self.op.consultas)

    def test_consulta_ultrapassa_prazo_sem_post(self):
        self.registrar()
        self.now = dt('2026-10-23T00:30:05')
        self.op.codigo = '64'
        self.op.ao_consultar = lambda: setattr(self, 'now', dt('2026-10-23T00:31:00'))
        self.assertEqual(self.rodar()[0]['status'], 'expirado')
        self.assertFalse(self.op.envios)

    def test_oc73_existente_encerra(self):
        self.registrar()
        self.now = dt('2026-10-23T00:20:00')
        self.op.codigo = '73'
        self.assertEqual(self.rodar()[0]['status'], 'ja_existia')
        self.assertFalse(self.op.envios)

    def test_oc63_encerra_sem_lancar(self):
        self.registrar()
        self.now = dt('2026-10-23T00:20:00')
        self.op.codigo = '63'
        self.assertEqual(self.rodar()[0]['status'], 'bloqueado_ocorrencia')
        self.assertFalse(self.op.envios)

    def test_historico_invalido_bloqueia(self):
        self.registrar()
        self.now = dt('2026-10-23T00:20:00')
        self.op.historia = [SimpleNamespace(codigo='64', data_hora='invalida')]
        self.assertEqual(self.rodar()[0]['status'], 'bloqueado_historico')
        self.assertFalse(self.op.envios)

    def test_reserva_incerta_nao_reenvia(self):
        self.registrar()
        self.repo.tentativas[('JOI','123')] = dict(id=7,status='envio_iniciado')
        self.now = dt('2026-10-23T00:20:00')
        self.assertEqual(self.rodar()[0]['status'], 'incerto')
        self.assertFalse(self.op.consultas)
        self.assertFalse(self.op.envios)

    def test_timeout_envio_nao_rearma(self):
        self.registrar()
        self.now = dt('2026-10-23T00:20:00')
        self.op.codigo = '64'
        self.op.fail_send = True
        self.assertEqual(self.rodar()[0]['status'], 'incerto')
        self.assertEqual(self.rodar(), [])
        self.assertEqual(len(self.op.envios), 1)

    def test_confirmacao_ausente_incerto(self):
        self.registrar()
        self.now = dt('2026-10-23T00:20:00')
        self.op.codigo = '64'
        self.op.confirmar = False
        self.assertEqual(self.rodar()[0]['status'], 'incerto')

    def test_limite_acumulado_original(self):
        self.registrar(limite_envios=1)
        self.repo.initial_attempts = 1
        self.now = dt('2026-10-23T00:20:00')
        self.assertEqual(self.rodar()[0]['status'], 'ignorado_limite')
        self.assertFalse(self.op.envios)

    def test_novo_limite_nao_amplia_persistido(self):
        self.registrar(limite_envios=1)
        self.repo.initial_attempts = 1
        self.now = dt('2026-10-23T00:20:00')
        self.assertEqual(self.rodar(max_lancamentos=99)[0]['status'], 'ignorado_limite')

    def test_retoma_com_nova_instancia(self):
        self.registrar()
        self.now = dt('2026-10-23T00:20:00')
        self.op.codigo = '64'
        self.worker = RechecagemBIG(self.repo, client_factory=lambda: self.client,
                                   op101_factory=lambda c: self.op, clock=lambda: self.now)
        self.assertEqual(self.rodar()[0]['status'], 'confirmado')
        self.assertEqual(self.rodar(), [])
        self.assertEqual(len(self.op.envios), 1)

    def test_outro_worker_com_trava_ocupada_nao_consulta(self):
        self.registrar()
        self.now = dt('2026-10-23T00:20:00')
        self.repo.lock.acquire()
        try:
            self.assertEqual(self.rodar(), [])
            self.assertFalse(self.op.consultas)
        finally:
            self.repo.lock.release()

    def test_falha_apos_confirmacao_nao_reenvia(self):
        self.registrar()
        self.now = dt('2026-10-23T00:20:00')
        self.op.codigo = '64'
        self.repo.falhar_finalizacao = True
        self.assertEqual(self.rodar()[0]['status'], 'incerto')
        self.assertEqual(self.rodar(), [])
        self.assertEqual(len(self.op.envios), 1)


class IntegracaoServiceTests(unittest.TestCase):
    setUp = RechecagemTests.setUp
    # Compartilha somente os fixtures; não repete os testes do worker.
    def executar_inicial(self, *, dry_run, habilitada=True):
        import io
        from contextlib import redirect_stdout
        from pathlib import Path
        self.now = dt('2026-10-22T23:30:00')
        self.op.historia = [SimpleNamespace(
            codigo='77', data_hora='22/10/26 23:00', to_dict=lambda: {'codigo':'77'},
        )]
        svc = object.__new__(service.Ocorrencia73Service)
        svc.repository = self.repo
        svc.montar_diretorios_execucao = lambda _: {
            'base':Path('/unused'), 'original':Path('/unused'), 'auditoria':Path('/unused'),
        }
        svc.criar_client_logado = lambda: self.client
        svc.preparar_lotes_oc73 = lambda **kw: {
            'filtrados':[item()], 'lotes':[], 'total_relatorio':1,
        }
        svc.gerar_auditoria_filtrados = lambda **kw: None
        svc.salvar_resultado_json = lambda **kw: Path('/unused/resultado.json')
        with patch.object(service, 'DRY_RUN', dry_run), \
             patch.object(service, 'OC73_ENVIO_HABILITADO', True), \
             patch.object(service, 'MAX_LANCAMENTOS', 3), \
             patch.object(service, 'CTRC_TESTE', ''), \
             patch.object(service, 'OP455Report', lambda client: object()), \
             patch.object(service, 'OP101Ocorrencias', lambda client: self.op), \
             patch('modules.ocorrencia_73.rechecagem_big.RechecagemBIG', lambda repo: self.worker), \
             patch.dict(os.environ, {'OC73_RECHECAGEM_BIG_ENABLED':'SIM' if habilitada else 'NAO'}), \
             redirect_stdout(io.StringIO()):
            return svc._executar_interno(date(2026,10,22), 'scheduler:BIG', 8, 'BIG')

    def test_inicial_77_persiste_espera_sem_post(self):
        resultado = self.executar_inicial(dry_run=False)
        self.assertEqual(resultado['itens'][0]['status'], 'aguardando_oc64')
        self.assertEqual(resultado['total_aguardando_oc64'], 1)
        self.assertEqual(len(self.repo.rows), 1)
        self.assertFalse(self.op.envios)

    def test_inicial_77_dry_run_sem_fila(self):
        resultado = self.executar_inicial(dry_run=True)
        self.assertEqual(resultado['itens'][0]['status'], 'aguardando_oc64_simulacao')
        self.assertFalse(self.repo.rows)
        self.assertFalse(self.op.envios)

    def test_inicial_flag_desligada_preserva_bloqueio(self):
        resultado = self.executar_inicial(dry_run=True, habilitada=False)
        self.assertEqual(resultado['itens'][0]['status'], 'bloqueado_ultima_ocorrencia')
        self.assertFalse(self.repo.rows)


class PoliticaTests(unittest.TestCase):
    def test_quinta_janela_acaba_sexta(self):
        inicio, limite = janela_rechecagem(date(2026,10,22))
        self.assertEqual(inicio, dt('2026-10-22T23:30:00'))
        self.assertEqual(limite, dt('2026-10-23T00:30:59'))

    def test_sexta_nao_abre_janela_nova(self):
        with self.assertRaises(ValueError):
            janela_rechecagem(date(2026,10,23))

    def test_reagenda_ultima_rodada_0030(self):
        _, limite = janela_rechecagem(date(2026,10,22))
        self.assertEqual(proxima_rechecagem(dt('2026-10-23T00:26:00'), limite),
                         dt('2026-10-23T00:30:00'))

    def test_slots_nao_acumulam_atraso_da_consulta(self):
        _, limite = janela_rechecagem(date(2026,10,22))
        self.assertEqual(proxima_rechecagem(dt('2026-10-22T23:41:17'), limite),
                         dt('2026-10-22T23:50:00'))

    def test_sem_nova_rodada_depois_0030(self):
        _, limite = janela_rechecagem(date(2026,10,22))
        self.assertIsNone(proxima_rechecagem(dt('2026-10-23T00:30:00'), limite))

    def test_parser_77_apenas_opt_in_e_sc(self):
        registro = {'Serie/Numero CTRC':'JOI123-4','UF do Expedidor':'SC',
                    'Codigo Ultima Ocorrencia':'77'}
        self.assertEqual(filtrar_registros([registro],fluxo='BIG'), [])
        candidatos = filtrar_registros([registro],fluxo='BIG', incluir_espera_big=True)
        self.assertEqual(candidatos[0]['regra_filtro'], 'big_sc_aguardando_oc64')
        registro['UF do Expedidor'] = 'PR'
        self.assertEqual(filtrar_registros([registro],fluxo='BIG', incluir_espera_big=True), [])

    def test_parser_outra_ocorrencia_nao_entra(self):
        registro = {'Serie/Numero CTRC':'JOI123-4','UF do Expedidor':'SC',
                    'Codigo Ultima Ocorrencia':'62'}
        self.assertEqual(filtrar_registros([registro],fluxo='BIG', incluir_espera_big=True), [])


if __name__ == '__main__':
    unittest.main()
