"""Cada intenção de escrita é persistida ANTES da chamada ao portal."""
import logging
import threading
import uuid
from .config import validate_period, Settings
from .repository import Repository
from .portal import Portal
from .parser import eligibility, invoice_from_filename, Row

logger = logging.getLogger(__name__)
# Etapas anteriores a qualquer envio permitem nova consulta; demais são conservadoras.
UNCERTAIN = {'MAPEAMENTO_SOLICITADO', 'ENVIO_SOLICITADO', 'CONFERIR'}


def matching_mapped(pod, nf, carrier):
    mapped = pod.values.get('NOTAS_MAPEADAS', '')
    return (pod.status == 'MAPPED' and pod.values.get('POD_STATUS') == '2'
            and pod.values.get('P_CARR_CD') == carrier
            and mapped.strip() == nf)


def process(repo, portal, run_id, settings, start, end, dry_run):
    rows = portal.search(start, end)
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
    for original in rows:
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
            except Exception:
                repo.item(run_id, settings, current, nf, 'CONFERIR',
                          'Mapeamento sem confirmação conclusiva. Conferir no portal; não repetir automaticamente.', invoice)
                # Timeout ou sessão inválida: parar o lote sem enviar outras notas.
                raise RuntimeError('Mapeamento sem confirmação. Consulte o item marcado para conferência.') from None
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
        except Exception:
            repo.item(run_id, settings, current, nf, 'CONFERIR',
                      'Envio sem confirmação conclusiva. Conferir no portal; não repetir automaticamente.', invoice)
            raise RuntimeError('Envio sem confirmação. Consulte o item marcado para conferência.') from None
        repo.item(run_id, settings, current, nf, 'ENVIADO', message + ' — enviado para aprovação LG.', invoice)
    return problems


def execute(run_id, settings, start, end, dry_run, repo_factory=Repository, portal_factory=Portal):
    with repo_factory() as repo:
        if not repo.lock():
            repo.run_status(run_id, 'BLOQUEADA', 'Já existe uma execução LG POD em andamento.', True)
            return
        try:
            repo.interrupt_old(run_id)
            settings.validate(real=not dry_run)
            validate_period(start, end)
            repo.run_status(run_id, 'EXECUTANDO')
            with portal_factory(settings) as portal:
                portal.login()
                problems = process(repo, portal, run_id, settings, start, end, dry_run)
            repo.run_status(run_id, 'COM_PENDENCIAS' if problems else 'CONCLUIDA',
                            'Simulação sem alterações no portal.' if dry_run else 'Processamento finalizado.', True)
        except Exception as exc:
            # Não colocar respostas HTML, cookies, URLs ou senhas nos logs/relatórios.
            name = type(exc).__name__
            logger.error('LG POD: execução %s interrompida (%s).', run_id, name)
            safe = str(exc) if isinstance(exc, (ValueError, RuntimeError)) and not any(
                token in str(exc) for token in [settings.password, 'http', '<html'] if token
            ) else 'Falha de conexão ou do portal. Consulte as etapas registradas e verifique o ambiente.'
            repo.run_status(run_id, 'ERRO', safe[:1000], True)


def start_execution(start, end, dry_run=True, actor='web', background=True):
    settings = Settings.from_env()
    settings.validate(real=not dry_run)
    validate_period(start, end)
    run_id = str(uuid.uuid4())
    with Repository() as repo:
        repo.create_run(run_id, settings, start, end, dry_run, actor)
    if background:
        threading.Thread(target=execute, args=(run_id, settings, start, end, dry_run),
                         daemon=True, name='lg-pod-' + run_id[:8]).start()
    else:
        execute(run_id, settings, start, end, dry_run)
    return run_id
