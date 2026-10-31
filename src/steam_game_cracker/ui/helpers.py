import logging
from pathlib import Path
from typing import Any

from steam_game_cracker.core import config
from steam_game_cracker.core.helpers import resolve_game_path
from steam_game_cracker.core.utils import read_json

logger = logging.getLogger(__name__)


def scan_installed_games() -> dict[str, dict[str, Any]]:
    """Scan game root directories and map all installed games."""
    installed_map: dict[str, dict[str, Any]] = {}
    for root in config.settings.game_root_dirs:
        root_p = Path(root)
        if not root_p.exists():
            continue
        try:
            for item in root_p.iterdir():
                if item.is_dir():
                    info_p = item / config.settings.crack_info_filename
                    is_cracked = info_p.exists()
                    aid: str | None = None

                    if is_cracked:
                        try:
                            data = read_json(info_p)
                            raw_app_id = data.get("app_id")
                            aid = str(raw_app_id) if raw_app_id else None
                        except Exception:
                            pass

                    installed_map[item.name.lower()] = {
                        "name": item.name,
                        "path": str(item.resolve()),
                        "app_id": aid,
                        "cracked": is_cracked,
                    }
        except Exception:
            pass

    return installed_map


def get_unified_games() -> list[dict[str, Any]]:
    """Unify configured games and scanned installed games into a sorted status list."""
    cfg = read_json(config.GAMES_JSON_PATH) or {}
    installed_map = scan_installed_games()

    unified: list[dict[str, Any]] = []
    processed_folders: set[str] = set()

    for key, cdata in cfg.items():
        folder_name = cdata.get("game_folder", key)
        found = installed_map.get(folder_name.lower())

        if not found:
            resolved_p = resolve_game_path(folder_name, silent=True)
            if resolved_p:
                res_name = resolved_p.name
                found = installed_map.get(res_name.lower())

        if found:
            status = "[green]INSTALLED[/green]"
            status += " [cyan][CRACKED][/cyan]" if found["cracked"] else " [yellow][UNCRACKED][/yellow]"
            unified.append(
                {
                    "name": found["name"],
                    "app_id": str(cdata.get("app_id") or found["app_id"] or key),
                    "path": found["path"],
                    "status": status,
                    "config_data": {
                        **cdata,
                        "app_id": str(cdata.get("app_id") or found["app_id"] or key),
                    },
                }
            )
            processed_folders.add(found["name"].lower())
        else:
            unified.append(
                {
                    "name": folder_name,
                    "app_id": str(cdata.get("app_id", key)),
                    "path": None,
                    "status": "[red]NOT INSTALLED[/red]",
                    "config_data": {**cdata, "app_id": str(cdata.get("app_id", key))},
                }
            )

    for folder_l, info in installed_map.items():
        if folder_l not in processed_folders:
            status = "[green]INSTALLED[/green]"
            status += " [cyan][CRACKED][/cyan]" if info["cracked"] else " [yellow][UNCRACKED][/yellow]"
            unified.append(
                {
                    "name": info["name"],
                    "app_id": info["app_id"] or "0",
                    "path": info["path"],
                    "status": status,
                    "config_data": {
                        "app_id": info["app_id"] or "0",
                        "game_folder": info["name"],
                    },
                }
            )

    return sorted(unified, key=lambda x: x["name"])
