import logging
import re
import shutil
import subprocess
from pathlib import Path

from steam_game_cracker.core import config

logger = logging.getLogger(__name__)


class FirewallService:
    """Interfaces with the PowerShell Game Firewall Blocker script."""

    def __init__(self) -> None:
        self.script_path: Path = config.FW_BLOCKER_PATH

    def apply_rules(self, target_path: str | Path, excludes_path: str | Path | None = None) -> bool:
        """Add firewall rules for executables in the target directory."""
        return self._run_script(Path(target_path), "Add", Path(excludes_path) if excludes_path else None)

    def remove_rules(self, target_path: str | Path) -> bool:
        """Remove firewall rules for the target game."""
        return self._run_script(Path(target_path), "Remove")

    def refresh_rules(self, target_path: str | Path, excludes_path: str | Path | None = None) -> bool:
        """Refresh (Remove then Add) firewall rules for the target game."""
        return self._run_script(Path(target_path), "Refresh", Path(excludes_path) if excludes_path else None)

    # --- Helpers ---

    def _run_script(self, target_path: Path, action: str, excludes_path: Path | None = None) -> bool:
        """Execute the PowerShell script with the specified parameters."""
        if not shutil.which("powershell.exe"):
            logger.error("PowerShell is not installed or not available in the system PATH.")
            return False

        if not self.script_path.exists():
            logger.error(f"Firewall script not found: {self.script_path}")
            return False

        cmd = [
            "powershell.exe",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-TargetPath",
            str(target_path),
            "-Action",
            action,
        ]

        if excludes_path:
            cmd.extend(["-ExcludesPath", str(excludes_path)])

        logger.info(f"Firewall: Running {action} on {target_path}...")

        try:
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=60,
            )

            if process.returncode == 0:
                logger.info(f"Firewall {action} completed successfully.")

                output = process.stdout
                created_match = re.search(r"Created (\d+) rules", output)
                purged_match = re.search(r"purged (\d+) rules", output)

                if created_match:
                    logger.info(f"Firewall: Created {created_match.group(1)} rules.")
                if purged_match:
                    logger.info(f"Firewall: Removed {purged_match.group(1)} rules.")

                return True
            else:
                if "RunAsAdministrator" in process.stderr or "Access is denied" in process.stderr:
                    logger.error("Firewall error: Administrative privileges are required.")
                else:
                    logger.error(f"Firewall error (code {process.returncode}): {process.stderr.strip()}")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"Firewall {action} timed out after 60 seconds.")
            return False
        except Exception as e:
            logger.error(f"Failed to execute firewall script: {e}")
            return False
