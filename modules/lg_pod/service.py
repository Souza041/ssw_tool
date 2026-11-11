"""Cada intenção de escrita é persistida ANTES da chamada ao portal."""
import logging
import threading
import uuid
from .config import validate_period, Settings
from .repository import Repository
from .db_worker import error_code, safe_error_message
from .portal import Portal
from .parser import eligibility, invoice_from_filename, Row

logger = logging.getLogger(__name__)
# Etapas anteriores a qualquer envio permitem nova consulta; demais são conservadoras.
UNCERTAIN = {'MAPEAMENTO_SOLICITADO', 'ENVIO_SOLICITADO', 'CONFERIR'}


def failure_detail(exc, settings):
    text = str(exc)
    for secret in (getattr(settings, 'password', ''), getattr(settings, 'proxy_password', '')):
        if secret:
            text = text.replace(secret, '[redigido]')
    if '<html' in text.lower() or '<?xml' in text.lower():
        text = 'Resposta do portal inválida; conteúdo omitido.'
    return type(exc).__name__ + ': ' + safe_error_message(RuntimeError(text))


def mutation_failure(repo, portal, run_id, settings, current, nf, invoice, exc, *, approval=False):
    before_post = getattr(portal, 'mutation_attempted', None) is False
    operation = 'Envio' if approval else 'Mapeamento'
    if before_post:
        stage = 'MAPEADO' if approval else 'ERRO_CONSULTA'
        message = operation + ' não iniciado: falha antes do POST de alteração. '
    else:
        stage = 'CONFERIR'
        message = operation + ' sem confirmação. Conferir no portal antes de repetir. '
    message += 'Causa: ' + failure_detail(exc, settings)
    # Registrar a causa antes de tentar gravar; falha no banco não pode escondê-la.
    logger.error('LG POD %s POD %s NF %s: %s', run_id, current.pod_id, nf, message)
    try:
        repo.item(run_id, settings, current, nf, stage, message, invoice)
    except Exception as journal_exc:
        logger.error('LG POD: falha ao registrar item: %s', failure_detail(journal_exc, settings))
        raise RuntimeError(message + ' Não foi possível atualizar o item no banco.') from exc
    raise RuntimeError(message) from exc


def matching_mapped(pod, nf, carrier):
    mapped = pod.values.get('NOTAS_MAPEADAS', '')
    return (pod.status == 'MAPPED' and pod.values.get('POD_STATUS') == '2'
            and pod.values.get('P_CARR_CD') == carrier
            and mapped.strip() == nf)


def process(repo, portal, run_id, settings, start, end, dry_run, progress=lambda message: None):
    progress('Consultando comprovantes e carregando páginas.')
    rows = portal.search(start, end)
    progress(f'{len(rows)} comprovantes encontrados; conferindo histórico.')
    problems = False
    visible_ids = {row.pod_id for row in rows}
    # Uma escrita incerta continua visível mesmo se o POD saiu da consulta.
    for old in repo.unresolved(settings.user, settings.carrier, run_id):
        if old['pod_id'] in visible_ids:
            continue
        missing = Row('pod', {'ID_POD': old['pod_id'], 'IMAGE_NAME': old['arquivo'],
                              'STATUS': 'A_CONFERIR'})
        repo.item(run_id, settings, missing, old['nf'],
                  'MAPEADO' if old['etapa'] == 'MAPEADO' else 'CONFERIR',
                  'POD de execução anterior fora desta consulta. Inclua o período original para conferir ou retomar.',
                  Row('invoice', {'KEY_CODE': old['chave_nota'], 'LD_NF_SERIE': old['serie']}))
        problems = True
    for position, original in enumerate(rows, 1):
        nf, reason = eligibility(original, settings.carrier)
        previous = repo.previous(settings.user, settings.carrier, original.pod_id, run_id)
        resume = (previous and previous['etapa'] == 'MAPEADO'
                  and original.filename == previous['arquivo']
                  and matching_mapped(original, previous['nf'], settings.carrier))
        # Um resultado incerto não desaparece do relatório só porque o status mudou.
        if previous and previous['etapa'] in UNCERTAIN:
            repo.item(run_id, settings, original, previous['nf'], 'CONFERIR',
                      'Tentativa anterior sem confirmação conclusiva. Conferir no portal antes de repetir.')
            problems = True
            continue
        if previous and previous['etapa'] == 'ENVIADO':
            repo.item(run_id, settings, original, previous['nf'], 'IGNORADO',
                      'Já enviado para aprovação em uma execução anterior.')
            continue
        if previous and previous['etapa'] == 'MAPEADO' and not resume:
            repo.item(run_id, settings, original, previous['nf'], 'CONFERIR',
                      'Mapeamento anterior diverge do estado atual. Conferir antes de realizar outra ação.')
            problems = True
            continue
        if not nf and not resume:
            repo.item(run_id, settings, original, '', 'IGNORADO', reason)
            continue
        if resume:
            nf = previous['nf']
        if not dry_run:
            # Revalidação imediatamente antes de cada mutação; não usar posição da linha.
            current = portal.refresh_pod(original.pod_id)
        else:
            current = original
        if not current or current.filename != original.filename:
            repo.item(run_id, settings, original, nf, 'ERRO_CONSULTA',
                      'Comprovante ausente ou alterado desde a consulta inicial.')
            problems = True
            continue
        if resume:
            if not matching_mapped(current, nf, settings.carrier):
                repo.item(run_id, settings, current, nf, 'CONFERIR', 'Mapeamento anterior não coincide com o portal.')
                problems = True
                continue
            invoice = Row('invoice', {'KEY_CODE': previous['chave_nota'], 'LD_NF_NO': nf,
                                      'LD_NF_SERIE': previous['serie']})
            if dry_run:
                repo.item(run_id, settings, current, nf, 'SIMULADO',
                          'Retomada possível: mapeamento confirmado, falta enviar para aprovação.', invoice)
                continue
        else:
            fresh_nf, fresh_reason = eligibility(current, settings.carrier)
            if fresh_nf != nf:
                repo.item(run_id, settings, current, nf, 'IGNORADO', fresh_reason or 'Identidade da NF alterada.')
                continue
            progress(f'Consultando NF {nf} — comprovante {position}/{len(rows)}.')
            repo.item(run_id, settings, current, nf, 'CONSULTANDO', 'Consultando a identidade da NF no portal.')
            try:
                invoice = portal.find_invoice(nf)
            except ValueError as exc:
                repo.item(run_id, settings, current, nf, 'ERRO_CONSULTA', str(exc))
                problems = True
                continue
            if dry_run:
                repo.item(run_id, settings, current, nf, 'SIMULADO',
                          'NF única encontrada. Nenhum mapeamento ou envio realizado.', invoice)
                continue
            repo.item(run_id, settings, current, nf, 'MAPEAMENTO_SOLICITADO',
                      'Intenção registrada antes de clicar em Mapear.', invoice)
            repo.assert_lock()
            try:
                message = portal.map(current, invoice)
            except Exception as exc:
                mutation_failure(repo, portal, run_id, settings, current, nf, invoice, exc)
            repo.item(run_id, settings, current, nf, 'MAPEADO', message, invoice)
            # Se a consulta falhar aqui, MAPEADO permanece gravado e permite retomada.
            current = portal.refresh_pod(original.pod_id)
            if not current or not matching_mapped(current, nf, settings.carrier):
                repo.item(run_id, settings, original, nf, 'CONFERIR',
                          'Portal retornou sucesso, mas o vínculo MAPPED com a NF não pôde ser confirmado.', invoice)
                problems = True
                continue
        repo.item(run_id, settings, current, nf, 'ENVIO_SOLICITADO',
                  'Intenção registrada antes de enviar para aprovação.', invoice)
        repo.assert_lock()
        try:
            message = portal.approve(current)
        except Exception as exc:
            mutation_failure(repo, portal, run_id, settings, current, nf, invoice, exc, approval=True)
        repo.item(run_id, settings, current, nf, 'ENVIADO', message + ' — enviado para aprovação LG.', invoice)
    return problems


def execute(run_id, settings, start, end, dry_run, repo_factory=Repository, portal_factory=Portal):
    try:
        with repo_factory(account=(settings.user, settings.carrier)) as repo:
            if not repo.lock():
                repo.run_status(run_id, 'BLOQUEADA', f'Já existe uma execução LG POD em andamento para {settings.operation}.', True)
                return
            repo.interrupt_old(run_id)
            settings.validate(real=not dry_run)
            validate_period(start, end)
            last_progress = ''

            def progress(message):
                nonlocal last_progress
                last_progress = message
                logger.info('LG POD [%s] %s: %s', getattr(settings, 'operation', 'SC'), run_id[:8], message)
                repo.run_status(run_id, 'EXECUTANDO', message)

            def retry_progress(message):
                # Preservar NF, posição e total conhecidos durante a espera.
                repo.run_status(run_id, 'EXECUTANDO', last_progress + ' | ' + message)

            progress('Iniciando cliente HTTP requests.')
            with portal_factory(settings) as portal:
                portal.on_retry = retry_progress
                progress('Autenticando no portal LG.')
                portal.login()
                problems = process(repo, portal, run_id, settings, start, end, dry_run, progress)
                progress('Consultas finalizadas; encerrando sessão HTTP.')
            repo.run_status(run_id, 'COM_PENDENCIAS' if problems else 'CONCLUIDA',
                            'Simulação sem alterações no portal.' if dry_run else 'Processamento finalizado.', True)
    except Exception as exc:
        code = error_code(exc)
        logger.error('LG POD: execução %s interrompida (%s, codigo=%s). Causa: %s',
                     run_id, type(exc).__name__, code, failure_detail(exc, settings))
        if code is not None:
            safe = f'Falha MySQL {code} ({type(exc).__name__}). Execução interrompida sem repetição automática.'
        else:
            safe = str(exc) if isinstance(exc, (ValueError, RuntimeError)) and not any(
                token in str(exc) for token in [settings.password, 'http', '<html'] if token
            ) else 'Falha de conexão ou do portal. Consulte as etapas registradas e verifique o ambiente.'
        # A conexão de execução foi encerrada. Uma conexão NOVA serve somente
        # para atualizar o estado deste run_id; não retoma o lote nem pega lock.
        try:
            with repo_factory() as recovery:
                recovery.run_status(run_id, 'ERRO', safe[:1000], True)
        except Exception as status_exc:
            logger.error('LG POD: não foi possível registrar ERRO para %s (%s, codigo=%s). '
                         'A falha original foi %s, codigo=%s; nenhuma operação será repetida.',
                         run_id, type(status_exc).__name__, error_code(status_exc), type(exc).__name__, code)



def _start_profiles(start, end, dry_run, actor, background, profiles, parallel):
    validate_period(start, end)
    settings_list = [Settings.from_env(profile) for profile in profiles]
    if not settings_list:
        raise ValueError('Selecione ao menos uma operação.')
    for settings in settings_list:
        settings.validate(real=not dry_run)
    logins = [settings.user.strip().casefold() for settings in settings_list]
    if len(set(logins)) != len(logins):
        raise ValueError('Os perfis selecionados devem usar logins distintos. Nenhuma execução foi iniciada.')
    jobs = [(str(uuid.uuid4()), settings) for settings in settings_list]
    created = []
    try:
        with Repository() as repo:
            for run_id, settings in jobs:
                repo.create_run(run_id, settings, start, end, dry_run, actor)
                created.append(run_id)
    except Exception:
        # Sem iniciar o portal até todas as configurações/linhas serem preparadas.
        try:
            with Repository() as recovery:
                for run_id in created:
                    recovery.run_status(run_id, 'ERRO', 'Falha ao preparar o grupo de execuções; nenhum portal foi iniciado.', True)
        except Exception:
            logger.error('LG POD: falha ao registrar a preparação incompleta do grupo.')
        raise

    def launch_one(run_id, settings):
        execute(run_id, settings, start, end, dry_run)

    def run_sequence():
        for run_id, settings in jobs:
            launch_one(run_id, settings)

    threads = []
    if parallel:
        for run_id, settings in jobs:
            try:
                thread = threading.Thread(target=launch_one, args=(run_id, settings),
                    daemon=background, name='lg-pod-' + settings.operation + '-' + run_id[:8])
                thread.start()
                threads.append(thread)
            except Exception:
                with Repository() as recovery:
                    recovery.run_status(run_id, 'ERRO', 'Não foi possível iniciar a thread desta operação.', True)
        if not background:
            for thread in threads:
                thread.join()
    elif background:
        threading.Thread(target=run_sequence, daemon=True, name='lg-pod-sequencia').start()
    else:
        run_sequence()
    return [run_id for run_id, _ in jobs]


def start_execution(start, end, dry_run=True, actor='web', background=True, profile='SC'):
    return _start_profiles(start, end, dry_run, actor, background, (profile,), parallel=False)[0]


def start_group(start, end, dry_run=True, actor='web', background=True, parallel=True, profiles=None):
    # Compatibilidade: AMBAS continua sendo SC+PR; TODAS passa as três explicitamente.
    selected = ('SC', 'PR') if profiles is None else tuple(profiles)
    return _start_profiles(start, end, dry_run, actor, background, selected, parallel)
