"""Fichier ignore des faux négatifs de liens (édité par un humain).

Certains sites (SNCF, Île-de-France Mobilités…) renvoient 403/429 à cause d'une
protection anti-bot alors que le lien est valide. On les liste ici pour ne PAS
les traiter comme cassés (pas de PR private:true).
"""
from pathlib import Path
from urllib.parse import urlparse
import yaml


class LinkIgnore:
    """Domaines et URLs à ignorer lors de la détection de liens cassés."""

    def __init__(self, domains: list[str] | None = None, urls: list[str] | None = None):
        self.domains = {d.lower().lstrip(".") for d in (domains or [])}
        self.urls = set(urls or [])

    def is_ignored(self, url: str) -> bool:
        if url in self.urls:
            return True
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return False
        # match domaine exact ou sous-domaine (ter.sncf.com couvre www.ter.sncf.com)
        return any(host == d or host.endswith("." + d) for d in self.domains)


def load_link_ignore(path: Path) -> LinkIgnore:
    """Charge le fichier ignore ; renvoie une liste vide si absent/illisible."""
    path = Path(path)
    if not path.exists():
        return LinkIgnore()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return LinkIgnore()
    return LinkIgnore(domains=data.get("domains", []), urls=data.get("urls", []))
