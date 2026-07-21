"""Wrapper CLI de l'agent Veille (usage cron). python -m agent.veille_cli"""
import argparse
import asyncio
import json

from agent.agents.veille import veille_agent


def parse_args(argv) -> dict:
    parser = argparse.ArgumentParser(description="Agent Veille — batch")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--model-name", dest="model_name", default=None)
    ns = parser.parse_args(argv)
    return {"limit": ns.limit, "only": ns.only, "model_name": ns.model_name}


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
