import os
from dotenv import load_dotenv
from pathlib import Path

# Load .env
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

class Settings:
    # LLM Configuration
    MODEL_NAME = os.getenv("MODEL_NAME", "ministral-3b-2512")
    OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "http://localhost:1234/v1")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "lm-studio")

    MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_API_AI_STUDIO")
    CROQ_API_KEY = os.getenv("CROQ_API_KEY")

    # Redis Configuration
    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB = int(os.getenv("REDIS_DB", "0"))
    REDIS_CACHE_TTL = int(os.getenv("REDIS_CACHE_TTL", "3600"))  # 1 hour

    # Agent Parameters
    MAX_TOKENS = int(os.getenv("MAX_TOKENS", "4096"))

    # Paths
    # BASE_DIR = tools/aj-llm ; le repo aides-jeunes est 2 niveaux au-dessus.
    BASE_DIR = Path(__file__).parent.parent
    AIDES_JEUNES_ROOT = BASE_DIR.parent.parent

    # Aides Jeunes data path (par défaut : data/ du repo qui contient cet outil)
    AIDES_JEUNES_DATA_PATH = Path(os.getenv(
        "AIDES_JEUNES_DATA_PATH",
        str(AIDES_JEUNES_ROOT / "data")
    ))
    # Dossier des fiches benefits (sortie Agent 1)
    AIDES_JEUNES_BENEFITS_PATH = AIDES_JEUNES_DATA_PATH / "benefits" / "javascript"
    # Dossiers scannés par l'Agent 3 (veille) : les fiches openfisca ont la même
    # forme YAML que les javascript, seul le dossier change.
    # (boucle et non compréhension : le corps d'une compréhension ne voit pas
    # les attributs de classe en cours de définition)
    VEILLE_BENEFITS_DIRS = []
    for _name in os.getenv("VEILLE_BENEFITS_DIRS", "javascript,openfisca").split(","):
        if _name.strip():
            VEILLE_BENEFITS_DIRS.append(
                AIDES_JEUNES_DATA_PATH / "benefits" / _name.strip())
    del _name

    # Incitations covoiturage : tableau JSON (pas des fiches YAML) — vérifiées
    # en lecture seule par l'Agent 3 via --covoiturage.
    AIDES_JEUNES_COVOITURAGE_PATH = (
        AIDES_JEUNES_DATA_PATH / "benefits" / "dynamic" / "incitations-covoiturage.json")

    # ── Agent 3 : Veille ──────────────────────────────────────────────
    VEILLE_STATS_URL = os.getenv(
        "VEILLE_STATS_URL",
        "https://aides-jeunes-stats-recorder.osc-fr1.scalingo.io/benefits",
    )
    VEILLE_MATOMO_URL = os.getenv(
        "VEILLE_MATOMO_URL",
        "https://stats.beta.gouv.fr/index.php?module=API&format=JSON&idSite=63"
        "&period=range&date=previous30&method=Events.getName&filter_limit=-1",
    )
    VEILLE_STATE_PATH = Path(os.getenv(
        "VEILLE_STATE_PATH", str(BASE_DIR / ".veille" / "state.json")
    ))
    VEILLE_REPORTS_DIR = Path(os.getenv(
        "VEILLE_REPORTS_DIR", str(BASE_DIR / "reports")
    ))
    VEILLE_DAILY_BATCH = int(os.getenv("VEILLE_DAILY_BATCH", "10"))
    VEILLE_RECHECK_DAYS = int(os.getenv("VEILLE_RECHECK_DAYS", "30"))
    VEILLE_CONCURRENCY = int(os.getenv("VEILLE_CONCURRENCY", "3"))
    VEILLE_CONFIDENCE_MIN = float(os.getenv("VEILLE_CONFIDENCE_MIN", "0.8"))
    VEILLE_PR_MODE = os.getenv("VEILLE_PR_MODE", "off")  # off | draft | ready
    VEILLE_LINK_IGNORE_PATH = Path(os.getenv(
        "VEILLE_LINK_IGNORE_PATH", str(BASE_DIR / "veille-link-ignore.yml")
    ))
    # HTTP : contre-mesures anti-bot (voir agent/tools/http_client.py).
    VEILLE_USER_AGENT = os.getenv("VEILLE_USER_AGENT", "")  # vide = UA par défaut
    VEILLE_HTTP_TIMEOUT_CONNECT = float(os.getenv("VEILLE_HTTP_TIMEOUT_CONNECT", "10"))
    VEILLE_HTTP_TIMEOUT_READ = float(os.getenv("VEILLE_HTTP_TIMEOUT_READ", "30"))
    VEILLE_HTTP_RETRIES = int(os.getenv("VEILLE_HTTP_RETRIES", "3"))
    VEILLE_HTTP_HOST_DELAY = float(os.getenv("VEILLE_HTTP_HOST_DELAY", "1.5"))
    # Git/PR : remote du repo aides-jeunes à pousser (souvent un fork), repo cible
    # de la PR et propriétaire de la branche head (pour PR cross-fork via gh).
    VEILLE_GIT_REMOTE = os.getenv("VEILLE_GIT_REMOTE", "origin")
    # Remote dont partent les branches : doit pointer le repo cible de la PR,
    # sinon les PR sont bâties sur un fork obsolète (correctifs upstream annulés).
    VEILLE_PR_BASE_REMOTE = os.getenv("VEILLE_PR_BASE_REMOTE", "origin")
    VEILLE_PR_REPO = os.getenv("VEILLE_PR_REPO", "")        # ex: betagouv/aides-jeunes
    VEILLE_PR_HEAD = os.getenv("VEILLE_PR_HEAD", "")        # ex: aides-jeunes-bot
    VEILLE_MAX_PR = int(os.getenv("VEILLE_MAX_PR", "20"))   # cap dur de PR par run

settings = Settings()
