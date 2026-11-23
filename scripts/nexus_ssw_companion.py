"""CLI interativa da homologação local do Nexus SSW Companion."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.local_agent.ssw_companion import (  # noqa: E402
    CompanionContract,
    CompanionError,
    configure_sanitized_log,
    run_interactive_homologation,
)
from modules.local_agent.ssw_companion_proxy import load_companion_proxy  # noqa: E402
from ssw.settings import DEFAULT_SSW_BASE_URL  # noqa: E402


def _confirm() -> bool:
    print("Conclua o login e o reCAPTCHA manualmente na janela do Chromium.")
    answer = input("Quando o menu autenticado estiver visível, digite CONFIRMAR: ")
    return answer.strip() == "CONFIRMAR"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Homologa autenticação humana SSW em Chromium temporário e visível."
    )
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--base-url", default=DEFAULT_SSW_BASE_URL)
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument(
        "--log-file", type=Path,
        default=Path.home() / "AppData/Local/Nexus/logs/ssw-companion.log",
    )
    parser.add_argument("--readonly-probe", action="store_true")
    parser.add_argument("--navigation-timeout", type=int, default=60)
    parser.add_argument(
        "--human-login-timeout", type=int, default=900,
        help="Prazo passivo para login/reCAPTCHA humano, entre 30 e 900 segundos.",
    )
    args = parser.parse_args(argv)
    logger = configure_sanitized_log(args.log_file)
    try:
        contract = CompanionContract.from_file(args.contract)
        proxy = load_companion_proxy(args.env_file)
        values = dotenv_values(args.env_file, encoding="utf-8-sig", interpolate=False)
        expected_domain = str(values.get("NEXUS_SSW_EXPECTED_DOMAIN") or "").strip() or input("Informe seu domínio SSW esperado: ").strip()
        expected_identity = str(values.get("NEXUS_SSW_EXPECTED_USER") or "").strip() or input("Informe seu usuário SSW esperado: ").strip()
        expected_unit = str(values.get("NEXUS_SSW_EXPECTED_UNIT") or "").strip() or input("Informe sua unidade SSW esperada: ").strip()
        print("O Companion abrirá somente a origem HTTPS oficial do SSW.")
        from playwright.sync_api import sync_playwright
        result = run_interactive_homologation(
            base_url=args.base_url,
            contract=contract,
            expected_identity=expected_identity,
            expected_unit=expected_unit,
            expected_domain=expected_domain,
            confirmation=_confirm,
            execute_probe=args.readonly_probe,
            playwright_factory=sync_playwright,
            logger=logger,
            proxy=proxy,
            navigation_timeout_ms=max(10, min(args.navigation_timeout, 180)) * 1000,
            human_login_timeout_seconds=max(
                30, min(args.human_login_timeout, 900)
            ),
        )
    except (CompanionError, ImportError) as exc:
        print(f"Homologação não concluída: {exc}", file=sys.stderr)
        return 2
    print(f"Homologação concluída. Sondagem read-only: {result['readonly_probe']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

