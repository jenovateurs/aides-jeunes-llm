"""Wrapper CLI du mode revival (usage cron). python -m agent.revival_cli

Sens inverse de la veille : parcourt les fiches `private: true`, reteste leurs
liens, et pour celles dont tous les liens répondent ouvre une PR qui retire
`private` (et met à jour le montant maximum si la page en annonce un autre).
"""
import argparse
import asyncio
import json

from agent.agents.veille import veille_agent


def parse_args(argv) -> dict:
    parser = argparse.ArgumentParser(
        description="Agent Veille — réactivation des fiches private")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--model-name", dest="model_name", default=None)
    parser.add_argument(
        "--all-private", dest="all_private", action="store_true",
        help="Teste TOUTES les fiches private, pas seulement celles que la "
             "veille a elle-même passées en private (state.json). Attention : "
             "beaucoup sont private par décision métier — lien vivant ne "
             "signifie pas dispositif vivant.",
    )
    parser.add_argument(
        "--links-only", dest="links_only", action="store_true",
        help="Ne vérifie que les liens : pas de contrôle du montant, aucun "
             "appel LLM",
    )
    ns = parser.parse_args(argv)
    return {"limit": ns.limit, "only": ns.only, "model_name": ns.model_name,
            "links_only": ns.links_only, "all_private": ns.all_private,
            "revival": True}


async def main_async(params: dict) -> dict:
    async def emit(event_type, payload):
        print(f"[{event_type}] {json.dumps(payload, ensure_ascii=False)}")
    return await veille_agent.run(params, emit=emit)


def main(argv=None):
    params = parse_args(argv if argv is not None else None)
    result = asyncio.run(main_async(params))
    print(f"Rapport : {result.get('report_path')}")
    return result


if __name__ == "__main__":
    main()
