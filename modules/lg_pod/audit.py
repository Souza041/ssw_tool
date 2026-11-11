"""Exportação de auditoria: snapshots do banco, sem ações no portal."""
import csv
from datetime import datetime
from io import BytesIO, StringIO
from zoneinfo import ZoneInfo
from .presentation import clean_message, LABELS, stamp

ITEM_HEADERS = ['Execução', 'Usuário portal', 'Transportador', 'Período inicial', 'Período final',
                'Modo', 'Situação da execução', 'Solicitado por', 'Início da execução', 'Fim da execução', 'Registro', 'POD', 'Arquivo', 'NF', 'Série',
                'Chave da nota', 'Status portal', 'Etapa código', 'Resultado', 'Mensagem', 'Atualizado em']
RUN_HEADERS = ['Execução', 'Usuário portal', 'Transportador', 'Período inicial', 'Período final',
               'Modo', 'Situação', 'Solicitado por', 'Início', 'Fim', 'Itens totais',
               'Itens exportados', 'Enviados', 'Consultados', 'Ignorados', 'Erros consulta',
               'Conferir', 'Mapeados', 'Mensagem']


def item_values(run, item):
    return [run['id'],run.get('portal_user',''),run.get('transportador',''),str(run['data_inicial']),
            str(run['data_final']),'Consulta sem envio' if run['simulacao'] else 'Mapeamento e envio',
            run['status'],run.get('solicitado_por',''),stamp(run.get('iniciado_em')),stamp(run.get('finalizado_em')),str(item.get('id','')),item.get('pod_id',''),item.get('arquivo',''),
            item.get('nf',''),item.get('serie',''),item.get('chave_nota',''),item.get('status_portal',''),
            item.get('etapa',''),LABELS.get(item.get('etapa',''),item.get('etapa','')),
            clean_message(item.get('mensagem','')),stamp(item.get('atualizado_em')) if item else '']


def run_values(bundle):
    r,c=bundle['run'],bundle['counts']
    return [r['id'],r.get('portal_user',''),r.get('transportador',''),str(r['data_inicial']),str(r['data_final']),
            'Consulta sem envio' if r['simulacao'] else 'Mapeamento e envio',r['status'],r.get('solicitado_por',''),
            stamp(r.get('iniciado_em')),stamp(r.get('finalizado_em')),sum(c.values()),len(bundle['items']),
            c.get('ENVIADO',0),c.get('SIMULADO',0),c.get('IGNORADO',0),c.get('ERRO_CONSULTA',0),
            c.get('CONFERIR',0)+c.get('MAPEAMENTO_SOLICITADO',0)+c.get('ENVIO_SOLICITADO',0),
            c.get('MAPEADO',0),clean_message(r.get('mensagem',''))]


def csv_bytes(bundles):
    out=StringIO(newline='');writer=csv.writer(out,delimiter=';',lineterminator='\r\n')
    writer.writerow(ITEM_HEADERS)
    for bundle in bundles:
        for item in bundle['items'] or [dict(mensagem='Sem itens para os critérios desta exportação.')]:
            values=item_values(bundle['run'],item)
            # Excel interpreta CSV como fórmulas; preservar o conteúdo como texto.
            writer.writerow(["'"+str(v) if str(v).lstrip().startswith(('=','+','-','@')) or str(v).startswith(('\t','\r','\n')) else v for v in values])
    return out.getvalue().encode('utf-8-sig')


def xlsx_bytes(bundles, criteria='Execuções selecionadas'):
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    wb=Workbook(write_only=True)
    def sheet(name,headers,rows,widths):
        from openpyxl.utils import get_column_letter
        ws=wb.create_sheet(name);ws.freeze_panes='A2'
        for index,width in enumerate(widths,1):ws.column_dimensions[get_column_letter(index)].width=width
        title=[]
        for value in headers:
            cell=WriteOnlyCell(ws,value=value);cell.font=Font(name='Calibri',bold=True,color='FFFFFF')
            cell.fill=PatternFill('solid',fgColor='2559DC');title.append(cell)
        ws.append(title);count=1
        alignment=Alignment(vertical='top',wrap_text=True)
        for row in rows:
            cells=[]
            for value in row:
                if not isinstance(value,(int,float)):value=ILLEGAL_CHARACTERS_RE.sub('',str(value))[:32767]
                cell=WriteOnlyCell(ws,value=value)
                if isinstance(value,str):cell.data_type='s';cell.number_format='@'
                cell.alignment=alignment;cells.append(cell)
            ws.append(cells);count+=1
        ws.auto_filter.ref='A1:'+get_column_letter(len(headers))+str(count)
        return ws
    sheet('Execuções',RUN_HEADERS,(run_values(b) for b in bundles),[39,22,18,16,16,25,24,20,23,23,14,17,13,14,13,16,13,13,70])
    sheet('Comprovantes',ITEM_HEADERS,(item_values(b['run'],i) for b in bundles for i in b['items']),[39,22,18,16,16,25,24,20,23,23,12,16,34,18,12,44,18,26,26,75,23])
    generated=datetime.now(ZoneInfo('America/Sao_Paulo')).isoformat(timespec='seconds')
    sheet('Critérios',['Campo','Informação'],[
        ['Gerado em (Brasília)',generated],['Seleção',criteria],
        ['Escopo','Inclui todos os itens que atendem aos filtros; não se limita à página da tela.'],
        ['Contagens','Execuções: contagens totais do banco; Comprovantes: linhas selecionadas para exportar.'],
        ['Interpretação','ENVIADO significa envio para aprovação; aprovação final pertence à LG.'],
        ['Tentativas','Uma NF/POD pode constar em execuções diferentes. Não somar tentativas como documentos únicos.'],
        ['Integridade','Snapshot de leitura. Em execução ativa, os registros ainda podem mudar.'],
        ['Sem itens','CSV inclui uma linha descritiva para execução sem itens selecionados; Excel preserva o resumo.'],
        ['Execuções selecionadas',len(bundles)],['Itens exportados',sum(len(b['items']) for b in bundles)]
    ],[30,110])
    out=BytesIO();wb.save(out);return out.getvalue()
