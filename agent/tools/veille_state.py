"""State store local pour l'agent Veille : rotation batch sur les dispositifs."""
import json
import os
from datetime import date
from pathlib import Path


def _days_between(iso_a: str, iso_b: str) -> int:
    return abs((date.fromisoformat(iso_a) - date.fromisoformat(iso_b)).days)


class StateStore:
    """Persistance JSON de la date de dernière analyse par dispositif."""

    def __init__(self, path: Path, today: str):
        self.path = Path(path)
        self.today = today
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"version": 1, "last_run_date": None, "benefits": {}}

    def select_pending(self, dispositifs, priority, limit, recheck_days, only=None):
        """Retourne les dispositifs à traiter ce run."""
        if only:
            wanted = set(only)
            return [d for d in dispositifs if d["slug"] in wanted]

        benefits = self.data.get("benefits", {})
        candidates = []
        for d in dispositifs:
            entry = benefits.get(d["slug"])
            if entry and entry.get("last_run"):
                if _days_between(entry["last_run"], self.today) <= recheck_days:
                    continue  # traité récemment
            candidates.append(d)
        candidates.sort(key=lambda d: priority.get(d["slug"], 0.0), reverse=True)
        return candidates[:limit]

    def mark(self, slug, link_status, content_status, pr_url):
        """Met à jour l'entrée d'un dispositif (date toujours mise à jour)."""
        benefits = self.data.setdefault("benefits", {})
        entry = benefits.setdefault(slug, {"runs": 0})
        entry["last_run"] = self.today
        entry["runs"] = entry.get("runs", 0) + 1
        entry["link_status"] = link_status
        entry["content_status"] = content_status
        entry["pr_url"] = pr_url

    def save(self):
        """Écriture atomique (tmp + rename)."""
        self.data["last_run_date"] = self.today
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
