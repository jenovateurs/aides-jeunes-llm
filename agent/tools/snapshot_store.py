"""Stockage local des snapshots de page : un dossier par fiche.

    <root>/<slug>/<YYYYMMDD>.txt   lignes normalisées
    <root>/<slug>/meta.json        {url, extractor, snapshots, last_posted}

Écritures atomiques (fichier temporaire + rename) : un run interrompu ne laisse
jamais un snapshot à moitié écrit, qui produirait un faux diff au run suivant.
"""
import hashlib
import json
import os
import tempfile
from pathlib import Path


def sha256_lines(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class SnapshotStore:
    def __init__(self, root: Path, keep: int = 8):
        self.root = Path(root)
        self.keep = keep

    def _dir(self, slug: str) -> Path:
        return self.root / slug

    def load_meta(self, slug: str) -> dict:
        path = self._dir(slug) / "meta.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"snapshots": []}

    def _write_meta(self, slug: str, meta: dict) -> None:
        _atomic_write(self._dir(slug) / "meta.json",
                      json.dumps(meta, ensure_ascii=False, indent=2))

    def latest_before(self, slug: str, day: str) -> dict | None:
        """Dernier snapshot strictement antérieur à `day` (YYYYMMDD)."""
        for snap in reversed(self.load_meta(slug)["snapshots"]):
            if snap["date"] < day:
                path = self._dir(slug) / f"{snap['date']}.txt"
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    return None
                return {"date": snap["date"], "lines": text.split("\n") if text else [],
                        "sha256": snap["sha256"]}
        return None

    def save(self, slug: str, day: str, lines: list[str], url: str,
             extractor: str) -> None:
        """Enregistre le snapshot du jour (écrase celui du même jour).

        Contenu identique au dernier snapshot stocké → pas de nouveau fichier :
        garder 8 copies identiques réduirait l'historique utile à rien.
        """
        meta = self.load_meta(slug)
        meta["url"], meta["extractor"] = url, extractor
        sha = sha256_lines(lines)
        snaps = [s for s in meta["snapshots"] if s["date"] != day]
        if not (snaps and snaps[-1]["sha256"] == sha):
            text = "\n".join(lines)
            _atomic_write(self._dir(slug) / f"{day}.txt", text)
            snaps.append({"date": day, "sha256": sha, "chars": len(text)})
        elif (self._dir(slug) / f"{day}.txt").exists():
            (self._dir(slug) / f"{day}.txt").unlink()
        snaps.sort(key=lambda s: s["date"])
        for old in snaps[:-self.keep]:
            (self._dir(slug) / f"{old['date']}.txt").unlink(missing_ok=True)
        meta["snapshots"] = snaps[-self.keep:]
        self._write_meta(slug, meta)

    def set_last_posted(self, slug: str, pr: str, sha: str) -> None:
        """Mémorise le dernier diff posté : évite de le reposter sur la PR."""
        meta = self.load_meta(slug)
        meta["last_posted"] = {"pr": pr, "sha": sha}
        self._write_meta(slug, meta)
