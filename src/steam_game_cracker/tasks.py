import logging
from pathlib import Path
from typing import Any

from steam_game_cracker.core import config
from steam_game_cracker.core.helpers import TOGGLE_KEYS, load_settings, resolve_game_path
from steam_game_cracker.core.utils import read_json
from steam_game_cracker.services.cracker_service import CrackerService
from steam_game_cracker.services.firewall_service import FirewallService
from steam_game_cracker.services.library_scanner import LibraryScanner

logger = logging.getLogger(__name__)


# --- Crack Tasks ---


def run_apply_crack(
    target: str | Path,
    app_id: int | str,
    overrides: dict[str, Any] | None = None,
    label: str | None = None,
) -> None:
    """Apply a crack to a single game directory."""
    target_path = Path(target)
    name = label or target_path.name or str(app_id)

    if not target_path.exists():
        logger.error(f"Game folder not found: {target_path}")
        return

    logger.info(f"Applying crack to {name}...")
    try:
        settings = load_settings(config.SETTINGS_JSON_PATH, app_id=app_id, overrides=overrides)
        CrackerService(settings).crack(target_path, int(app_id))
        logger.info("Crack operation complete")
    except Exception as e:
        logger.exception(f"Crack failed for {name}")
        logger.error(f"Crack failed: {e}")
        raise


def run_restore_crack(target: str | Path, label: str | None = None) -> None:
    """Remove a crack from a single game directory."""
    target_path = Path(target)
    name = label or target_path.name

    if not target_path.exists():
        logger.error(f"Game folder not found: {target_path}")
        return

    logger.info(f"Removing crack from {name}...")
    try:
        settings = load_settings(config.SETTINGS_JSON_PATH)
        CrackerService(settings).restore(target_path)
        logger.info("Restoration complete!")
    except Exception as e:
        logger.exception(f"Restore failed for {name}")
        logger.error(f"Restore failed: {e}")


# --- Firewall Tasks ---


def run_apply_firewall(target: str | Path, label: str | None = None) -> None:
    """Add firewall rules for a single game directory."""
    target_path = Path(target)
    name = label or target_path.name

    if not target_path.exists():
        logger.error(f"Game folder not found: {target_path}")
        return

    logger.info(f"Adding firewall rules for {name}...")
    try:
        FirewallService().apply_rules(target_path)
        logger.info("Firewall rules added!")
    except Exception as e:
        logger.exception(f"Firewall apply failed for {name}")
        logger.error(f"Failed: {e}")


def run_remove_firewall(target: str | Path, label: str | None = None) -> None:
    """Remove firewall rules for a single game directory."""
    target_path = Path(target)
    name = label or target_path.name

    if not target_path.exists():
        logger.error(f"Game folder not found: {target_path}")
        return

    logger.info(f"Removing firewall rules for {name}...")
    try:
        FirewallService().remove_rules(target_path)
        logger.info("Firewall rules removed!")
    except Exception as e:
        logger.exception(f"Firewall remove failed for {name}")
        logger.error(f"Failed: {e}")


def run_refresh_firewall(target: str | Path, label: str | None = None) -> None:
    """Refresh firewall rules for a single game directory."""
    target_path = Path(target)
    name = label or target_path.name

    if not target_path.exists():
        logger.error(f"Game folder not found: {target_path}")
        return

    logger.info(f"Refreshing firewall rules for {name}...")
    try:
        FirewallService().refresh_rules(target_path)
        logger.info("Firewall rules refreshed!")
    except Exception as e:
        logger.exception(f"Firewall refresh failed for {name}")
        logger.error(f"Failed: {e}")


# --- Bulk Tasks ---


def run_crack_all() -> tuple[int, int]:
    """Apply cracks to all configured games, returning (ok, failed) counts."""
    logger.info("Running bulk crack for all config games...")
    cfg = read_json(config.GAMES_JSON_PATH) or {}

    ok, fail = 0, 0
    for key, c in cfg.items():
        try:
            target = resolve_game_path(c.get("game_folder"))
            if not target or not target.is_dir():
                logger.warning(f"Skipped (folder not found): {key}")
                continue

            logger.info(f"Processing: {key}...")
            app_id = str(c.get("app_id", key))

            overrides = {k: v for k, v in c.items() if v is not None and k in TOGGLE_KEYS}

            run_apply_crack(target, app_id, overrides=overrides, label=key)
            logger.info(f"OK: {key}")
            ok += 1
        except Exception as e:
            logger.error(f"Failed to crack {key}: {e}")
            fail += 1

    logger.info(f"Bulk crack complete. {ok} OK, {fail} Failed.")
    return ok, fail


def run_restore_all() -> tuple[int, int]:
    """Remove cracks from all configured games, returning (ok, failed) counts."""
    logger.info("Running bulk restore for all config games...")
    cfg = read_json(config.GAMES_JSON_PATH) or {}

    ok, fail = 0, 0
    for key, c in cfg.items():
        try:
            target = resolve_game_path(c.get("game_folder"))
            if not target or not target.is_dir():
                logger.warning(f"Skipped (folder not found): {key}")
                continue

            logger.info(f"Restoring: {key}...")
            run_restore_crack(target, label=key)
            logger.info(f"OK: {key}")
            ok += 1
        except Exception as e:
            logger.error(f"Failed to restore {key}: {e}")
            fail += 1

    logger.info(f"Bulk restore complete. {ok} OK, {fail} Failed.")
    return ok, fail


# --- Library Tasks ---


def run_scan_library() -> tuple[int, int, list[str]]:
    """Scan game root directories and sync new games into games.json."""
    logger.info("Scanning game root directories for unmapped games...")
    added, skipped, unmapped = LibraryScanner().scan_and_sync()
    logger.info(f"Scan complete. {added} added, {skipped} skipped, {len(unmapped)} unmapped.")
    return added, skipped, unmapped
