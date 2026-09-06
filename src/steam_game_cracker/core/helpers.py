import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from steam_game_cracker.core import config
from steam_game_cracker.core.utils import (
    backup_file,
    calculate_sha256,
    deep_merge,
    read_json,
)
from steam_game_cracker.models import MasterSettings

logger = logging.getLogger(__name__)


class ChecksumMismatchError(ValueError):
    """Raised when an archive's calculated SHA256 does not match its expected checksum file."""


TOGGLE_KEYS = (
    "apply_emu",
    "apply_steam_stub_unpacker",
    "apply_hypervisor",
    "copy_extra_files",
    "generate_crack_only",
)


def load_settings(
    settings_path: str | Path,
    app_id: int | str | None = None,
    overrides: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> MasterSettings:
    """
    Load master settings, deep merging App ID and dynamic overrides.
    Args:
        settings_path: Path to the settings.json file.
        app_id: Steam Application ID for overrides lookup.
        overrides: Key-value overrides to apply.
    Returns:
        Merged settings object.
    """
    s_path = Path(settings_path)
    if not s_path.exists():
        raise FileNotFoundError(f"Master settings not found: {s_path}")

    settings_dict = read_json(s_path)

    if not settings_dict:
        logger.warning("Settings file is empty or corrupted. Using default fallback configuration.")
        settings_dict = {
            "config_settings": {
                "apply_emu": True,
                "apply_steam_stub_unpacker": True,
                "apply_hypervisor": False,
                "copy_extra_files": False,
                "generate_crack_only": False,
            },
            "emu_settings": {
                "emulator": "Goldberg",
                "rune_settings": {},
                "goldberg_settings": {},
                "fetch_dlcs_from_steam": True,
                "generate_emu_game_info": True,
            },
            "steam_stub_unpacker_settings": {},
            "hypervisor_settings": {},
            "extra_files_settings": {},
            "crack_only_settings": {},
        }

    # 1. Merge App ID overrides from file
    if app_id:
        override_file = Path(config.OVERRIDES_PATH) / f"{app_id}.json"
        if override_file.exists():
            app_overrides = read_json(override_file)
            if app_overrides:
                deep_merge(settings_dict, app_overrides)

    # 2. Deep-merge dynamic CLI / dict overrides
    if overrides:
        override_list = overrides if isinstance(overrides, list) else [overrides]
        for override in override_list:
            if isinstance(override, dict):
                deep_merge(settings_dict, override)

    # 3. Fold top-level snake_case toggle overrides into the config_settings block
    config_block = settings_dict.setdefault("config_settings", {})
    for key in TOGGLE_KEYS:
        if key in settings_dict:
            config_block[key] = settings_dict[key]

    return MasterSettings.from_dict(settings_dict)


def read_tool_version(release_path: str | Path) -> str:
    """Read the tool version from the version file relative to its release path."""
    version_path = Path(release_path).parent / "current_version"
    if version_path.exists():
        try:
            with version_path.open("r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return "Unknown"


def resolve_game_path(game_folder: str | Path | None, silent: bool = False) -> Path | None:
    """Search for a game folder across all root directories."""
    if not game_folder:
        return None

    game_folder_name = Path(game_folder).name if isinstance(game_folder, Path) else str(game_folder)

    for root in config.settings.game_root_dirs:
        root_p = Path(root)
        if not root_p.exists():
            continue

        full_path = root_p / game_folder_name
        if full_path.is_dir():
            logger.debug(f"Resolved game path: {full_path}")
            return full_path

    if not silent:
        logger.warning(f"Could not resolve game path for folder: {game_folder}")

    return None


def get_launcher_rel_path(game_dir: str | Path) -> str | None:
    """Find the most likely relative launcher path within a game directory."""
    skip_list = [
        "RapidCRC.exe",
        "unins000.exe",
        "BsSndRpt64.exe",
        "BugSplatHD64.exe",
        "UnityCrashHandler64.exe",
        "UnityCrashHandler32.exe",
        "DXSETUP.exe",
        "launcher.exe",
    ]

    def is_skipped(p: Path) -> bool:
        return any(p.name.lower() == skip.lower() for skip in skip_list)

    game_path = Path(game_dir)
    if not game_path.is_dir():
        return None

    # 1. Largest EXE in the root
    root_exes = [f for f in game_path.glob("*.exe") if not is_skipped(f)]
    if root_exes:
        root_exes.sort(key=lambda x: x.stat().st_size, reverse=True)
        return str(root_exes[0].relative_to(game_path))

    # 2. Largest EXE in the entire tree (fallback)
    all_exes = [p for p in game_path.rglob("*.exe") if p.is_file() and not is_skipped(p)]

    if all_exes:
        all_exes.sort(key=lambda x: x.stat().st_size, reverse=True)
        return str(all_exes[0].relative_to(game_path))

    return None


def verify_archive_hash(
    src_path: str | Path,
    verification_results: list[dict[str, Any]] | None = None,
) -> bool:
    """Verify the SHA256 of an archive if a .sha256 file exists."""
    src_p = Path(src_path)
    filename = src_p.name
    abs_path = str(src_p.resolve())

    def record(status: str, hash_val: str | None = None) -> None:
        if verification_results is not None:
            verification_results.append({"file": filename, "status": status, "hash": hash_val, "path": abs_path})

    # 1. Check existing state (Idempotency)
    if verification_results is not None:
        existing = next((v for v in verification_results if v.get("path") == abs_path), None)
        if existing:
            if existing.get("status") == "Failed":
                raise ChecksumMismatchError(f"SHA256 checksum mismatch for {filename}")
            return True

    # 2. Check for bypass condition
    hash_file = src_p.with_suffix(src_p.suffix + ".sha256")
    if not hash_file.exists():
        logger.debug(f"No .sha256 file found for {filename}. Skipping.")
        record("Unverified")
        return True

    # 3. Delegate execution
    logger.info(f"Verifying SHA256 for {filename}...")
    try:
        actual_hash = check_archive_hash(src_p)
        logger.info("SHA256 verified successfully.")
        record("Verified", actual_hash)
        return True
    except Exception as e:
        record("Failed")
        if isinstance(e, ValueError):
            raise
        logger.error(f"Failed to verify hash for {src_p}: {e}")
        raise ValueError(f"Failed to verify hash for {src_p}: {e}") from e


def check_archive_hash(src_path: str | Path) -> str:
    """Read the associated .sha256 file and compare it against the calculated hash."""
    src_p = Path(src_path)
    filename = src_p.name
    hash_file = src_p.with_suffix(src_p.suffix + ".sha256")

    with hash_file.open("r", encoding="utf-8") as f:
        content = f.read().strip()
        match = re.search(r"([a-fA-F0-9]{64})", content)
        if not match:
            raise ValueError(f"Could not find valid SHA256 in {hash_file}")

    expected_hash = match.group(1).lower()
    actual_hash = calculate_sha256(src_p).lower()

    if actual_hash != expected_hash:
        logger.error(f"SHA256 MISMATCH for {filename}!")
        logger.error(f"Expected: {expected_hash}")
        logger.error(f"Actual:   {actual_hash}")
        raise ValueError(f"SHA256 checksum mismatch for {filename}")

    return actual_hash


def extract_archive_with_backup(
    src_path: str | Path,
    dst_dir: str | Path,
    pattern: str = "*",
) -> list[str]:
    """Extract an archive using 7-Zip, backing up any pre-existing files first."""
    src_p = Path(src_path)
    dst_p = Path(dst_dir)

    added_files: list[str] = []
    tracking_files: list[str] = []

    internal_paths = get_archive_file_list(src_p)
    for relative in internal_paths:
        if pattern != "*" and not relative.lower().replace("\\", "/").startswith(pattern.lower().replace("*", "")):
            continue

        full_path = dst_p / relative
        if not full_path.is_dir():
            bak = backup_file(full_path)
            if bak:
                added_files.append(str(bak.resolve()))
            tracking_files.append(str(full_path.resolve()))

    logger.info(f"Extracting archive: {src_p.name} (Pattern: {pattern})")
    try:
        run_7z(["x", str(src_p), f"-o{dst_p}", "-y", pattern], timeout=300)
        added_files.extend(tracking_files)
        return added_files
    except Exception as e:
        logger.error(f"Extraction failed for {src_p}: {e}")
        return []


def get_archive_file_list(src_path: str | Path) -> list[str]:
    """Get a list of all file paths inside an archive using 7-Zip."""
    src_p = Path(src_path)
    try:
        listing = run_7z(["l", "-slt", str(src_p)], timeout=30)

        paths: list[str] = []
        for line in listing.stdout.splitlines():
            if line.startswith("Path = ") and not line.endswith(src_p.name):
                relative = line[7:].replace("/", "\\")
                if relative:
                    paths.append(relative)
        return paths
    except Exception as e:
        logger.error(f"Failed to list archive {src_p}: {e}")
        return []


def run_7z(
    args: list[str],
    cwd: str | Path | None = None,
    timeout: int | float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute a 7-Zip command with standard subprocess configuration."""
    zip7_p = Path(config.settings.zip7_path)
    if not zip7_p.exists():
        raise FileNotFoundError(f"7-Zip executable not found at {zip7_p}")

    return subprocess.run(
        [str(zip7_p), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=timeout,
    )
