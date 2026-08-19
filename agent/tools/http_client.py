"""Client HTTP mutualisé de l'agent Veille : identité navigateur et politesse.

Beaucoup de sites publics (SNCF, IDFM, collectivités) filtrent les clients qui
« sentent le bot » : User-Agent daté, pas de HTTP/2, rafales de requêtes. On
centralise ici les contre-mesures pour que la vérification de liens ET le fetch
de contenu partagent la même identité :

- HTTP/2 avec ALPN (un UA Firefox qui négocie du HTTP/1.1 est repérable),
- en-têtes de navigateur récents, en français,
- un seul appel en vol par domaine, espacé d'un délai avec jitter.

Le User-Agent par défaut est mesuré, pas choisi par principe : voir le commentaire
de DEFAULT_USER_AGENT. Il est surchargeable via VEILLE_USER_AGENT.
"""
import asyncio
import random
from urllib.parse import urlparse

import httpx

from configs.settings import settings

# Mesuré sur ter.sncf.com / sncf.com (DataDome), 2026-08-12 :
# - ce UA passe (5/5 essais en 200) ;
# - y ajouter un suffixe identifiant le bot ("AidesJeunesLinkBot/1.0 (+url)")
#   déclenche un 403 immédiat — le WAF filtre sur le mot-clé ;
# - un UA Chrome récent est aussi bloqué : le fingerprint TLS d'httpx (OpenSSL)
#   ne colle pas à Chrome, et DataDome vérifie cette cohérence.
# Ne pas « moderniser » ce UA sans refaire la mesure ; le gain supposé n'existe pas.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 6.1; Win64; x64; rv:99.0) "
    "Gecko/20100101 Firefox/99.0"
)

BROWSER_HEADERS = {
    "User-Agent": settings.VEILLE_USER_AGENT or DEFAULT_USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://mes-aides.1jeune1solution.beta.gouv.fr/",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-User": "?1",
}


def make_client(**kwargs) -> httpx.AsyncClient:
    """AsyncClient configuré pour la veille (HTTP/2, headers, timeouts).

    `verify=False` est conservé : les collectivités ont souvent des chaînes TLS
    incomplètes, et on ne veut pas transformer ça en faux lien cassé.
    """
    timeout = httpx.Timeout(
        connect=settings.VEILLE_HTTP_TIMEOUT_CONNECT,
        read=settings.VEILLE_HTTP_TIMEOUT_READ,
        write=settings.VEILLE_HTTP_TIMEOUT_CONNECT,
        pool=settings.VEILLE_HTTP_TIMEOUT_READ,
    )
    options = {
        "http2": True,
        "verify": False,
        "headers": BROWSER_HEADERS,
        "follow_redirects": True,
        "timeout": timeout,
    }
    options.update(kwargs)
    return httpx.AsyncClient(**options)


class HostThrottle:
    """Un seul appel en vol par domaine, espacé de `delay` secondes.

    La sémaphore globale de concurrence ne protège de rien si les 3 requêtes
    simultanées visent le même site : c'est exactement le profil qui déclenche
    les WAF. On sérialise donc par hostname.
    """

    def __init__(self, delay: float | None = None, jitter: float = 0.3):
        self.delay = settings.VEILLE_HTTP_HOST_DELAY if delay is None else delay
        self.jitter = jitter
        self._locks: dict[str, asyncio.Lock] = {}
        self._next_allowed: dict[str, float] = {}

    def _lock(self, host: str) -> asyncio.Lock:
        if host not in self._locks:
            self._locks[host] = asyncio.Lock()
        return self._locks[host]

    def _wait_for(self, host: str) -> float:
        """Délai à respecter avant la prochaine requête vers `host`."""
        loop = asyncio.get_running_loop()
        return max(0.0, self._next_allowed.get(host, 0.0) - loop.time())

    def _schedule_next(self, host: str) -> None:
        loop = asyncio.get_running_loop()
        spread = self.delay * self.jitter
        wait = self.delay + random.uniform(-spread, spread)
        self._next_allowed[host] = loop.time() + max(0.0, wait)

    def slot(self, url: str):
        """Context manager async réservant un créneau pour l'hôte de `url`."""
        return _HostSlot(self, (urlparse(url).hostname or "").lower())


class _HostSlot:
    def __init__(self, throttle: HostThrottle, host: str):
        self.throttle = throttle
        self.host = host
        self._lock = throttle._lock(host)

    async def __aenter__(self):
        await self._lock.acquire()
        wait = self.throttle._wait_for(self.host)
        if wait > 0:
            await asyncio.sleep(wait)
        return self

    async def __aexit__(self, *exc):
        self.throttle._schedule_next(self.host)
        self._lock.release()
        return False
