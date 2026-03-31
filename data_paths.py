from pathlib import Path


APP_ROOT = Path(__file__).resolve().parent
USERDATA_ROOT = APP_ROOT / "lavuserdata"

MEMORY_DIR = USERDATA_ROOT / "lavender_memory"
MEMORY_BACKUPS_DIR = MEMORY_DIR / "backups"
IMAGES_DIR = USERDATA_ROOT / "lavender_images"
SELF_IMAGE_DIR = USERDATA_ROOT / "self_image"

MEMORY_DB_PATH = MEMORY_DIR / "lavender_memory.db"
USER_DB_PATH = USERDATA_ROOT / "user.db"
FAVORITES_PATH = USERDATA_ROOT / "favorites.json"
USER_ENV_PATH = USERDATA_ROOT / ".env"


def ensure_userdata_dirs() -> None:
    USERDATA_ROOT.mkdir(parents=True, exist_ok=True)
    MEMORY_BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    SELF_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
