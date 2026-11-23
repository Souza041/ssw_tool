"""CLI do assistente de configuração local do Nexus SSW Companion."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.local_agent.ssw_companion import (  # noqa: E402
    CompanionError,
    configure_sanitized_log,
)
from modules.local_agent.ssw_companion_config import run_configuration_wizard  # noqa: E402
from modules.local_agent.ssw_companion_proxy import load_companion_proxy  # noqa: E402
from ssw.settings import DEFAULT_SSW_BASE_URL  # noqa: E402


DEFAULT_OUTPUT = Path(r"C:\ProgramData\Nexus\ssw-companion-contract.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reconhece a estrutura oficial do SSW e gera o contrato automático "
            "do Companion."
        )
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--base-url", default=DEFAULT_SSW_BASE_URL)
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument(
        "--log-file", type=Path,
        default=Path.home() / "AppData/Local/Nexus/logs/ssw-companion.log",
    )
    parser.add_argument("--navigation-timeout", type=int, default=60)
    parser.add_argument(
        "--human-login-timeout", type=int, default=900,
        help="Prazo passivo para login/reCAPTCHA humano, entre 30 e 900 segundos.",
    )
    args = parser.parse_args(argv)
    logger = configure_sanitized_log(args.log_file)
    try:
        expected_domain = input("Informe seu domínio SSW esperado: ").strip()
        expected_identity = input("Informe seu usuário SSW esperado: ").strip()
        expected_unit = input("Informe sua unidade SSW esperada: ").strip()
        from playwright.sync_api import sync_playwright
        proxy = load_companion_proxy(args.env_file)
        output = run_configuration_wizard(
            base_url=args.base_url,
            output_path=args.output,
            playwright_factory=sync_playwright,
            expected_domain=expected_domain,
            expected_identity=expected_identity,
            expected_unit=expected_unit,
            proxy=proxy,
            logger=logger,
            navigation_timeout_ms=max(10, min(args.navigation_timeout, 180)) * 1000,
            human_login_timeout_seconds=max(
                30, min(args.human_login_timeout, 900)
            ),
        )
    except (CompanionError, ImportError) as exc:
        print(f"Configuração não concluída: {exc}", file=sys.stderr)
        return 2
    print(f"Contrato salvo em: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

