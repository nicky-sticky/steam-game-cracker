import logging
from pathlib import Path

from steam_game_cracker.core import config
from steam_game_cracker.core.utils import read_json, write_json
from steam_game_cracker.services.steam.steam_meta import SteamMetadataClient

logger = logging.getLogger(__name__)


class LibraryScanner:
    """Discovers unmapped game directories and registers them in games.json."""

    def __init__(self, games_json_path: Path | None = None) -> None:
        self.games_json_path = games_json_path or config.GAMES_JSON_PATH

    def scan_and_sync(self) -> tuple[int, int, list[str]]:
        """
        Scan all root directories for unmapped game folders, resolve their App IDs via Steam,
        and update games.json.

        Returns:
            (added_count, skipped_count, unmapped_list)
        """
        games_cfg = read_json(self.games_json_path) or {}

        # Collect existing game folder names (case-folded for fuzzy comparison)
        existing_folders = {
            entry.get("game_folder", "").lower(): app_id
            for app_id, entry in games_cfg.items()
            if isinstance(entry, dict)
        }

        root_dirs = config.settings.game_root_dirs
        if not root_dirs:
            logger.warning("No game_root_dirs configured. Skipping library scan.")
            return 0, 0, []

        added_count = 0
        skipped_count = 0
        unmapped: list[str] = []

        for root in root_dirs:
            root_p = Path(root)
            if not root_p.exists() or not root_p.is_dir():
                logger.warning(f"Game root directory does not exist: {root_p}")
                continue

            logger.info(f"Scanning directory: {root_p}...")
            for item in root_p.iterdir():
                if not item.is_dir():
                    continue

                folder_name = item.name
                if folder_name.lower() in existing_folders:
                    logger.debug(f"Game folder already mapped: {folder_name}")
                    skipped_count += 1
                    continue

                logger.info(f"Unmapped folder discovered: '{folder_name}'. Querying Steam Store...")
                try:
                    result = SteamMetadataClient.search_store_by_name(folder_name)
                except ConnectionError as e:
                    logger.warning(
                        f"[-] Network connection error while searching Steam Store API: {e}. "
                        "Aborting further web queries."
                    )
                    unmapped.append(folder_name)
                    break

                if result:
                    app_id, official_name = result
                    app_id_str = str(app_id)

                    # Update in-memory dict
                    games_cfg[app_id_str] = {"app_name": official_name, "game_folder": folder_name}

                    existing_folders[folder_name.lower()] = app_id_str

                    logger.info(f"[+] Auto-added game: {official_name} (App ID {app_id_str}) -> {folder_name}")
                    added_count += 1
                else:
                    logger.warning(f"[-] Could not resolve Steam App ID for folder: '{folder_name}'")
                    unmapped.append(folder_name)

        if added_count > 0:
            write_json(self.games_json_path, games_cfg)
            logger.info(f"Successfully updated games.json with {added_count} newly discovered game(s).")

        return added_count, skipped_count, unmapped
