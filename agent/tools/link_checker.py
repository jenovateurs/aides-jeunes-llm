"""Vérification HTTP des liens des dispositifs (httpx async, retry, concurrence)."""
import asyncio
import httpx

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 6.1; Win64; x64; rv:99.0) "
        "Gecko/20100101 Firefox/99.0"
    ),
    "Referer": "https://mes-aides.1jeune1solution.beta.gouv.fr/",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-User": "?1",
}

TIMEOUT_STATUS = 499

# Classification des statuts pour décider quoi faire d'un lien non-200 :
# - "broken"     : lien réellement mort → PR passage en private.
# - "suspicious" : protection anti-bot / auth probable (SNCF, IDFM…) → PAS de PR,
#                  vérif humaine + candidat au fichier ignore.
REAL_BROKEN_STATUSES = {404, 410, TIMEOUT_STATUS}
SUSPICIOUS_STATUSES = {401, 403, 429}


def classify_status(status: int) -> str:
    """ok | broken | suspicious à partir d'un statut HTTP."""
    if status == 200:
        return "ok"
    if status in REAL_BROKEN_STATUSES or status >= 500:
        return "broken"
    return "suspicious"


async def _get_status(url: str, client: httpx.AsyncClient) -> int:
    try:
        resp = await client.get(
            url, headers=BROWSER_HEADERS, follow_redirects=True, timeout=15.0
        )
        return resp.status_code
    except httpx.TimeoutException:
        return TIMEOUT_STATUS
    except httpx.HTTPError:
        return TIMEOUT_STATUS


async def check_link(url: str, client: httpx.AsyncClient) -> dict:
    """Statut HTTP d'un lien ; retry 1× sur timeout. ok = status 200."""
    status = await _get_status(url, client)
    if status == TIMEOUT_STATUS:
        status = await _get_status(url, client)
    return {"url": url, "status": status, "ok": status == 200}


async def check_dispositif_links(dispositif, client, semaphore) -> dict:
    """Vérifie tous les liens d'un dispositif (sous sémaphore de concurrence)."""
    async def one(link):
        async with semaphore:
            res = await check_link(link["url"], client)
            res["type"] = link["type"]
            return res

    results = await asyncio.gather(*(one(l) for l in dispositif["links"]))
    return {"slug": dispositif["slug"], "links": results}
