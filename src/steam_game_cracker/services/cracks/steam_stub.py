import contextlib
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from steam_game_cracker.core import config
from steam_game_cracker.core.helpers import read_tool_version
from steam_game_cracker.core.utils import backup_file, copy_file

logger = logging.getLogger(__name__)

STEAMLESS_OPTIONS = ["keep_bind", "keep_stub", "realign", "recalc_checksum"]

OPTION_FLAGS = {
    "keep_bind": "--keepbind",
    "keep_stub": "--keepstub",
    "realign": "--realign",
    "recalc_checksum": "--recalc-checksum",
}


class SteamStubCrack:
    """Manages SteamStub DRM unpacking via Steamless CLI."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings or {}
        self.cli_path: Path = config.STEAMLESS_PATH / "Steamless.CLI.exe"

    def get_version(self) -> str:
        """Read the Steamless version from the version file, or 'Unknown' if failed."""
        return read_tool_version(config.STEAMLESS_PATH)

    def apply(
        self,
        target_dir: str | Path,
        crack_only: bool = False,
        out_dir: str | Path | None = None,
    ) -> list[tuple[str, str]]:
        """
        Scan and unpack executables in a directory, or handle a single override.
        Args:
            target_dir: Directory path to scan.
            crack_only: Whether we are in crack-only generation mode.
            out_dir: Output directory for crack-only mode.
        Returns:
            Unpacked file results.
        """
        target = Path(target_dir)
        if not target.is_dir():
            return []

        override = self.settings.get("exe_override")
        if override:
            override_p = Path(override)
            file_path = override_p if override_p.is_absolute() else target / override

            if file_path.exists():
                logger.info(f"Using SteamStub override: {file_path}")
                return self._unpack_file(file_path, crack_only=crack_only, out_dir=out_dir)

            logger.warning(f"SteamStub override not found: {override}")

        logger.info(f"Scanning {target} for SteamStub targets...")

        results: list[tuple[str, str]] = []
        for file_path in target.rglob("*"):
            if not file_path.is_file() or not self._is_candidate_exe(file_path.name):
                continue
            result = self._unpack_file(file_path, crack_only=crack_only, out_dir=out_dir)
            if result:
                results.extend(result)

        return results

    # --- Helpers ---

    def _unpack_file(
        self, file_path: str | Path, crack_only: bool = False, out_dir: str | Path | None = None
    ) -> list[tuple[str, str]]:
        """Attempt to unpack a single executable with Steamless."""
        if not self.cli_path.exists():
            logger.error(f"Steamless CLI not found: {self.cli_path}")
            return []

        logger.info(f"Unpacking: {Path(file_path).name}")

        target_input = Path(file_path)
        if crack_only and out_dir:
            # Work on a copy so the original game files stay untouched
            target_input = Path(out_dir) / target_input.name
            target_input.parent.mkdir(parents=True, exist_ok=True)
            copy_file(file_path, target_input)

        cmd = self._build_command(target_input)

        try:
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=30,
            )
            unpacked_path = target_input.with_name(target_input.name + ".unpacked.exe")

            if unpacked_path.exists():
                return self._handle_unpacked(file_path, target_input, unpacked_path, crack_only)

            self._cleanup_temp_file(file_path, target_input)
            self._log_unpack_result(process.stdout, file_path)

        except Exception as e:
            logger.error(f"Steamless error on {file_path}: {e}")
            self._cleanup_temp_file(file_path, target_input)

        return []

    def _build_command(self, target_input: Path) -> list[str]:
        """Construct the Steamless CLI command."""
        cmd = [str(self.cli_path)]

        cmd.extend(OPTION_FLAGS[option] for option in STEAMLESS_OPTIONS if self.settings.get(option))

        if self.settings.get("use_experimental_features"):
            cmd.append("--experimental")

        cmd.append(str(target_input))
        return cmd

    @staticmethod
    def _is_candidate_exe(filename: str) -> bool:
        """Check whether a filename is a valid unpack candidate."""
        lower = filename.lower()
        return lower.endswith(".exe") and ".unpacked.exe" not in lower and ".bak" not in lower

    def _handle_unpacked(
        self, file_path: str | Path, target_input: Path, unpacked_path: Path, crack_only: bool
    ) -> list[tuple[str, str]]:
        """Process a successfully unpacked file."""
        if crack_only:
            logger.info(f"Unpacked (Isolated): {Path(file_path).name}")
            self._cleanup_temp_file(file_path, target_input)
            return [(str(unpacked_path), str(file_path))]

        bak = backup_file(file_path)
        shutil.move(unpacked_path, file_path)
        logger.info(f"Unpacked successfully: {Path(file_path).name}")
        if bak:
            return [(str(file_path), str(file_path)), (str(bak), str(file_path))]
        return [(str(file_path), str(file_path))]

    @staticmethod
    def _cleanup_temp_file(original_path: str | Path, temp_path: str | Path) -> None:
        """Remove a temporary copy if it differs from the original."""
        original_p, temp_p = Path(original_path), Path(temp_path)
        if temp_p != original_p and temp_p.exists():
            with contextlib.suppress(Exception):
                temp_p.unlink()

    @staticmethod
    def _log_unpack_result(stdout: str, file_path: str | Path) -> None:
        """Log the outcome when no unpacked file was produced."""
        basename = Path(file_path).name
        if not stdout:
            logger.warning(f"Failed to unpack {basename}: No output received from Steamless.")
            return

        if "not appear to be packed" in stdout or "already unpacked" in stdout.lower():
            logger.debug(f"{basename} is not packed.")
        else:
            error_lines = [
                line.strip()
                for line in stdout.splitlines()
                if "error" in line.lower() or "fail" in line.lower() or "exception" in line.lower()
            ]
            if error_lines:
                logger.warning(f"Failed to unpack {basename}. Steamless reports: {'; '.join(error_lines[:3])}")
            else:
                logger.warning(f"Failed to unpack {basename}. Steamless output: {stdout.strip()[:200]}")
