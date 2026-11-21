"""Repository data locations shared by the localization app and its tools."""

from pathlib import Path


DATA_DIR = Path(__file__).resolve().parents[2] / "data"
MAPS_DIR = DATA_DIR / "maps"
EXPERIMENTS_DIR = DATA_DIR / "experiments" / "localization"
