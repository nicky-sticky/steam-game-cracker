import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from steam_game_cracker.core import config
from steam_game_cracker.core.helpers import read_tool_version
from steam_game_cracker.core.utils import bool_str, copy_file, copy_with_backup, write_ini

logger = logging.getLogger(__name__)


RELEASE_GROUPS = {
    "RUNE": "3681a9beddbae875",
    "CODEX": "b7d5bc716512b5d6",
    "ENIGMA": "bd7d927cba306346",
    "BIZKIT": "7be5c03d2185fc7a",
    "NOY": "8e996f761f4a821e",
    "CODEPUNKS": "bff8a694fb62739e",
    "ADDONIA": "608a9928834128b7",
    "EZAME": "a77820b7e74d0010",
}

# Snake_case settings.json keys mapped back to the RUNE emulator's steam_emu.ini spelling.
RUNE_AVATAR_KEYS = {
    "small": "Small",
    "medium": "Medium",
    "large": "Large",
}

RUNE_CONTROLLER_KEYS = {
    "enabled": "Enabled",
    "force_controller": "ForceController",
    "rumble": "Rumble",
    "swap_face_buttons": "SwapFaceButtons",
    "raw_input": "RawInput",
    "left_joystick_deadzone": "LeftJoystickDeadzone",
    "right_joystick_deadzone": "RightJoystickDeadzone",
    "left_trigger_deadzone": "LeftTriggerDeadzone",
    "right_trigger_deadzone": "RightTriggerDeadzone",
}


class RuneEmulator:
    """Manages RUNE Steam emulator application and config generation."""

    def __init__(self, full_config: dict[str, Any]) -> None:
        self.config = full_config or {}
        self.emu_settings: dict[str, Any] = self.config.get("emu_settings", {}).get("rune_settings", {})
        self.release_path: Path = config.RUNE_EMU_PATH
        self.created_files: list[str] = []

    def get_version(self) -> str:
        """Read the RUNE version from the version file, or 'Unknown' if failed."""
        return read_tool_version(self.release_path)

    def apply_to_dll(
        self,
        dll_path: str | Path,
        app_id: int | str,
        dlc_list: dict[str, Any] | None = None,
        interfaces: dict[str, str] | None = None,
    ) -> list[str] | bool:
        """
        Replace a Steam DLL with its RUNE equivalent and generate settings.
        Args:
            dll_path: Path to the DLL.
            app_id: Steam Application ID.
            dlc_list: Map of DLC names.
            interfaces: Map of scanned interfaces.
        Returns:
            List of affected files, or False if failed.
        """
        self.created_files = []
        dll_name = Path(dll_path).name.lower()
        if "64" not in dll_name:
            logger.warning("RUNE only supports x64.")
            return False

        src_path = Path(self.release_path) / "steam_api64.dll"
        if not src_path.exists():
            logger.error("RUNE source not found.")
            return False

        # Copy original DLL to .rne before replacing it
        if Path(dll_path).exists():
            rne_path = Path(dll_path).with_suffix(".rne")
            try:
                copy_file(dll_path, rne_path)
                self.created_files.append(str(rne_path))
            except Exception as e:
                logger.error(f"Failed to copy original DLL to {rne_path.name}: {e}")

        # Replace the DLL
        copied = copy_with_backup(src_path, Path(dll_path).parent)

        settings_dir = Path(dll_path).parent
        self._generate_ini(settings_dir, app_id, dlc_list, interfaces=interfaces)
        ini_path = str(settings_dir / "steam_emu.ini")

        return [*copied, ini_path, *self.created_files]

    def generate_crack_only(
        self,
        output_dir: str | Path,
        app_id: int | str,
        dlc_list: dict[str, Any] | None = None,
        game_name: str | None = None,
    ) -> None:
        """
        Export standalone RUNE files for distribution.
        Args:
            output_dir: Directory to save the crack package.
            app_id: Steam Application ID.
            dlc_list: Map of DLC names.
            game_name: Human readable game name.
        """
        x64_dir = Path(output_dir) / "x64"
        x64_dir.mkdir(parents=True, exist_ok=True)

        src_path = Path(self.release_path) / "steam_api64.dll"
        if src_path.exists():
            copy_file(src_path, x64_dir / "steam_api64.dll")
            self._generate_ini(x64_dir, app_id, dlc_list)

        if self.config.get("crack_only_settings", {}).get("create_readme"):
            self._generate_readme(output_dir, app_id, game_name)

    # --- Helpers ---

    def _generate_ini(
        self,
        settings_dir: str | Path,
        app_id: int | str,
        dlc_list: dict[str, Any] | None,
        interfaces: dict[str, str] | None = None,
    ) -> None:
        """Build steam_emu.ini by injecting settings into the template."""
        template_path = Path(self.release_path) / "steam_emu.ini"
        target_dir = settings_dir

        set_cfg = self.emu_settings.get("settings", {})

        settings = {
            "AppId": str(app_id),
            "AccountId": str(set_cfg.get("account_id", "22202")),
            "UserName": set_cfg.get("user_name", "steam_user"),
            "Language": set_cfg.get("language", "english"),
            "LobbyEnabled": bool_str(set_cfg.get("lobby_enabled", True)),
            "Offline": bool_str(set_cfg.get("offline", False)),
            "Overlays": bool_str(set_cfg.get("overlays", True)),
        }

        extra_settings = {
            "LoadDll": set_cfg.get("load_dll", ""),
            "LegacyCallbacks": bool_str(set_cfg.get("legacy_callbacks", False)),
            "BlockConnection": bool_str(set_cfg.get("block_connection", True)),
            "SelfProtect": bool_str(set_cfg.get("self_protect", True)),
            "Country": set_cfg.get("country", "US"),
            "LobbyPort": str(set_cfg.get("lobby_port", 31183)),
            "Exit": bool_str(set_cfg.get("exit", False)),
            "SkipRegistry": bool_str(set_cfg.get("skip_registry", False)),
            "SkipHooks": bool_str(set_cfg.get("skip_hooks", False)),
        }

        content = self._load_and_patch_template(template_path, settings, extra_settings)
        content = self._inject_dlc_section(content, dlc_list)
        content = self._inject_crack_section(content)
        content = self._inject_avatar_section(content, target_dir)
        content = self._inject_controller_section(content)
        content = self._inject_interfaces_section(content, interfaces)
        content = self._inject_achievements_section(content)
        content = self._inject_achievement_icons_section(content)

        path = Path(settings_dir) / "steam_emu.ini"
        path_str = str(path)
        try:
            write_ini(path, content.splitlines())
            if path_str not in self.created_files:
                self.created_files.append(path_str)
        except Exception as e:
            logger.error(f"Error writing steam_emu.ini: {e}")

    def _load_and_patch_template(
        self, template_path: str | Path, settings: dict[str, Any], extra_settings: dict[str, Any]
    ) -> str:
        """Load the INI template and ensure all settings are present and updated."""
        template_p = Path(template_path)
        content = ""
        if template_p.exists() and template_p.stat().st_size > 0:
            try:
                with open(template_path, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception as e:
                logger.error(f"Failed to read RUNE template: {e}")

        if "[Settings]" not in content:
            content = "[Settings]\n" + content

        missing_core = []
        missing_extra = []

        # Patch both core and extra settings using a helper to avoid duplication
        content, missing_core = self._patch_section_values(content, settings)
        content, missing_extra = self._patch_section_values(content, extra_settings)

        if missing_core:
            content = content.replace("[Settings]", "[Settings]\n" + "\n".join(missing_core))

        if missing_extra:
            extra_block = "###\n" + "\n".join(missing_extra)
            pattern = re.compile(r"(\[Settings\].*?)(?=\n\s*\[|$)", re.DOTALL)
            match = pattern.search(content)
            if match:
                new_block = match.group(1).rstrip() + f"\n{extra_block}"
                content = content.replace(match.group(1), new_block)

        return content

    def _patch_section_values(self, content: str, settings: dict[str, Any]) -> tuple[str, list[str]]:
        """Patch values in the content in-place using regex."""
        missing = []
        for key, value in settings.items():
            active_p = re.compile(rf"^({key}\s*=.*)$", re.MULTILINE | re.IGNORECASE)
            commented_p = re.compile(rf"^([#;]\s*)({key}\s*=.*)$", re.MULTILINE | re.IGNORECASE)

            val_str = f"{key}={value}"
            if active_p.search(content):
                content = active_p.sub(val_str, content)
            elif commented_p.search(content):
                content = commented_p.sub(rf"\1{val_str}", content)
            else:
                missing.append(val_str)
        return content, missing

    def _inject_section(
        self, content: str, section_name: str, block_content: str, preserve_headers_only: bool = True
    ) -> str:
        """Ensure a section exists and has the given block content."""
        if f"[{section_name}]" in content:
            pattern = re.compile(rf"(\[{section_name}\])(.*?)(?=\n\s*\[|$)", re.DOTALL)
            match = pattern.search(content)
            if match:
                header = match.group(1)
                body = match.group(2)

                if preserve_headers_only:
                    headers = re.findall(r"^###.*$", body, re.MULTILINE)
                    if headers:
                        header_str = "\n".join(headers)
                        return content.replace(
                            match.group(0),
                            f"{header}\n{header_str}\n{block_content.strip()}",
                        )

                return content.replace(match.group(0), f"{header}\n{block_content.strip()}")

        return content.rstrip() + f"\n\n[{section_name}]\n{block_content.strip()}\n"

    def _inject_dlc_section(self, content: str, dlc_list: dict[str, Any] | None) -> str:
        """Append or inject a [DLC] section with overrides and unlockall status."""
        dlc_cfg = self.emu_settings.get("dlc", {})
        merged_dlc = (dlc_list or {}).copy()
        merged_dlc.update(dlc_cfg.get("overrides", {}))

        if not merged_dlc:
            return content

        lines = [f"{app_id}={name}" for app_id, name in merged_dlc.items()]
        lines.insert(0, f"DLCCount={len(merged_dlc)}")
        lines.append(f"DLCUnlockall={bool_str(dlc_cfg.get('unlock_all', False))}")

        return self._inject_section(content, "DLC", "\n".join(lines), preserve_headers_only=True)

    def _inject_crack_section(self, content: str) -> str:
        """Append or inject a [Crack] section."""
        overrides = self.emu_settings.get("crack", {}).get("overrides", {})
        if not overrides:
            return content

        crack_lines = []
        for filename, group in overrides.items():
            group_hex = RELEASE_GROUPS.get(group.upper(), group.lower())
            if not filename:
                crack_lines.append(f"={group_hex}")
            else:
                file_hash = hashlib.md5(filename.lower().encode()).hexdigest()[:16]
                crack_lines.append(f"{file_hash}={group_hex}")

        return self._inject_section(content, "Crack", "\n".join(crack_lines), preserve_headers_only=False)

    def _inject_avatar_section(self, content: str, target_dir: str | Path) -> str:
        """Copy avatars to game folder and inject relative paths into [Avatar] section."""
        avatar_cfg = self.emu_settings.get("avatar", {})
        if not avatar_cfg:
            return content

        avatar_dir = Path(target_dir) / "avatar"
        avatar_dir.mkdir(parents=True, exist_ok=True)

        lines = []
        for size, source_path in avatar_cfg.items():
            size_key = RUNE_AVATAR_KEYS.get(size, size)
            if not source_path:
                continue
            source_p = Path(source_path)
            if not source_p.is_absolute():
                source_p = Path(config.PROJECT_ROOT) / source_p
            if not source_p.exists():
                continue

            filename = source_p.name
            dest_path = avatar_dir / filename
            try:
                copy_file(source_p, dest_path)
                self.created_files.append(str(dest_path))
                lines.append(f"{size_key}=avatar\\{filename}")
            except Exception as e:
                logger.error(f"Failed to copy avatar {source_p}: {e}")

        if not lines:
            return content

        return self._inject_section(content, "Avatar", "\n".join(lines), preserve_headers_only=False)

    def _inject_controller_section(self, content: str) -> str:
        """Append or inject a [Controller] section."""
        ctrl = self.emu_settings.get("controller", {})
        if not ctrl:
            return content

        lines = []
        for k, v in ctrl.items():
            key = RUNE_CONTROLLER_KEYS.get(k, k)
            if isinstance(v, bool):
                lines.append(f"{key}={bool_str(v)}")
            else:
                lines.append(f"{key}={v}")

        return self._inject_section(content, "Controller", "\n".join(lines), preserve_headers_only=False)

    def _inject_interfaces_section(self, content: str, scanned_interfaces: dict[str, str] | None) -> str:
        """Append or inject an [Interfaces] section, preserving header comments."""
        overrides = self.emu_settings.get("interfaces", {}).get("overrides", {})
        final = (scanned_interfaces or {}).copy()
        final.update(overrides)

        if not final:
            return content

        lines = [f"{k}={v}" for k, v in final.items()]
        return self._inject_section(content, "Interfaces", "\n".join(lines), preserve_headers_only=True)

    def _inject_achievements_section(self, content: str) -> str:
        """Append or inject an [Achievements] section."""
        ach = self.emu_settings.get("achievements", {}).get("whitelist", {})
        if not ach:
            return content

        lines = [f"{k}={v}" for k, v in ach.items()]
        return self._inject_section(content, "Achievements", "\n".join(lines), preserve_headers_only=True)

    def _inject_achievement_icons_section(self, content: str) -> str:
        """Append or inject an [AchievementIcons] section."""
        icons = self.emu_settings.get("achievements", {}).get("icons", {})
        if not icons:
            return content

        lines = [f"{k}={v}" for k, v in icons.items()]
        return self._inject_section(content, "AchievementIcons", "\n".join(lines), preserve_headers_only=True)

    def _generate_readme(self, output_dir: str | Path, app_id: int | str, game_name: str | None = None) -> None:
        """Generate a RUNE readme file."""
        readme_path = Path(output_dir) / "RUNE.txt"
        content = f"Game: {game_name or 'Steam Game'}\nApp ID: {app_id}\nEmulator: RUNE\n"
        try:
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:
            pass
