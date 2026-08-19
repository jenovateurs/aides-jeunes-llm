"""Vérification HTTP des liens des dispositifs (httpx async, retry, concurrence)."""
import asyncio
import random
import socket
import ssl

import httpx

from agent.tools.http_client import BROWSER_HEADERS, HostThrottle  # noqa: F401
from configs.settings import settings

# Pseudo-statuts internes pour les erreurs réseau (pas de réponse HTTP).
# On distingue le transitoire du définitif : un timeout ressemble beaucoup à
# une protection anti-bot, alors qu'un domaine qui ne résout plus est mort.
NETWORK_TRANSIENT = 499   # timeout, reset, TLS, lecture coupée → suspicious
NETWORK_FATAL = 599       # DNS introuvable, connexion refusée, URL invalide → broken

# Classification des statuts pour décider quoi faire d'un lien non-200 :
# - "broken"     : lien réellement mort → PR passage en private.
# - "suspicious" : protection anti-bot / auth probable (SNCF, IDFM…) → PAS de PR,
#                  vérif humaine + candidat au fichier ignore.
REAL_BROKEN_STATUSES = {404, 410, NETWORK_FATAL}
SUSPICIOUS_STATUSES = {401, 403, 429, NETWORK_TRANSIENT}

# Statuts qui méritent une nouvelle tentative. 401/403 en sont exclus : sans
# changer d'identité, réessayer ne fait qu'aggraver le profil de la requête.
RETRYABLE_STATUSES = {NETWORK_TRANSIENT, NETWORK_FATAL, 429}

BACKOFF_SECONDS = (2.0, 6.0)


def classify_status(status: int) -> str:
    """ok | broken | suspicious à partir d'un statut HTTP."""
    if status == 200:
        return "ok"
    if status in SUSPICIOUS_STATUSES:
        return "suspicious"
    if status in REAL_BROKEN_STATUSES or status >= 500:
        return "broken"
    return "suspicious"


def _is_dns_or_refused(exc: BaseException) -> bool:
    """Détecte une cause réseau définitive dans la chaîne d'exceptions."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (socket.gaierror, ConnectionRefusedError)):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def classify_error(exc: Exception) -> int:
    """Pseudo-statut réseau (499 transitoire / 599 définitif) pour une exception."""
    # URL malformée ou schéma inconnu : c'est la fiche qui est fautive.
    if isinstance(exc, (httpx.InvalidURL, httpx.UnsupportedProtocol)):
        return NETWORK_FATAL
    # Le TLS échoue même avec verify=False → on reste prudent, pas de PR.
    if isinstance(exc, (ssl.SSLError, httpx.ConnectTimeout, httpx.TimeoutException)):
        return NETWORK_TRANSIENT
    if isinstance(exc, httpx.ConnectError):
        return NETWORK_FATAL if _is_dns_or_refused(exc) else NETWORK_TRANSIENT
    return NETWORK_TRANSIENT


async def _get_status(url: str, client: httpx.AsyncClient) -> int:
    try:
        resp = await client.get(url, headers=BROWSER_HEADERS, follow_redirects=True)
        return resp.status_code
    except Exception as exc:  # noqa: BLE001 — toute erreur devient un pseudo-statut
        if not isinstance(exc, (httpx.HTTPError, ssl.SSLError, OSError)):
            raise
        return classify_error(exc)


async def _backoff(attempt: int) -> None:
    """Pause avant la tentative suivante, avec jitter ±30 %."""
    base = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
    await asyncio.sleep(base * random.uniform(0.7, 1.3))


async def check_link(url: str, client: httpx.AsyncClient, throttle=None) -> dict:
    """Statut HTTP d'un lien, avec retry et backoff. ok = status 200."""
    attempts = max(1, settings.VEILLE_HTTP_RETRIES)
    status = NETWORK_TRANSIENT
    for attempt in range(attempts):
        if throttle is not None:
            async with throttle.slot(url):
                status = await _get_status(url, client)
        else:
            status = await _get_status(url, client)
        if status not in RETRYABLE_STATUSES:
            break
        if attempt < attempts - 1:
            await _backoff(attempt)
    return {"url": url, "status": status, "ok": status == 200}


async def check_dispositif_links(dispositif, client, semaphore, throttle=None) -> dict:
    """Vérifie tous les liens d'un dispositif (sous sémaphore de concurrence)."""
    async def one(link):
        async with semaphore:
            res = await check_link(link["url"], client, throttle)
            res["type"] = link["type"]
            return res

    results = await asyncio.gather(*(one(l) for l in dispositif["links"]))
    return {"slug": dispositif["slug"], "links": results}
