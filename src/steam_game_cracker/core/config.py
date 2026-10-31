import logging
import os
from dataclasses import dataclass, fields
from pathlib import Path

from dotenv import load_dotenv

from steam_game_cracker.core.utils import read_toml
from steam_game_cracker.models import FileConfig

logger = logging.getLogger(__name__)

PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]

# Load environment overrides
env_file: Path = PROJECT_ROOT / ".env"
if env_file.exists():
    load_dotenv(env_file)

# Project directories
GAMES_JSON_PATH: Path = PROJECT_ROOT / "games.json"
SETTINGS_JSON_PATH: Path = PROJECT_ROOT / "settings.json"

OVERRIDES_PATH: Path = PROJECT_ROOT / "overrides"
TEMP_PATH: Path = PROJECT_ROOT / "temp"

# Emulator paths
GOLDBERG_EMU_PATH: Path = PROJECT_ROOT / "bin" / "Goldberg Emu (Fork)" / "Goldberg Emu"
RUNE_EMU_PATH: Path = PROJECT_ROOT / "bin" / "RUNE Emulator" / "RUNE Emulator"

# Crack paths
STEAMLESS_PATH: Path = PROJECT_ROOT / "bin" / "Steamless" / "Steamless"
HYPERVISOR_LAUNCHER_PATH: Path = PROJECT_ROOT / "bin" / "HypervisorLauncher" / "HypervisorLauncher"
HYPERVISOR_CRACKS_PATH: Path = PROJECT_ROOT / "hypervisor"

# Internal tool paths
FW_BLOCKER_PATH: Path = PROJECT_ROOT / "fw-blocker" / "Game-Firewall-Blocker.ps1"


def _load_file_config() -> FileConfig:
    """Read config.toml and coerce contents into a FileConfig model."""
    data = read_toml(PROJECT_ROOT / "config.toml") or {}
    if not isinstance(data, dict):
        logger.warning("config.toml did not contain a table; using defaults.")
        data = {}

    known = {f.name for f in fields(FileConfig)}
    for key in data:
        if key not in known:
            logger.warning(f"Ignoring unknown config.toml key '{key}'.")

    return FileConfig.from_dict(data)


@dataclass(frozen=True)
class Settings:
    """All runtime configuration settings, loaded from .env, config.toml, and defaults."""

    # Credentials
    steam_username: str

    # Paths & Config
    zip7_path: Path
    game_root_dirs: tuple[Path, ...]
    crack_info_filename: str

    @classmethod
    def load(cls, file: FileConfig) -> "Settings":
        """Build runtime settings: environment variables win, then config.toml, then defaults."""
        raw_env_dirs = os.getenv("GAME_ROOT_DIRS", "")
        if raw_env_dirs.strip():
            dirs = [Path(p.strip()) for p in raw_env_dirs.split(",") if p.strip()]
        elif file.game_root_dirs:
            dirs = [Path(d) for d in file.game_root_dirs]
        else:
            dirs = []

        zip7_raw = os.getenv("ZIP7_PATH") or file.zip7_path
        crack_info_filename = os.getenv("CRACK_INFO_FILENAME") or file.crack_info_filename

        return cls(
            steam_username=os.getenv("STEAM_USERNAME", ""),
            zip7_path=Path(zip7_raw),
            game_root_dirs=tuple(dirs),
            crack_info_filename=crack_info_filename,
        )


settings: Settings = Settings.load(_load_file_config())
