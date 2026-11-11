"""Planejamento systemd e gravação atômica dos arquivos de auditoria."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

ZONE=ZoneInfo('America/Sao_Paulo')
CALENDAR='*-*-* 01:00:00 America/Sao_Paulo'


def previous_day(moment=None):
    moment=moment or datetime.now(ZONE)
    if moment.tzinfo is None:raise ValueError('O instante precisa conter fuso horário.')
    return moment.astimezone(ZONE).date()-timedelta(days=1)


def render_units(project,python,user):
    for value in (str(project),str(python)):
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+',value):
            raise ValueError('Use caminhos absolutos sem espaços/caracteres especiais para systemd.')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*',user):raise ValueError('Usuário Linux inválido.')
    service=f'''[Unit]
Description=LG POD diario - SC PR SP - dia anterior
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User={user}
WorkingDirectory={project}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=TZ=America/Sao_Paulo
UMask=0077
ExecStart={python} -u -m scripts.executar_lg_pod_diario --executar
TimeoutStartSec=8h
Restart=no
SuccessExitStatus=2
StandardOutput=journal
StandardError=journal
'''
    timer=f'''[Unit]
Description=LG POD todos os dias as 01h de Brasilia

[Timer]
OnCalendar={CALENDAR}
AccuracySec=1s
RandomizedDelaySec=0
Persistent=false
Unit=lg-pod-diario.service

[Install]
WantedBy=timers.target
'''
    return service,timer


def atomic_write(path,content):
    path=Path(path);temp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(content);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        if temp.exists():temp.unlink()


def save_audit(bundles,reference,directory):
    from .audit import csv_bytes,xlsx_bytes
    destination=Path(directory)/reference.isoformat()/uuid.uuid4().hex
    destination.mkdir(parents=True,mode=0o750)
    criteria=f'Rotina diária 01h Brasília | Comprovantes de {reference} a {reference} | SC, PR, SP'
    files={}
    for name,payload in [('auditoria.xlsx',xlsx_bytes(bundles,criteria)),('auditoria.csv',csv_bytes(bundles))]:
        atomic_write(destination/name,payload)
        files[name]=hashlib.sha256(payload).hexdigest()
    manifest=dict(referencia=reference.isoformat(),gerado_em=datetime.now(ZONE).isoformat(),
                  execucoes=[dict(id=b['run']['id'],transportador=b['run']['transportador'],status=b['run']['status'],
                                  simulacao=bool(b['run']['simulacao']),contagens=b['counts']) for b in bundles],sha256=files)
    atomic_write(destination/'manifesto.json',json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8'))
    return destination
