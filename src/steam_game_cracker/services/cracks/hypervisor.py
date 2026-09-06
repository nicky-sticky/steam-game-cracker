import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any

from steam_game_cracker.core import config
from steam_game_cracker.core.helpers import (
    extract_archive_with_backup,
    get_archive_file_list,
    get_launcher_rel_path,
    read_tool_version,
    verify_archive_hash,
)
from steam_game_cracker.core.utils import copy_dir_with_backup

logger = logging.getLogger(__name__)


def _relpath(path: Path, base: Path) -> str:
    """Relative path from base to path, walking up with '..' when needed."""
    try:
        return str(path.relative_to(base))
    except ValueError:
        base_abs = base.resolve()
        path_abs = path.resolve()
        ups: list[str] = []
        while not path_abs.is_relative_to(base_abs) and base_abs != base_abs.parent:
            base_abs = base_abs.parent
            ups.append("..")
        if not path_abs.is_relative_to(base_abs):
            return str(path)
        return str(Path(*ups) / path_abs.relative_to(base_abs))


class HypervisorCrack:
    """Handles the discovery and deployment of the Hypervisor (DenuvOwO) crack."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings or {}
        self.verification_results: list[dict[str, Any]] = []
        self._cached_crack: tuple[str, str] | None = None

    def get_version(self) -> str:
        """Read the Hypervisor version from the version file, or 'Unknown' if failed."""
        return read_tool_version(config.HYPERVISOR_LAUNCHER_PATH)

    def apply(self, target_dir: str | Path, app_id: int | str) -> list[str]:
        """
        Deploy the hypervisor crack to the destination.
        Args:
            target_dir: Target directory (game dir or output dir).
            app_id: Steam App ID.
        Returns:
            Absolute paths of files deployed.
        """
        res = self._find_crack(app_id)
        if not res:
            return []

        target = Path(target_dir)
        target_exe_rel = get_launcher_rel_path(target)
        if not target_exe_rel:
            logger.error(f"Could not identify target game executable in {target}.")
            return []

        target_exe_abs = target / target_exe_rel
        added: list[str] = []

        # 1. Deploy payload
        archive_path, internal_subfolder = res

        verify_archive_hash(archive_path, self.verification_results)

        # Stage and copy payload to discard parent folder layout
        temp_stage = Path(config.TEMP_PATH) / f"hv_stage_{app_id}_{int(time.time())}"
        temp_stage.mkdir(parents=True, exist_ok=True)
        try:
            pattern = internal_subfolder.replace("\\", "/") + "/*"
            if extract_archive_with_backup(archive_path, temp_stage, pattern=pattern):
                src_folder = temp_stage / internal_subfolder
                if src_folder.is_dir():
                    added.extend(copy_dir_with_backup(src_folder, target))
        finally:
            shutil.rmtree(temp_stage, ignore_errors=True)

        if not added:
            logger.error(f"No hypervisor payload files were deployed for App ID {app_id}.")
            return []

        # 2. Identify active folder and locate crack INI
        active_folder = target
        denuvo_ini_path: str | None = None

        for p in added:
            if not denuvo_ini_path and p.lower().endswith(("denuvowo.ini", "reflex.ini")):
                denuvo_ini_path = p

            if active_folder == target:
                try:
                    rel_p = Path(p).relative_to(target)
                except ValueError:
                    continue
                parts = rel_p.parts
                for driver_dir in ("driver_amd", "driver_intel"):
                    if driver_dir in parts:
                        idx = parts.index(driver_dir)
                        active_folder = target.joinpath(*parts[:idx])
                        break

            if denuvo_ini_path and active_folder != target:
                break

        # 3. Merge launcher shell
        launcher_src = config.HYPERVISOR_LAUNCHER_PATH
        if Path(launcher_src).is_dir():
            added.extend(copy_dir_with_backup(launcher_src, active_folder))

        # 4. Configure launcher
        launcher_ini = active_folder / "launcher.ini"
        if launcher_ini.exists():
            exe_val = _relpath(target_exe_abs, active_folder)
            self._update_ini_file(launcher_ini, "Game", "exe", exe_val)
            added.append(str(launcher_ini.resolve()))

        # 5. Configure crack INI
        if denuvo_ini_path and Path(denuvo_ini_path).exists():
            auto_load = "true" if self.settings.get("auto_load_hv") else "false"
            # Autoload config setting can be named AutoLoadHV or autoload
            self._update_ini_file(denuvo_ini_path, "Config", "AutoLoadHV", auto_load)
            self._update_ini_file(denuvo_ini_path, "Config", "autoload", auto_load)
            added.append(str(Path(denuvo_ini_path).resolve()))

        return list(dict.fromkeys(added))  # Deduplicate while preserving insertion order

    # --- Helpers ---

    @staticmethod
    def _update_ini_file(
        file_path: str | Path, section: str, key: str, value: str, only_if_exists: bool = True
    ) -> None:
        """Update a key in an INI file while attempting to preserve comments."""
        if not Path(file_path).exists():
            return

        with open(file_path, encoding="utf-8") as f:
            lines = f.readlines()

        new_lines = []
        current_section = None
        updated = False
        section_pattern = re.compile(r"^\s*\[([^\]]+)\]")
        key_pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")

        for line in lines:
            stripped = line.strip()
            sec_match = section_pattern.match(stripped)
            if sec_match:
                current_section = sec_match.group(1).strip().lower()

            if current_section == section.lower() and key_pattern.match(line):
                # Preserve leading whitespace
                indent = line[: line.find(key)] if key in line else ""
                new_lines.append(f"{indent}{key}={value}\n")
                updated = True
            else:
                new_lines.append(line)

        if not updated and not only_if_exists:
            # Key not found in the section — append it directly after the section header
            result = []
            for line in new_lines:
                result.append(line)
                sec_match = section_pattern.match(line.strip())
                if sec_match and sec_match.group(1).strip().lower() == section.lower():
                    result.append(f"{key}={value}\n")
            new_lines = result

        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

    def _find_crack(self, app_id: int | str) -> tuple[str, str] | None:
        """Locate the hypervisor crack archive and its internal DenuvOwO path."""
        if self._cached_crack:
            return self._cached_crack

        app_dir = Path(config.HYPERVISOR_CRACKS_PATH) / str(app_id)
        if not app_dir.is_dir():
            return None

        # Find latest archive
        archives: list[tuple[int, str]] = []
        for entry in app_dir.iterdir():
            if (
                entry.is_file()
                and "-denuvowo." in entry.name.lower()
                and entry.name.lower().endswith((".zip", ".7z", ".rar", ".tar", ".gz"))
            ):
                build_id = 0
                match = re.search(r"BuildID(\d+)", entry.name, re.IGNORECASE)
                if match:
                    build_id = int(match.group(1))
                archives.append((build_id, str(entry)))

        if not archives:
            return None

        archives.sort(key=lambda x: (x[0], x[1]), reverse=True)
        archive_path = archives[0][1]

        # Locate DenuvOwO folder in archive
        if not config.settings.zip7_path.exists():
            logger.error("7-Zip not found. Cannot search archive.")
            return None

        try:
            internal_paths = get_archive_file_list(archive_path)
            for path_val in internal_paths:
                if path_val.lower().endswith("denuvowo"):
                    self._cached_crack = (archive_path, path_val)
                    # Record verification status for manifest
                    verify_archive_hash(archive_path, self.verification_results)
                    return self._cached_crack
        except Exception as e:
            logger.error(f"Failed to scan archive {archive_path}: {e}")

        return None
