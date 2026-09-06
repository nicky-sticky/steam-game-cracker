import contextlib
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from steam_game_cracker.core import config
from steam_game_cracker.core.helpers import extract_archive_with_backup, run_7z, verify_archive_hash
from steam_game_cracker.core.utils import (
    copy_file,
    copy_with_backup,
    read_json,
    sanitize_filename,
    write_json,
)
from steam_game_cracker.models import EmulatorType, MasterSettings
from steam_game_cracker.services.cracks.hypervisor import HypervisorCrack
from steam_game_cracker.services.cracks.steam_stub import SteamStubCrack
from steam_game_cracker.services.emulators.goldberg import GoldbergEmulator
from steam_game_cracker.services.emulators.rune import RuneEmulator
from steam_game_cracker.services.scanners.interface import InterfaceScanner
from steam_game_cracker.services.steam.steam_meta import SteamMetadataClient

logger = logging.getLogger(__name__)

STEAM_DLL_NAMES = frozenset(
    [
        "steam_api.dll",
        "steam_api64.dll",
        "steamclient.dll",
        "steamclient64.dll",
    ]
)

EMULATOR_MAP = {
    EmulatorType.GOLDBERG: ("goldberg_settings", GoldbergEmulator),
    EmulatorType.RUNE: ("rune_settings", RuneEmulator),
}


class CrackerService:
    """
    Orchestrates the full cracking pipeline: unpacking, emulator injection,
    extra file injection, packaging, and restoration.
    """

    def __init__(self, full_config: MasterSettings) -> None:
        self.config = full_config

        self.emu_settings = self.config.emu_settings.to_dict()
        self.unpacker_settings = self.config.steam_stub_unpacker_settings
        self.extras_settings = self.config.extra_files_settings
        self.crack_only_settings = self.config.crack_only_settings
        self.hypervisor_settings = self.config.hypervisor_settings
        self.gen_only = bool(self.config.config_settings.generate_crack_only)

        self.modified_files: list[str] = []
        self.applied_services: list[dict[str, str]] = []
        self.verification_results: list[dict[str, Any]] = []

        self.emu_type = self.config.emu_settings.emulator
        self.emulator, self.active_emu_settings = self._init_emulator(self.config.to_dict())
        self.steam_stub = SteamStubCrack(self.unpacker_settings)
        self.interface_scanner = InterfaceScanner()
        self.steam_meta_client = SteamMetadataClient(self.emu_settings)

    def crack(self, target_path: str | Path | None, app_id: int | str) -> None:
        """
        Run the full cracking sequence for a given App ID and target directory.
        Args:
            target_path: Path to the game directory (or None for headless mode).
            app_id: Steam Application ID.
        """
        target = Path(target_path) if target_path else None
        if target and not target.is_dir():
            if self.gen_only:
                logger.warning(f"Target directory not found: {target}. Falling back to Headless mode.")
                target = None
            else:
                raise FileNotFoundError(f"Target directory not found: {target}")

        if self.gen_only and not target:
            mode = "Crack-Only (Headless)"
        elif self.gen_only:
            mode = "Crack-Only"
        else:
            mode = "Direct"
        logger.info(f"Starting crack sequence for App ID: {app_id} (Mode: {mode})")

        try:
            # 0. Validate dependencies
            self._validate_dependencies()

            # 1. Prepare target
            if target:
                self._prepare_target(target)
                # Start fresh: any tracking from the pre-crack cleanup is complete
                self.modified_files = []

            # 2. Metadata
            game_data = self.steam_meta_client.fetch_metadata(app_id)
            dlc_list = self._prepare_dlc_list(game_data.get("dlcs", {}))
            out_dir = self._resolve_output_dir(app_id, self.gen_only)

            # 3. Emulator injection
            if self.config.config_settings.apply_emu and not self.gen_only and target:
                self._apply_emulator(target, app_id, dlc_list)

            # 4. SteamStub unpacking
            unpacked: list[tuple[str, str]] = []
            if self.config.config_settings.apply_steam_stub_unpacker:
                unpacked = self._run_steam_stub_unpacker(target, out_dir)

            # 5. Hypervisor
            if self.config.config_settings.apply_hypervisor:
                self._run_hypervisor(target, app_id, out_dir)

            # 6. Extra files
            if self.config.config_settings.copy_extra_files:
                self._run_extra_files(target, out_dir)

            # 7. Finalise
            if self.gen_only:
                if out_dir is None:
                    raise RuntimeError("Crack-Only mode requires an output directory.")
                dlls = self._find_steam_dlls(target) if target else []
                self._generate_crack_package(
                    target,
                    app_id,
                    dlc_list,
                    game_data.get("name"),
                    unpacked,
                    out_dir,
                    dlls=dlls,
                )
            elif target:
                self._write_crack_info(target, app_id)

            logger.info("Crack sequence complete.")
        except Exception as e:
            if target and not self.gen_only:
                logger.error(f"Crack pipeline failed: {e}. Reverting in-flight changes...")
                try:
                    self._revert(target)
                except Exception as e:
                    logger.error(f"Failed to revert changes: {e}")
            raise

    def restore(self, target_path: str | Path) -> bool:
        """
        Revert a cracked game to its original state using !crack.info metadata.
        Args:
            target_path: Path to the game directory to restore.
        """
        target = Path(target_path)
        if not target.is_dir():
            logger.error(f"Restore failed: Target directory not found: {target}")
            return False

        info_path = target / config.settings.crack_info_filename

        info = read_json(info_path) if info_path.exists() else None
        if not info:
            self._restore_backups(target)
            return True

        logger.info(f"Restoring crack: {info.get('app_id', 'Manual')}...")

        # Restore modified files from backups
        self._restore_backups(target, explicit_files=info.get("modified_files") or [])

        # Remove added files
        added = info.get("added_files") or []
        modified = set(info.get("modified_files") or [])
        added_only = [f for f in added if f not in modified]
        if added_only:
            self._delete_added_files(target_dir=target, added_files=added_only)

        # Remove manifest
        with contextlib.suppress(Exception):
            info_path.unlink()

        logger.info("Restore complete.")
        return True

    # --- Pipeline Execution Helpers ---

    def _init_emulator(self, full_config: dict[str, Any]) -> tuple[GoldbergEmulator | RuneEmulator, dict[str, Any]]:
        """Instantiate the correct emulator service based on config."""
        settings_key, service_cls = EMULATOR_MAP[self.emu_type]
        settings = self.emu_settings.get(settings_key, {})
        return service_cls(full_config), settings

    def _prepare_target(self, target_path: Path) -> None:
        """Ensure the target directory is in a clean state before cracking."""
        if self.gen_only:
            logger.info("Crack-Only mode: Skipping target restoration.")
            return

        if (target_path / config.settings.crack_info_filename).exists():
            logger.info("Previous crack detected. Restoring clean state...")
            self.restore(target_path)
        else:
            # No manifest: attempt a safe restore of known Steam DLLs only
            self._restore_backups(target_path)

    def _resolve_output_dir(self, app_id: int | str, gen_only: bool) -> Path | None:
        """Determine the final output directory, or None when not generating a package."""
        if not gen_only:
            return None

        base_out = self.crack_only_settings.get("output_path", "output/crack")
        out_dir = Path(base_out) / str(app_id)
        if not out_dir.is_absolute():
            out_dir = config.PROJECT_ROOT / out_dir

        # Ensure a clean directory for Crack-Only mode
        if out_dir.is_dir():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        return out_dir

    def _track_modified(self, file_path: str | Path) -> None:
        """Record a replaced file and its .bak sibling for revert/restore tracking."""
        path_str = str(file_path)
        self.modified_files.append(path_str)
        backup_path = path_str + ".bak"
        if Path(backup_path).exists():
            self.modified_files.append(backup_path)

    def _run_steam_stub_unpacker(self, target_path: Path | None, out_dir: Path | None) -> list[tuple[str, str]]:
        """Unpack the SteamStub DRM from the game executable."""
        if not target_path:
            logger.warning("SteamStub unpacking requires a target directory. Skipping in Headless mode.")
            return []

        logger.info("Unpacking SteamStub DRM...")
        results = self.steam_stub.apply(target_path, crack_only=self.gen_only, out_dir=out_dir)

        if results and not self.gen_only:
            for current_file, _ in results:
                self._track_modified(current_file)

            self.applied_services.append({"name": "Steamless", "version": self.steam_stub.get_version()})

        return results

    def _apply_emulator(self, target_path: Path, app_id: int | str, dlc_list: dict[str, str]) -> None:
        """Apply emulator binaries and generate settings for each Steam DLL found."""
        logger.info(f"Applying {self.emu_type} emulator...")
        dlls = self._find_steam_dlls(target_path)

        for dll_path in dlls:
            # Scan interfaces from the original DLL before patching/replacing
            interfaces = self.interface_scanner.scan(dll_path)

            result = self.emulator.apply_to_dll(dll_path, app_id, dlc_list=dlc_list, interfaces=interfaces)
            if isinstance(result, list):
                self.modified_files.extend(result)
            elif result:
                self._track_modified(dll_path)

        if dlls:
            self.applied_services.append({"name": self.emu_type, "version": self.emulator.get_version()})

    def _run_hypervisor(self, target_path: Path | None, app_id: int | str, out_dir: Path | None) -> None:
        """Invoke the hypervisor merge and deployment logic."""
        logger.info("Applying Hypervisor crack...")
        dest_base = out_dir if self.gen_only else target_path
        if not dest_base:
            return

        hv = HypervisorCrack(self.hypervisor_settings)
        hv.verification_results = list(self.verification_results)

        added = hv.apply(dest_base, app_id)

        # Sync verification results back to service
        self.verification_results = hv.verification_results

        if added:
            if not self.gen_only:
                self.modified_files.extend(added)

            self.applied_services.append({"name": "Hypervisor", "version": hv.get_version()})

    def _run_extra_files(self, target_path: Path | None, out_dir: Path | None) -> None:
        """Inject extra files into the target if the feature is enabled."""
        extras_source = self.extras_settings.get("source_path")
        if not extras_source:
            return
        if not target_path and not self.gen_only:
            return

        added = self._apply_extra_files(extras_source, target_path, out_dir=out_dir)

        if added:
            self.applied_services.append({"name": "Extra Files", "version": Path(extras_source).name})

    def _validate_dependencies(self) -> None:
        """Verify all required tools and crack files exist for the enabled modes."""
        # 7-Zip check
        needs_7z = (
            self.config.config_settings.apply_hypervisor
            or (self.config.config_settings.copy_extra_files and self.extras_settings.get("extract_with_7zip", True))
            or (self.gen_only and self.crack_only_settings.get("compress_with_7zip"))
        )

        if needs_7z and not config.settings.zip7_path.exists():
            raise FileNotFoundError(f"7-Zip not found at {config.settings.zip7_path}")

        # Emulator
        if self.config.config_settings.apply_emu:
            emu_path = getattr(self.emulator, "release_path", None)
            if emu_path and not Path(emu_path).is_dir():
                raise FileNotFoundError(f"Emulator path not found: {emu_path}")

        # SteamStub
        if self.config.config_settings.apply_steam_stub_unpacker and not config.STEAMLESS_PATH.exists():
            raise FileNotFoundError(f"Steamless not found at {config.STEAMLESS_PATH}")

        # Hypervisor
        if self.config.config_settings.apply_hypervisor and not Path(config.HYPERVISOR_LAUNCHER_PATH).is_dir():
            raise FileNotFoundError(f"Hypervisor Launcher not found at {config.HYPERVISOR_LAUNCHER_PATH}")

        # Extra Files
        if self.config.config_settings.copy_extra_files:
            source = self.extras_settings.get("source_path")
            source_path = self._resolve_extra_source(source)
            if not source_path or not source_path.exists():
                logger.warning(f"Extra files source not found: {source}. Skipping.")

    # --- Steam Metadata Helpers ---

    def _prepare_dlc_list(self, base_dlcs: dict[str, str]) -> dict[str, str]:
        """Merge Steam-discovered DLCs with any user-specified overrides."""
        dlc_map = base_dlcs.copy()
        for dlc_id, dlc_name in self.active_emu_settings.get("dlc_overrides", {}).items():
            dlc_map[str(dlc_id)] = dlc_name
        return dlc_map

    # --- Crack Packaging & Manifest Helpers ---

    def _generate_crack_package(
        self,
        target_path: Path | None,
        app_id: int | str,
        dlc_list: dict[str, str],
        name: str | None,
        unpacked: list[tuple[str, str]],
        out_dir: Path,
        dlls: list[str] | None = None,
    ) -> None:
        """Assemble a standalone crack package in the output directory."""
        logger.info("Generating crack package...")

        if target_path and dlls:
            # Mode A: Template-based (we have the original DLLs to patch)
            self._package_dlls(target_path, app_id, dlc_list, out_dir, dlls)
        elif self.config.config_settings.apply_emu:
            # Mode B: Generic (Headless or no DLLs found)
            if not target_path:
                logger.info("Target directory missing. Generating generic emulator files.")
            self.emulator.generate_crack_only(out_dir, app_id, dlc_list=dlc_list, game_name=name)
        elif not target_path:
            logger.info("Target directory missing. Skipping emulator files (apply_emu is false).")

        if unpacked and target_path:
            self._package_unpacked_files(target_path, out_dir, unpacked)

        if self.crack_only_settings.get("compress_with_7zip"):
            self._compress_package(out_dir, name, app_id)

    def _package_dlls(
        self, target_path: Path, app_id: int | str, dlc_list: dict[str, str], out_dir: Path, dlls: list[str]
    ) -> None:
        """Copy each Steam DLL into the crack package and patch it in-place."""
        for dll_path in dlls:
            relative = Path(dll_path).relative_to(target_path)
            destination = out_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)

            # Keep the original as a .bak, then copy fresh for patching
            copy_file(dll_path, destination.with_name(destination.name + ".bak"))
            copy_file(dll_path, destination)

            # Scan interfaces from the original DLL before patching
            interfaces = self.interface_scanner.scan(dll_path)
            self.emulator.apply_to_dll(destination, app_id, dlc_list=dlc_list, interfaces=interfaces)

    def _package_unpacked_files(self, target_path: Path, out_dir: Path, unpacked: list[tuple[str, str]]) -> None:
        """Copy SteamStub-unpacked executables into the crack package."""
        for current_file, original_file in unpacked:
            relative = Path(original_file).relative_to(target_path)
            destination = out_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)

            copy_file(original_file, destination.with_name(destination.name + ".bak"))
            copy_file(current_file, destination)

            if current_file != original_file:
                with contextlib.suppress(Exception):
                    Path(current_file).unlink()

    def _compress_package(self, package_path: Path, name: str | None, app_id: int | str) -> None:
        """Create a 7z archive of the assembled crack package."""
        clean_name = sanitize_filename(name or "Crack")
        archive_name = f"{clean_name}_{app_id}.7z"
        archive_path = package_path.parent / archive_name

        if archive_path.exists():
            archive_path.unlink()

        try:
            run_7z(["a", "-t7z", "-mx=9", str(archive_path), "."], cwd=package_path)
        except Exception as e:
            logger.error(f"Compression failed: {e}")

    @staticmethod
    def _classify_tracked(rel_files: list[str]) -> tuple[set[str], list[str]]:
        """Split relative paths into (modified originals, added files)."""
        # Files that have a .bak sibling were modified; everything else was added
        modified = {f[:-4] for f in rel_files if f.endswith(".bak")}
        added = [f for f in rel_files if not f.endswith(".bak") and f not in modified]
        return modified, added

    def _write_crack_info(self, path: Path, app_id: int | str) -> None:
        """Write a !crack.info file recording all modified and added files."""
        rel_files = self._collect_relative_paths(path)
        modified, added = self._classify_tracked(rel_files)

        info = {
            "app_id": str(app_id),
            "timestamp": datetime.now().isoformat(),
            "applied": self.applied_services,
            "verifications": self.verification_results,
            "modified_files": sorted(modified),
            "added_files": sorted(set(added)),
        }

        write_json(path / config.settings.crack_info_filename, info)

    def _collect_relative_paths(self, base_path: Path) -> list[str]:
        """Build relative file paths from the tracked modifications list."""
        return [str(Path(fp).relative_to(base_path)) for fp in set(self.modified_files)]

    # --- File System & Backup Helpers ---

    def _revert(self, target_path: Path) -> None:
        """Revert in-flight modifications tracked during a failed crack pipeline."""
        if not target_path.is_dir():
            logger.error(f"Revert failed: Target directory not found: {target_path}")
            return

        if not self.modified_files:
            logger.info("No in-flight modifications to revert.")
            return

        logger.info(f"Reverting in-flight changes in {target_path}...")
        rel_files = [str(Path(fp).relative_to(target_path)) for fp in set(self.modified_files)]
        modified, added = self._classify_tracked(rel_files)

        self._restore_backups(target_path, explicit_files=list(modified))
        if added:
            self._delete_added_files(target_dir=target_path, added_files=added)

        self.modified_files = []
        logger.info("Revert complete.")

    def _resolve_extra_source(self, source: str | Path | None) -> Path | None:
        """Resolve an extras source path, rebasing relative paths onto the project root."""
        if not source:
            return None
        src = Path(source)
        if not src.is_absolute():
            src = config.PROJECT_ROOT / src
        return src

    def _apply_extra_files(
        self, source: str | Path, target_path: Path | None, out_dir: Path | None = None
    ) -> list[str]:
        """Resolve and inject a source file or directory into the target."""
        src = self._resolve_extra_source(source)
        if not src or not src.exists():
            logger.error(f"Extra files source not found: {source}")
            return []

        dest_base = out_dir if self.gen_only else target_path
        if not dest_base:
            return []
        dest_base.mkdir(parents=True, exist_ok=True)

        is_archive = src.name.lower().endswith((".zip", ".7z", ".rar", ".tar", ".gz"))
        use_7zip = self.extras_settings.get("extract_with_7zip", True)

        if is_archive and use_7zip and config.settings.zip7_path.exists():
            verify_archive_hash(src, self.verification_results)
            added_files = extract_archive_with_backup(src, dest_base)
        else:
            added_files = copy_with_backup(src, dest_base)

        if not self.gen_only:
            self.modified_files.extend(added_files)
        return added_files

    def _find_steam_dlls(self, folder_path: Path) -> list[str]:
        """Recursively scan a folder for Steam API DLLs."""
        return [str(p) for p in folder_path.rglob("*") if p.is_file() and p.name.lower() in STEAM_DLL_NAMES]

    def _restore_backups(self, target_dir: Path, explicit_files: list[str] | None = None) -> None:
        """Restore .bak files back to their original names."""
        if not target_dir.is_dir():
            return

        if explicit_files:
            logger.info(f"Restoring explicit backups in {target_dir}...")
            backup_files: list[Path] = []
            for rel_path in explicit_files:
                backup_p = (target_dir / rel_path).with_name(Path(rel_path).name + ".bak")
                if backup_p.exists():
                    backup_files.append(backup_p)
        else:
            logger.info(f"Restoring essential backups in {target_dir}...")
            backup_files = [
                p
                for p in target_dir.rglob("*")
                if p.is_file() and p.name.lower().endswith(".bak") and p.name[:-4].lower() in STEAM_DLL_NAMES
            ]

        if not backup_files:
            return

        # Longest paths first so chained backups collapse correctly
        backup_files.sort(key=lambda p: len(str(p)), reverse=True)

        restored_count = 0
        for backup_path in backup_files:
            if not backup_path.exists():
                continue
            original_path = backup_path.with_name(backup_path.name[:-4])
            try:
                if original_path.exists():
                    original_path.chmod(0o777)
                    original_path.unlink()
                shutil.move(backup_path, original_path)
                restored_count += 1
            except Exception as e:
                logger.error(f"Failed to restore {backup_path.name}: {e}")

        if restored_count > 0:
            logger.info(f"Restored {restored_count} files.")

    def _delete_added_files(self, target_dir: Path, added_files: list[str]) -> None:
        """Remove files added during cracking, then prune any empty directories."""
        removed_count = 0

        for relative in added_files:
            full_path = target_dir / relative
            if not full_path.exists():
                continue
            try:
                if full_path.is_dir():
                    shutil.rmtree(full_path)
                else:
                    full_path.unlink()
                removed_count += 1
            except Exception as e:
                logger.debug(f"Failed to remove {full_path}: {e}")

        # Walk upward from each deleted file, removing empty parent directories
        for relative in added_files:
            parent = (target_dir / relative).parent
            while parent != target_dir and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent

        if removed_count > 0:
            logger.info(f"Removed {removed_count} files.")
