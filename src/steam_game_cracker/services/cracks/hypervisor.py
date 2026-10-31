import logging
import re
import shutil
import subprocess
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
from steam_game_cracker.core.utils import copy_file_with_backup, read_json, write_file_with_backup, write_json

logger = logging.getLogger(__name__)

# Standard release naming: <Game>-BuildID<N>[-<Variant>]-<Type>.<ext>
RELEASE_FILENAME_PATTERN = re.compile(
    r"^(?P<game>.+)-BuildID(?P<build_id>\d+)(?:-(?P<variant>[A-Za-z0-9.]+))?-(?P<type>DenuvOwO|ARTIFACT)"
    r"\.(?P<ext>zip|7z|rar|tar(\.gz)?|gz)$",
    re.IGNORECASE,
)

# Selection tiebreak priority when BuildIDs are equal (newer crack family first)
RELEASE_TYPE_PRIORITY = {"artifact": 1, "denuvowo": 0}

# Standard hypervisor layout (provided by the launcher bundle)
HYPERVISOR_DRIVER_DIRS = frozenset({"driver_amd", "driver_intel", "artifact_amd", "artifact_intel"})

# CPU vendor per standard driver directory name (case-insensitive match)
HYPERVISOR_DRIVER_VENDOR = {
    "driver_amd": "amd",
    "artifact_amd": "amd",
    "driver_intel": "intel",
    "artifact_intel": "intel",
}

# Canonical .sys name per vendor, preferred when a release ships more than one
CANONICAL_DRIVER_SYS = {"amd": "simplesvm.sys", "intel": "hyperkd.sys"}

# Crack configuration files (autoload toggling)
CRACK_INI_SUFFIXES = ("denuvowo.ini", "reflex.ini")
CRACK_JSON_NAMES = ("artifact.json",)


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
    """Handles discovery, coercion and deployment of Hypervisor releases (DenuvOwO / ARTIFACT)."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings or {}
        self.verification_results: list[dict[str, Any]] = []
        self._cached_release: tuple[str, str] | None = None

    def get_version(self) -> str:
        """Read the Hypervisor version from the version file, or 'Unknown' if failed."""
        return read_tool_version(config.HYPERVISOR_LAUNCHER_PATH)

    def apply(self, target_dir: str | Path, app_id: int | str) -> list[str]:
        """
        Coerce the selected hypervisor release into the standard structure and deploy it.
        Args:
            target_dir: Target directory (game dir or output dir).
            app_id: Steam App ID.
        Returns:
            Absolute paths of files deployed.
        """
        res = self._find_release(app_id)
        if not res:
            return []

        archive_path, release_type = res
        target = Path(target_dir)

        target_exe_abs = self._resolve_target_exe(target)
        if not target_exe_abs:
            return []

        # 1. Extract payload
        temp_stage = Path(config.TEMP_PATH) / f"hv_stage_{app_id}_{int(time.time())}"
        temp_stage.mkdir(parents=True, exist_ok=True)
        try:
            payload_root = self._find_payload_root(archive_path, release_type)
            if not payload_root:
                return []

            pattern = payload_root.replace("\\", "/") + "/*"
            if not extract_archive_with_backup(archive_path, temp_stage, pattern=pattern):
                logger.error(f"Failed to extract payload {payload_root!r} from {Path(archive_path).name}.")
                return []

            payload = temp_stage / payload_root
            if not payload.is_dir():
                logger.error(f"Payload folder {payload_root!r} missing after extraction.")
                return []

            release_drivers = self._select_driver_sys(payload)
            added = self._deploy_payload(payload, target, release_type, Path(archive_path), payload_root)
        finally:
            shutil.rmtree(temp_stage, ignore_errors=True)

        if not added:
            logger.error(f"No hypervisor payload files were deployed for App ID {app_id}.")
            return []

        # 2. Merge launcher shell
        launcher_src = Path(config.HYPERVISOR_LAUNCHER_PATH)
        if launcher_src.is_dir():
            added.extend(self._merge_launcher(launcher_src, target, release_drivers))
        else:
            logger.error(f"Hypervisor launcher not found at {launcher_src}.")

        # 3. Configure launcher
        launcher_ini = target / "hvlauncher.ini"
        if launcher_ini.exists():
            # Point launcher at correct target
            exe_val = _relpath(target_exe_abs, target)
            self._update_ini_file(launcher_ini, "Target", "exe", exe_val)
            # Point launcher at release's driver sets where it ships them
            for vendor, ini_key in (("amd", "amd_sys"), ("intel", "intel_sys")):
                if vendor in release_drivers:
                    self._update_ini_file(
                        launcher_ini, "Driver", ini_key, release_drivers[vendor], only_if_exists=False
                    )
            added.append(str(launcher_ini.resolve()))

        # 4. Configure autoload settings
        added.extend(self._apply_autoload_settings(added))

        return list(dict.fromkeys(added))

    # --- Helpers ---

    @staticmethod
    def _parse_release_filename(name: str) -> tuple[str, int, bool] | None:
        """
        Strictly parse a canonical hypervisor release filename.
        The name must match <Game>-BuildID<N>[-<Variant>]-<Type>.<ext> with <Type> one of
        'DenuvOwO'/'ARTIFACT' (case-insensitive) and <ext> one of zip/7z/rar/tar(.gz)/gz.
        Returns:
            (release_type, build_id, has_variant) with a lowercase release type, or None when the
            name does not conform to the standard naming.
        """
        match = RELEASE_FILENAME_PATTERN.fullmatch(name)
        if not match:
            return None
        return match["type"].lower(), int(match["build_id"]), match["variant"] is not None

    @staticmethod
    def _classify_release_filename(name: str) -> str | None:
        """Classify a release archive by filename, or None when the name is non-conforming."""
        parsed = HypervisorCrack._parse_release_filename(name)
        return parsed[0] if parsed else None

    @staticmethod
    def _is_hypervisor_driver_path(rel: Path) -> bool:
        """True when a payload-relative path belongs to the release's hypervisor driver set."""
        if rel.suffix.lower() == ".sys":
            return True
        return any(part.lower() in HYPERVISOR_DRIVER_DIRS for part in rel.parts)

    @staticmethod
    def _driver_vendor(rel: Path) -> str | None:
        """
        Attribute a payload-relative path to a CPU vendor driver set ('amd'/'intel'), or None.

        Directory attribution wins (any ancestor named after a driver set); a file that sits
        outside driver directories is attributed by its canonical .sys name.
        """
        for part in rel.parts[:-1]:
            vendor = HYPERVISOR_DRIVER_VENDOR.get(part.lower())
            if vendor:
                return vendor
        name = rel.name.lower()
        if name == CANONICAL_DRIVER_SYS["amd"]:
            return "amd"
        if name == CANONICAL_DRIVER_SYS["intel"]:
            return "intel"
        return None

    def _resolve_target_exe(self, target: Path) -> Path | None:
        """Resolve the target game executable, honoring the exe_override setting."""
        override = str(self.settings.get("exe_override") or "").strip()
        if override:
            override_p = Path(override)
            if not override_p.is_absolute():
                override_p = target / override_p
            if override_p.is_file():
                return override_p.resolve()
            logger.error(f"Configured exe_override does not exist: {override_p}")
            return None

        target_exe_rel = get_launcher_rel_path(target)
        if not target_exe_rel:
            logger.error(f"Could not identify target game executable in {target}.")
            return None

        return (target / target_exe_rel).resolve()

    def _deploy_payload(
        self,
        payload: Path,
        target: Path,
        release_type: str,
        archive_path: Path,
        payload_root: str,
    ) -> list[str]:
        """
        Merge the extracted payload into the target root, preserving its relative layout.
        Hypervisor driver files are deployed as-is; the launcher's [Driver] settings are
        pointed at the release's .sys files, and the launcher bundle only provides driver
        sets that the release does not ship.

        When a staged file cannot be copied (e.g. deleted by an antivirus in the temp dir),
        the entry is streamed straight from the archive instead.
        """
        added: list[str] = []
        driver_count = 0
        root_p = Path(payload_root.replace("\\", "/"))

        for item_p in sorted(payload.rglob("*")):
            if not item_p.is_file():
                continue
            rel_p = item_p.relative_to(payload)
            if self._is_hypervisor_driver_path(rel_p):
                driver_count += 1
            dst_dir = target / rel_p.parent
            dst_dir.mkdir(parents=True, exist_ok=True)
            try:
                added.extend(copy_file_with_backup(item_p, dst_dir))
                continue
            except OSError as e:
                logger.warning(f"Staged copy unavailable for {rel_p.as_posix()}: {e}")

            data = self._stream_archive_entry(archive_path, root_p / rel_p)
            if data is None:
                logger.error(f"Failed to deploy {rel_p.as_posix()} from {archive_path.name}.")
                continue
            added.extend(write_file_with_backup(data, dst_dir, rel_p.name))

        if driver_count:
            logger.info(f"Deployed {driver_count} hypervisor driver file(s) from the {release_type} release.")

        return added

    @staticmethod
    def _stream_archive_entry(archive_path: Path, entry: Path) -> bytes | None:
        """Stream a single archive entry as raw bytes, bypassing the staged temp copy.

        Returns the entry content, or None when the entry cannot be read from the archive.
        """
        zip7_p = Path(config.settings.zip7_path)
        if not zip7_p.exists():
            logger.error(f"7-Zip executable not found at {zip7_p}.")
            return None

        entry_str = entry.as_posix().replace("/", "\\")
        try:
            res = subprocess.run(
                [str(zip7_p), "e", "-y", "-so", str(archive_path), entry_str],
                capture_output=True,
                check=False,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=300,
            )
        except (subprocess.SubprocessError, OSError) as e:
            logger.error(f"Failed to stream {entry_str} from {archive_path.name}: {e}")
            return None

        if res.returncode != 0:
            logger.error(
                f"Failed to stream {entry_str} from {archive_path.name}: "
                f"{res.stderr.decode('utf-8', errors='replace').strip()}"
            )
            return None
        return res.stdout

    @staticmethod
    def _select_driver_sys(payload: Path) -> dict[str, str]:
        """Pick the .sys the launcher should load per CPU vendor from the extracted payload.

        Returns a vendor ('amd'/'intel') to payload-relative .sys path (backslash form) mapping.
        Vendors without a .sys in the payload are omitted: the launcher bundle provides the
        standard fallback driver set for those.
        """
        candidates: dict[str, list[Path]] = {"amd": [], "intel": []}
        for item_p in sorted(payload.rglob("*")):
            if not item_p.is_file() or item_p.suffix.lower() != ".sys":
                continue
            vendor = HypervisorCrack._driver_vendor(item_p.relative_to(payload))
            if vendor:
                candidates[vendor].append(item_p.relative_to(payload))

        selected: dict[str, str] = {}
        for vendor, rels in candidates.items():
            if not rels:
                continue
            canonical = CANONICAL_DRIVER_SYS[vendor]
            match = [rel for rel in rels if rel.name.lower() == canonical]
            selected[vendor] = (match[0] if match else rels[0]).as_posix().replace("/", "\\")
        return selected

    @staticmethod
    def _merge_launcher(launcher_src: Path, target: Path, release_drivers: dict[str, str]) -> list[str]:
        """
        Merge the launcher shell into the target root.
        Bundled driver directories already covered by the release (per CPU vendor) are not
        copied, so the release's own driver set stays the only set for that vendor.
        """
        added: list[str] = []
        for item_p in sorted(launcher_src.rglob("*")):
            if not item_p.is_file():
                continue
            rel_p = item_p.relative_to(launcher_src)
            vendor = HypervisorCrack._driver_vendor(rel_p)
            if vendor is not None and vendor in release_drivers:
                continue
            dst_dir = target / rel_p.parent
            dst_dir.mkdir(parents=True, exist_ok=True)
            added.extend(copy_file_with_backup(item_p, dst_dir))
        return added

    def _apply_autoload_settings(self, deployed: list[str]) -> list[str]:
        """Apply the auto_load_hv setting to deployed crack configuration files."""
        auto_load = bool(self.settings.get("auto_load_hv"))
        configured: list[str] = []

        for deployed_path in deployed:
            name = Path(deployed_path).name.lower()
            if name.endswith(CRACK_INI_SUFFIXES):
                auto_str = "true" if auto_load else "false"
                # Autoload config setting can be named AutoLoadHV or autoload
                self._update_ini_file(deployed_path, "Config", "AutoLoadHV", auto_str)
                self._update_ini_file(deployed_path, "Config", "autoload", auto_str)
                configured.append(deployed_path)
            elif name in CRACK_JSON_NAMES:
                if self._update_json_key(deployed_path, "autoload", auto_load):
                    configured.append(deployed_path)

        return configured

    @staticmethod
    def _update_json_key(file_path: str | Path, key: str, value: Any, only_if_exists: bool = True) -> bool:
        """Update a top-level key in a JSON file, preserving the rest of the document."""
        data = read_json(file_path)
        if not isinstance(data, dict):
            return False
        if key not in data and only_if_exists:
            return False
        data[key] = value
        return write_json(file_path, data)

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
            # Key not found in the section: append after section header
            result = []
            for line in new_lines:
                result.append(line)
                sec_match = section_pattern.match(line.strip())
                if sec_match and sec_match.group(1).strip().lower() == section.lower():
                    result.append(f"{key}={value}\n")
            new_lines = result

        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

    def _find_release(self, app_id: int | str) -> tuple[str, str] | None:
        """Locate the latest hypervisor release archive and its type."""
        if self._cached_release:
            return self._cached_release

        app_dir = Path(config.HYPERVISOR_CRACKS_PATH) / str(app_id)
        if not app_dir.is_dir():
            return None

        # Collect releases
        releases: list[tuple[int, int, int, str, str]] = []
        for entry in app_dir.iterdir():
            if not entry.is_file():
                continue

            # Include if conforms to standard release naming
            parsed = self._parse_release_filename(entry.name)
            if parsed is not None:
                release_type, build_id, has_variant = parsed
                priority = RELEASE_TYPE_PRIORITY[release_type]
                releases.append((build_id, priority, int(has_variant), str(entry), release_type))
                continue

            # .sha256 sidecar is a recognized package component
            base_name = entry.name[: -len(".sha256")]
            if entry.name.endswith(".sha256") and self._parse_release_filename(base_name) is not None:
                continue

            # Anything else is non-conforming: skipped and logged
            logger.warning(
                f"Ignoring '{entry.name}': does not match the standard release naming "
                "<Game>-BuildID<N>[-<Variant>]-<Type>.<ext>."
            )

        if not releases:
            return None

        # Latest BuildID wins; on ties: newer release family first, variant release over base, then name
        best = max(releases, key=lambda item: (item[0], item[1], item[2], item[3]))
        archive_path, release_type = best[3], best[4]

        # Locate payload root folder in archive
        if not config.settings.zip7_path.exists():
            logger.error("7-Zip not found. Cannot search archive.")
            return None

        payload_root = self._find_payload_root(archive_path, release_type)
        if not payload_root:
            logger.error(f"No {release_type} payload folder found in {Path(archive_path).name}.")
            return None

        # Verify before caching so a failed checksum is never selectable without re-verification
        verify_archive_hash(archive_path, self.verification_results)
        self._cached_release = (archive_path, release_type)

        return self._cached_release

    def _find_payload_root(self, archive_path: str, release_type: str) -> str | None:
        """Find the payload root folder inside the archive (named after the release type)."""
        try:
            internal_paths = get_archive_file_list(archive_path)
        except Exception as e:
            logger.error(f"Failed to scan archive {archive_path}: {e}")
            return None

        wanted = release_type.lower()

        # Exact top-level folder match first (e.g. 'DenuvOwO', 'ARTIFACT')
        for path_val in internal_paths:
            parts = [p for p in path_val.replace("\\", "/").split("/") if p]
            if len(parts) == 1 and parts[0].lower() == wanted:
                return path_val

        # Fallback: any path whose final component matches the type name
        for path_val in internal_paths:
            if path_val.lower().endswith(wanted):
                return path_val

        return None
