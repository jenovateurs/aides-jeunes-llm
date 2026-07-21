"""Priorisation des dispositifs via les stats de prod (recorder → Matomo → vide)."""
import httpx


async def _get_json(client: httpx.AsyncClient, url: str, timeout: float = 15.0):
    resp = await client.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


async def fetch_priority_map(
    stats_url: str, matomo_url: str, client: httpx.AsyncClient | None = None
) -> dict[str, float]:
    """slug -> score de priorité. Ne lève jamais ; {} si aucune source dispo.

    Source primaire = Matomo (Events.getName, `label`=id benefit kebab-case,
    ~221/431 fiches matchent). Le recorder scalingo est actuellement injoignable
    (fallback secondaire, timeout court pour ne pas bloquer).
    """
    owns_client = client is None
    client = client or httpx.AsyncClient()
    try:
        # 1. Source primaire : Matomo Events.getName
        try:
            data = await _get_json(client, matomo_url)
            result: dict[str, float] = {}
            for row in data:
                slug = row.get("label")
                visits = row.get("nb_visits")
                if slug and visits:
                    result[slug] = float(visits)
            if result:
                return result
        except Exception as exc:
            print(f"⚠️ Veille: Matomo indisponible ({exc}), fallback recorder")

        # 2. Fallback : recorder /benefits (souvent injoignable → timeout court)
        try:
            data = await _get_json(client, stats_url, timeout=5.0)
            result = {}
            for row in data:
                slug = row.get("id")
                events = row.get("events") or {}
                if slug:
                    result[slug] = float(sum(events.values()))
            return result
        except Exception as exc:
            print(f"⚠️ Veille: recorder indisponible ({exc}), ordre alphabétique")
            return {}
    finally:
        if owns_client:
            await client.aclose()
