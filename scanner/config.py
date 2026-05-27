from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "market.duckdb"

MIN_HISTORY_DAYS = 260
DEFAULT_HISTORY_DAYS = 300
