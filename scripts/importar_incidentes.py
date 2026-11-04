"""CLI de importação; execução padrão é simulação sem escrita."""
import argparse
import json
from modules.incidentes.persistence import importar_auditoria


def main():
    parser = argparse.ArgumentParser(description='Importação versionada OP930')
    parser.add_argument('--arquivo', required=True)
    parser.add_argument('--aplicar', action='store_true', help='Gravar no MySQL')
    parser.add_argument('--publicar', action='store_true', help='Publicar como base ativa')
    parser.add_argument('--lote', type=int, default=100)
    args = parser.parse_args()
    if args.publicar and not args.aplicar:
        parser.error('--publicar exige --aplicar')
    resultado = importar_auditoria(args.arquivo, publicar=args.publicar,
                                  dry_run=not args.aplicar, lote=args.lote)
    print(json.dumps(resultado, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
