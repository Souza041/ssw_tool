"""Instalar timer na VPS; só --ativar habilita, após preflight sem portal."""
import argparse
from pathlib import Path
import os
import pwd
import subprocess
from tempfile import TemporaryDirectory
from modules.lg_pod.scheduler import render_units


def main(argv=None):
    p=argparse.ArgumentParser(description='Instalar timer LG POD 01h Brasília (D-1) na VPS')
    p.add_argument('--projeto',default='/home/automacao/bots/ssw_tool')
    p.add_argument('--python',default='/home/automacao/venv/bin/python')
    p.add_argument('--usuario',default='automacao')
    p.add_argument('--ativar',action='store_true',help='Validar configuração e habilitar o timer para a próxima 01h')
    args=p.parse_args(argv)
    if os.geteuid()!=0:p.error('Execute com sudo para instalar as unidades systemd.')
    project=Path(args.projeto).resolve();python=Path(args.python).absolute()
    if not project.is_dir() or not python.is_file():p.error('Confira os caminhos do projeto e do Python da venv.')
    if not (project/'scripts/executar_lg_pod_diario.py').is_file():p.error('Copie o pacote para a VPS antes de instalar o timer.')
    account=pwd.getpwnam(args.usuario)
    if account.pw_uid==0:p.error('Use o usuário da automação, não root, para executar o serviço.')
    service,timer=render_units(project,python,args.usuario)
    if args.ativar:
        env={'HOME':account.pw_dir,'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8'}
        subprocess.run([str(python),'-m','scripts.executar_lg_pod_diario','--check','--executar'],cwd=project,
                       user=account.pw_uid,group=account.pw_gid,extra_groups=[],env=env,check=True)
    target=Path('/etc/systemd/system')
    for name in ('lg-pod-diario.service','lg-pod-diario.timer'):
        if (target/name).is_symlink():p.error('Unidade existente é um link/mascaramento. Revise antes de substituir.')
    with TemporaryDirectory(prefix='lg-pod-units-') as staging:
        for name,text in [('lg-pod-diario.service',service),('lg-pod-diario.timer',timer)]:
            (Path(staging)/name).write_text(text,encoding='utf-8')
        subprocess.run(['systemd-analyze','verify',str(Path(staging)/'lg-pod-diario.service'),
                        str(Path(staging)/'lg-pod-diario.timer')],check=True)
    # Não modificar o timer se uma execução estiver ativa.
    active=subprocess.run(['systemctl','is-active','--quiet','lg-pod-diario.service'],check=False)
    if active.returncode==0:p.error('Há execução diária ativa. Aguarde terminar antes de reinstalar.')
    load=subprocess.run(['systemctl','show','lg-pod-diario.timer','-p','LoadState','--value'],capture_output=True,text=True,check=False)
    if load.stdout.strip() not in {'loaded','not-found'}:
        p.error('Não foi possível consultar o estado do timer. Revise o systemd antes de instalar.')
    if load.stdout.strip()!='not-found':subprocess.run(['systemctl','stop','lg-pod-diario.timer'],check=True)
    for name,text in [('lg-pod-diario.service',service),('lg-pod-diario.timer',timer)]:
        path=target/name;path.write_text(text,encoding='utf-8');path.chmod(0o644)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    if args.ativar:
        subprocess.run(['systemctl','enable','--now','lg-pod-diario.timer'],check=True)
        subprocess.run(['systemctl','list-timers','--all','lg-pod-diario.timer'],check=True)
        print('Timer habilitado para 01h Brasília; não foi iniciado um lote manual agora.')
    else:
        subprocess.run(['systemctl','disable','lg-pod-diario.timer'],check=True)
        print('Unidades instaladas, timer desabilitado. Use --ativar após validar a configuração.')


if __name__=='__main__':main()
