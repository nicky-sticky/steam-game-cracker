import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from steam_game_cracker import tasks
from steam_game_cracker.core import config
from steam_game_cracker.core.helpers import resolve_game_path
from steam_game_cracker.core.logger import setup_logger
from steam_game_cracker.core.utils import enable_ansi_utf8, read_json
from steam_game_cracker.services.firewall_service import FirewallService

enable_ansi_utf8()

logger = logging.getLogger(__name__)


# --- Argument Helpers ---


def str_to_bool(v: Any) -> bool | None:
    """Convert string representation of boolean to actual bool."""
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    if v.lower() in ("yes", "true", "t", "y", "1"):
        return True
    elif v.lower() in ("no", "false", "f", "n", "0"):
        return False
    else:
        raise argparse.ArgumentTypeError("Boolean value expected (true/false).")


def setup_parser() -> argparse.ArgumentParser:
    """Configure the 3-level subcommand structure: [SOURCE] [TOOL] [ACTION]"""
    parser = argparse.ArgumentParser(description="Steam Game Cracker CLI")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )

    sub = parser.add_subparsers(dest="source", required=True)

    # --- SCAN ---
    sub.add_parser("scan", help="Scan game root directories and auto-add missing games to games.json")

    # --- SOURCE: library ---
    library_p = sub.add_parser("library", help="Library game management")
    library_id = library_p.add_mutually_exclusive_group(required=True)
    library_id.add_argument("--app-id", help="Steam App ID from library games.json")
    library_id.add_argument("--all", action="store_true", help="Apply to all games in games.json")

    # --- SOURCE: target ---
    target_p = sub.add_parser("target", help="Ad-hoc folder management")
    target_p.add_argument("--app-id", required=True, help="Steam App ID for this target")
    target_p.add_argument("--game-dir", required=True, help="Direct path to game folder")

    # --- TOOLS ---
    for p in [library_p, target_p]:
        tool_sub = p.add_subparsers(dest="tool", required=True)

        # TOOL: crack
        crack_p = tool_sub.add_parser("crack", help="Crack management")
        action_sub = crack_p.add_subparsers(dest="action", required=True)

        # ACTION: apply
        apply_p = action_sub.add_parser("apply", help="Apply crack to game")
        apply_p.add_argument(
            "--apply-emu",
            type=str_to_bool,
            nargs="?",
            const=True,
            default=None,
            help="Apply emulator (true/false)",
        )
        apply_p.add_argument(
            "--apply-steam-stub",
            type=str_to_bool,
            nargs="?",
            const=True,
            default=None,
            help="Unpack SteamStub (true/false)",
        )
        apply_p.add_argument(
            "--apply-hypervisor",
            type=str_to_bool,
            nargs="?",
            const=True,
            default=None,
            help="Apply hypervisor crack (true/false)",
        )
        apply_p.add_argument(
            "--copy-extra-files",
            type=str_to_bool,
            nargs="?",
            const=True,
            default=None,
            help="Toggle extra file injection (true/false)",
        )
        apply_p.add_argument(
            "--generate-crack-only",
            type=str_to_bool,
            nargs="?",
            const=True,
            default=None,
            help="Generate crack package (true/false)",
        )

        # ACTION: restore
        action_sub.add_parser("restore", help="Restore original game files")

        # TOOL: firewall
        fw_p = tool_sub.add_parser("firewall", help="Firewall management")
        fw_action = fw_p.add_subparsers(dest="action", required=True)

        # ACTION: add
        fw_add = fw_action.add_parser("add", help="Add block rules")
        fw_add.add_argument("--excludes-path", help="Path to custom excludes file")

        # ACTION: refresh
        fw_ref = fw_action.add_parser("refresh", help="Refresh existing rules")
        fw_ref.add_argument("--excludes-path", help="Path to custom excludes file")

        # ACTION: remove
        fw_action.add_parser("remove", help="Remove block rules")

    return parser


def _build_cli_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """Build snake_case toggle overrides from CLI flags, dropping any unset flags."""
    return {
        key: value
        for key, value in {
            "apply_emu": getattr(args, "apply_emu", None),
            "apply_steam_stub_unpacker": getattr(args, "apply_steam_stub", None),
            "apply_hypervisor": getattr(args, "apply_hypervisor", None),
            "copy_extra_files": getattr(args, "copy_extra_files", None),
            "generate_crack_only": getattr(args, "generate_crack_only", None),
        }.items()
        if value is not None
    }


def _run_firewall(target_dir: str | Path, action: str, excludes_path: str | None = None) -> None:
    """Run the requested firewall action, exiting with a failure code on error."""
    service = FirewallService()
    if action == "add":
        success = service.apply_rules(target_dir, excludes_path=excludes_path)
    elif action == "remove":
        success = service.remove_rules(target_dir)
    elif action == "refresh":
        success = service.refresh_rules(target_dir, excludes_path=excludes_path)
    else:
        success = False

    if not success:
        logger.error("Firewall operation failed.")
        sys.exit(1)


# --- CLI Handlers ---


def _handle_scan() -> None:
    """Scan game root directories and sync new games into games.json."""
    tasks.run_scan_library()
    sys.exit(0)


def _handle_crack(args: argparse.Namespace, target: str | Path, label: str | None = None) -> None:
    """Dispatch the crack action for a resolved target."""
    if args.action == "apply":
        tasks.run_apply_crack(target, args.app_id, overrides=_build_cli_overrides(args), label=label)
    elif args.action == "restore":
        tasks.run_restore_crack(target, label=label)


def _handle_library(args: argparse.Namespace, main_cfg: dict[str, Any]) -> None:
    """Handle library source commands over all games or a single App ID."""
    if args.all:
        if args.tool == "crack":
            if args.action == "apply":
                tasks.run_crack_all()
            elif args.action == "restore":
                tasks.run_restore_all()
        elif args.tool == "firewall":
            for app_id, config_entry in main_cfg.items():
                target = resolve_game_path(config_entry.get("game_folder"), silent=True)
                if not target:
                    logger.warning(f"Could not resolve path for App ID: {app_id}")
                    continue
                _run_firewall(target, args.action, getattr(args, "excludes_path", None))
        return

    if args.app_id not in main_cfg:
        logger.error(f"App ID not found in config: {args.app_id}")
        sys.exit(1)

    config_entry = main_cfg[args.app_id]
    target = resolve_game_path(config_entry.get("game_folder"))
    if not target:
        logger.error(f"Could not resolve path for App ID: {args.app_id}")
        sys.exit(1)

    if args.tool == "crack":
        _handle_crack(args, target, label=config_entry.get("game_folder") or args.app_id)
    elif args.tool == "firewall":
        _run_firewall(target, args.action, getattr(args, "excludes_path", None))


def _handle_target(args: argparse.Namespace) -> None:
    """Handle target source commands (ad-hoc folder)."""
    if args.tool == "crack":
        _handle_crack(args, args.game_dir)
    elif args.tool == "firewall":
        _run_firewall(args.game_dir, args.action, getattr(args, "excludes_path", None))


def main() -> None:
    """Parse CLI arguments and dispatch to the requested task."""
    parser = setup_parser()
    args = parser.parse_args()

    setup_logger(verbose=args.verbose)

    main_cfg = read_json(config.GAMES_JSON_PATH) or {}

    try:
        if args.source == "scan":
            _handle_scan()
        elif args.source == "library":
            _handle_library(args, main_cfg)
        elif args.source == "target":
            _handle_target(args)
    except KeyboardInterrupt:
        logger.info("Interrupted.")
        sys.exit(130)
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        if args.verbose:
            logger.exception("Detailed traceback:")
        sys.exit(1)


if __name__ == "__main__":
    main()
