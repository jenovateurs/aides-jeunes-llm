from configs.settings import settings
from pathlib import Path

def test_aides_jeunes_data_path_exists():
    assert settings.AIDES_JEUNES_DATA_PATH.exists(), \
        f"Path not found: {settings.AIDES_JEUNES_DATA_PATH}"

def test_aides_jeunes_institutions_dir():
    institutions_dir = settings.AIDES_JEUNES_DATA_PATH / "institutions"
    assert institutions_dir.exists()

def test_aides_jeunes_benefits_dir():
    benefits_dir = settings.AIDES_JEUNES_DATA_PATH / "benefits" / "javascript"
    assert benefits_dir.exists()

def test_veille_benefits_dirs():
    dirs = settings.VEILLE_BENEFITS_DIRS
    assert [d.name for d in dirs] == ["javascript", "openfisca"]
    for d in dirs:
        assert d.exists(), f"Path not found: {d}"
