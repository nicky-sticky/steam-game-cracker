import logging
from pathlib import Path
from typing import Any

from steam_game_cracker.core import config
from steam_game_cracker.core.helpers import get_launcher_rel_path, read_tool_version
from steam_game_cracker.core.utils import bool_str, copy_file, copy_with_backup, write_ini

logger = logging.getLogger(__name__)

GENERATED_CONFIG_FILES = [
    "configs.user.ini",
    "configs.main.ini",
    "configs.app.ini",
    "configs.overlay.ini",
    "steam_appid.txt",
]

LANG_MAP = {
    0: "english",
    1: "german",
    2: "french",
    3: "italian",
    4: "spanish",
    5: "russian",
    6: "thai",
    7: "japanese",
    8: "portuguese",
    9: "polish",
    10: "danish",
    11: "dutch",
    12: "finnish",
    13: "norwegian",
    14: "swedish",
    15: "hungarian",
    16: "czech",
    17: "romanian",
    18: "turkish",
}


class GoldbergEmulator:
    """Manages Goldberg Steam emulator application and config generation."""

    def __init__(self, full_config: dict[str, Any]) -> None:
        self.config = full_config or {}
        self.emu_settings: dict[str, Any] = self.config.get("emu_settings", {}).get("goldberg_settings", {})
        self.release_path: Path = config.GOLDBERG_EMU_PATH

        self.mode = self.emu_settings.get("goldberg_mode", "regular")
        self.experimental_settings = self.emu_settings.get("experimental_settings", {})
        self.loader_settings = self.emu_settings.get("steam_client_loader_settings", {})

    def get_version(self) -> str:
        """Read the Goldberg version from the version file, or 'Unknown' if failed."""
        return read_tool_version(self.release_path)

    def apply_to_dll(
        self,
        dll_path: str | Path,
        app_id: int | str,
        dlc_list: dict[str, Any] | None = None,
        interfaces: dict[str, str] | None = None,
    ) -> list[str] | bool:
        """
        Replace a Steam DLL with its Goldberg equivalent and generate settings.
        Args:
            dll_path: Path to the DLL.
            app_id: Steam Application ID.
            dlc_list: Map of DLC names.
            interfaces: Map of scanned interfaces.
        Returns:
            List of affected files, or False if failed.
        """
        dll_name = Path(dll_path).name.lower()
        is_x64 = "64" in dll_name
        is_steamclient = "steamclient" in dll_name
        arch = "x64" if is_x64 else "x32"

        if self.mode == "steamclient_experimental":
            if is_steamclient:
                # Typically, steamclient emulation loader is applied relative to steam_api DLL scan
                return []

            dst_dir = Path(dll_path).parent
            copied = self._deploy_steamclient_loader(dst_dir, app_id, is_x64)

            # Generate settings (steam_settings folder) beside the loader files
            settings_dir = dst_dir / "steam_settings"
            settings_dir.mkdir(parents=True, exist_ok=True)
            created_files = self._generate_settings(settings_dir, app_id, dlc_list=dlc_list, interfaces=interfaces)

            return copied + created_files

        src_dll = self._resolve_source_dll(is_steamclient, is_x64)
        if not src_dll:
            return False

        src_path = Path(self.release_path) / self.mode / arch / src_dll
        if not src_path.exists():
            logger.debug(f"Goldberg source {src_dll} not found in {self.mode}.")
            return False

        # Replace the DLL
        copied = copy_with_backup(src_path, Path(dll_path).parent)

        # Generate settings alongside the main API DLL (not steamclient)
        created_files = []
        if not is_steamclient:
            settings_dir = Path(dll_path).parent / "steam_settings"
            settings_dir.mkdir(parents=True, exist_ok=True)
            created_files = self._generate_settings(settings_dir, app_id, dlc_list=dlc_list, interfaces=interfaces)

        return copied + created_files

    def generate_crack_only(
        self,
        output_dir: str | Path,
        app_id: int | str,
        dlc_list: dict[str, Any] | None = None,
        game_name: str | None = None,
    ) -> None:
        """
        Export standalone Goldberg emulator files for distribution.
        Args:
            output_dir: Path to the extraction directory.
            app_id: Steam Application ID.
            dlc_list: Map of DLC names.
            game_name: Name of the game.
        """
        if self.mode == "steamclient_experimental":
            for arch_type in ["x86", "x64"]:
                is_x64 = arch_type == "x64"
                arch_dir = Path(output_dir) / arch_type
                arch_dir.mkdir(parents=True, exist_ok=True)

                self._deploy_steamclient_loader(arch_dir, app_id, is_x64)

                # Generate config files
                settings_dir = arch_dir / "steam_settings"
                settings_dir.mkdir(parents=True, exist_ok=True)
                self._generate_settings(settings_dir, app_id, dlc_list=dlc_list)

            if self.config.get("crack_only_settings", {}).get("create_readme"):
                self._generate_readme(output_dir, app_id, game_name)
            return

        for arch_type in ["x86", "x64"]:
            is_x64 = arch_type == "x64"
            arch = "x64" if is_x64 else "x32"

            arch_dir = Path(output_dir) / arch_type
            arch_dir.mkdir(parents=True, exist_ok=True)

            # Copy steam_api DLL
            api_dll = "steam_api64.dll" if is_x64 else "steam_api.dll"
            api_src = Path(self.release_path) / self.mode / arch / api_dll
            if api_src.exists():
                copy_file(api_src, arch_dir / api_dll)

            # Copy steamclient DLL (experimental mode only)
            if self.mode == "experimental":
                client_dll = "steamclient64.dll" if is_x64 else "steamclient.dll"
                client_src = Path(self.release_path) / self.mode / arch / client_dll
                if client_src.exists():
                    copy_file(client_src, arch_dir / client_dll)

            # Generate config files
            settings_dir = arch_dir / "steam_settings"
            settings_dir.mkdir(parents=True, exist_ok=True)
            self._generate_settings(settings_dir, app_id, dlc_list=dlc_list)

        if self.config.get("crack_only_settings", {}).get("create_readme"):
            self._generate_readme(output_dir, app_id, game_name)

    # --- Helpers ---

    def _resolve_source_dll(self, is_steamclient: bool, is_x64: bool) -> str | None:
        """Determine which Goldberg DLL to use as the replacement source."""
        if is_steamclient:
            # Regular mode doesn't ship steamclient replacements
            if self.mode != "experimental":
                logger.debug("Goldberg regular mode: Skipping steamclient replacement.")
                return None
            return "steamclient64.dll" if is_x64 else "steamclient.dll"

        return "steam_api64.dll" if is_x64 else "steam_api.dll"

    def _generate_settings(
        self,
        settings_dir: str | Path,
        app_id: int | str,
        dlc_list: dict[str, Any] | None = None,
        interfaces: dict[str, str] | None = None,
    ) -> list[str]:
        """Generate all Goldberg config files in the given directory."""
        settings_p = Path(settings_dir)
        self._write_user_config(settings_p)
        self._write_main_config(settings_p)
        self._write_app_config(settings_p, dlc_list)
        self._write_overlay_config(settings_p)
        self._write_app_id_file(settings_p, app_id)
        self._write_interfaces_file(settings_p, interfaces)
        copied_avatar = self._copy_avatar(settings_p)

        generated = [str(settings_p / name) for name in GENERATED_CONFIG_FILES]
        if interfaces:
            generated.append(str(settings_p / "steam_interfaces.txt"))
        if copied_avatar:
            generated.append(copied_avatar)
        return generated

    def _write_user_config(self, settings_dir: str | Path) -> None:
        """Write configs.user.ini with account and language settings."""
        user = self.emu_settings.get("user", {})
        lang = user.get("language", "english")
        if isinstance(lang, int):
            # Config may store language as a numeric index
            lang = LANG_MAP.get(lang, "english")

        lines = [
            "[user::general]",
            f"account_name={user.get('account_name', 'gse orca')}",
            f"account_steamid={user.get('steam_id', '76561197960287930')}",
            f"language={lang}",
            f"ip_country={user.get('ip_country', 'US')}",
        ]

        if user.get("use_local_save"):
            save_path = user.get("local_save_path", "steam_settings/saves").replace("/", "\\")
            lines.append("")
            lines.append("[user::saves]")
            lines.append(f"local_save_path={save_path}")

        self._write_ini(settings_dir, "configs.user.ini", lines)

    def _write_main_config(self, settings_dir: str | Path) -> None:
        """Write configs.main.ini with general, stats, and connectivity settings."""
        main = self.emu_settings.get("main", {})

        lines = [
            "[main::general]",
            f"enable_account_avatar={bool_str(main.get('enable_overlay', True))}",
        ]

        # In experimental and steamclient_experimental modes, write the disable_lan_only option
        if self.mode in ("experimental", "steamclient_experimental"):
            disable_lan = self.experimental_settings.get("disable_lan_only", False)
            lines.append(f"disable_lan_only={bool_str(disable_lan)}")

        lines.extend(
            [
                "",
                "[main::stats]",
                f"disable_leaderboards_create_unknown={bool_str(not main.get('enable_leaderboards', True))}",
                f"record_playtime={bool_str(main.get('enable_stats', True))}",
                "",
                "[main::connectivity]",
                f"listen_port={main.get('listen_port', '47584')}",
                f"disable_networking={bool_str(main.get('disable_networking'))}",
                f"offline={bool_str(main.get('offline'))}",
            ]
        )

        self._write_ini(settings_dir, "configs.main.ini", lines)

    def _write_app_config(self, settings_dir: str | Path, dlc_list: dict[str, Any] | None) -> None:
        """Write configs.app.ini with branch and DLC settings."""
        app = self.emu_settings.get("app", {})

        lines = [
            "[app::general]",
            f"is_beta_branch={bool_str(app.get('is_beta_branch'))}",
            f"branch_name={app.get('branch_name', 'public')}",
            "",
            "[app::dlcs]",
            f"unlock_all={bool_str(app.get('unlock_all_dlcs'))}",
        ]

        if dlc_list:
            for dlc_id, dlc_name in dlc_list.items():
                lines.append(f"{dlc_id}={dlc_name}")

        self._write_ini(settings_dir, "configs.app.ini", lines)

    def _write_overlay_config(self, settings_dir: str | Path) -> None:
        """Write configs.overlay.ini with overlay behaviour settings."""
        overlay = self.emu_settings.get("overlay", {})

        lines = [
            "[overlay::general]",
            f"enable_experimental_overlay={bool_str(overlay.get('enable_experimental_overlay'))}",
            f"hook_delay_sec={overlay.get('hook_delay_sec', 0)}",
            f"renderer_detector_timeout_sec={overlay.get('renderer_detector_timeout_sec', 15)}",
            f"disable_achievement_notification={bool_str(overlay.get('disable_achievement_notification'))}",
            f"disable_friend_notification={bool_str(overlay.get('disable_friend_notification'))}",
            f"disable_achievement_progress={bool_str(overlay.get('disable_achievement_progress', True))}",
            f"disable_warning_any={bool_str(overlay.get('disable_warning_any'))}",
            f"disable_warning_bad_appid={bool_str(overlay.get('disable_warning_bad_appid'))}",
            f"disable_warning_local_save={bool_str(overlay.get('disable_warning_local_save'))}",
            f"overlay_always_show_fps={bool_str(overlay.get('overlay_always_show_fps'))}",
            f"overlay_always_show_playtime={bool_str(overlay.get('overlay_always_show_playtime'))}",
        ]

        self._write_ini(settings_dir, "configs.overlay.ini", lines)

    def _write_app_id_file(self, settings_dir: str | Path, app_id: int | str) -> None:
        """Write steam_appid.txt containing the application ID."""
        path = Path(settings_dir) / "steam_appid.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(app_id))

    # --- Helpers ---

    def _patch_client_loader_ini(self, ini_path: str | Path, target_dir: str | Path, app_id: int | str) -> None:
        """Patch ColdClientLoader.ini with the executable path, App ID, and injection options."""
        exe_override = self.loader_settings.get("exe_override")
        game_exe = exe_override if exe_override else (get_launcher_rel_path(target_dir) or "game.exe")

        exe_cmd = self.loader_settings.get("exe_command_line", "")
        force_client = self.loader_settings.get("force_inject_steam_client", True)
        force_overlay = self.loader_settings.get("force_inject_game_overlay_renderer", True)
        ignore_err = self.loader_settings.get("ignore_injection_error", True)
        ignore_arch = self.loader_settings.get("ignore_loader_arch_difference", False)
        persistence = self.loader_settings.get("persistence_mode", 0)
        debugger = self.loader_settings.get("resume_by_debugger", False)
        inject_folder = self.loader_settings.get("dlls_to_inject_folder", "")

        try:
            with open(ini_path, encoding="utf-8") as f:
                content = f.read()
            lines = content.splitlines(keepends=True)
            new_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("Exe="):
                    new_lines.append(f"Exe={game_exe}\n")
                elif stripped.startswith("ExeCommandLine="):
                    new_lines.append(f"ExeCommandLine={exe_cmd}\n")
                elif stripped.startswith("AppId="):
                    new_lines.append(f"AppId={app_id}\n")
                elif stripped.startswith("ForceInjectSteamClient="):
                    new_lines.append(f"ForceInjectSteamClient={bool_str(force_client)}\n")
                elif stripped.startswith("ForceInjectGameOverlayRenderer="):
                    new_lines.append(f"ForceInjectGameOverlayRenderer={bool_str(force_overlay)}\n")
                elif stripped.startswith("IgnoreInjectionError="):
                    new_lines.append(f"IgnoreInjectionError={bool_str(ignore_err)}\n")
                elif stripped.startswith("IgnoreLoaderArchDifference="):
                    new_lines.append(f"IgnoreLoaderArchDifference={bool_str(ignore_arch)}\n")
                elif stripped.startswith("Mode="):
                    new_lines.append(f"Mode={persistence}\n")
                elif stripped.startswith("ResumeByDebugger="):
                    new_lines.append(f"ResumeByDebugger={bool_str(debugger)}\n")
                elif stripped.startswith("DllsToInjectFolder="):
                    new_lines.append(f"DllsToInjectFolder={inject_folder}\n")
                else:
                    new_lines.append(line)
            with open(ini_path, "w", encoding="utf-8") as f:
                f.write("".join(new_lines))
        except Exception as e:
            logger.error(f"Failed to patch {ini_path}: {e}")

    def _deploy_steamclient_loader(self, dst_dir: str | Path, app_id: int | str, is_x64: bool) -> list[str]:
        """Deploy the steamclient loader files and patch ColdClientLoader.ini."""
        copied = []
        dst_p = Path(dst_dir)
        src_dir = Path(self.release_path) / "steamclient_experimental"

        # 1. Copy loader exe
        loader_exe = "steamclient_loader_x64.exe" if is_x64 else "steamclient_loader_x32.exe"
        loader_src = src_dir / loader_exe
        if loader_src.exists():
            copy_file(loader_src, dst_p / loader_exe)
            copied.append(str((dst_p / loader_exe).resolve()))

        # 2. Copy steamclient dll
        client_dll = "steamclient64.dll" if is_x64 else "steamclient.dll"
        client_src = src_dir / client_dll
        if client_src.exists():
            copy_file(client_src, dst_p / client_dll)
            copied.append(str((dst_p / client_dll).resolve()))

        # 3. Copy overlay dll
        overlay_dll = "GameOverlayRenderer64.dll" if is_x64 else "GameOverlayRenderer.dll"
        overlay_src = src_dir / overlay_dll
        if overlay_src.exists():
            copy_file(overlay_src, dst_p / overlay_dll)
            copied.append(str((dst_p / overlay_dll).resolve()))

        # 4. Copy ColdClientLoader.ini
        ini_src = src_dir / "ColdClientLoader.ini"
        ini_dst = dst_p / "ColdClientLoader.ini"
        if ini_src.exists():
            copy_file(ini_src, ini_dst)
            copied.append(str(ini_dst.resolve()))
            self._patch_client_loader_ini(ini_dst, dst_p, app_id)

        return copied

    def _write_interfaces_file(self, settings_dir: str | Path, interfaces: dict[str, str] | None) -> None:
        """Write steam_interfaces.txt with scanned interface versions."""
        if not interfaces:
            return

        lines = [f"{k}={v}" for k, v in interfaces.items()]
        path = Path(settings_dir) / "steam_interfaces.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _write_ini(self, directory: str | Path, filename: str, lines: list[str]) -> None:
        """Write an INI-style config file from a list of lines with validation."""
        write_ini(Path(directory) / filename, lines)

    def _copy_avatar(self, settings_dir: str | Path) -> str | None:
        """Locate and copy the configured avatar to steam_settings/avatar.png."""
        emu_set = self.config.get("emu_settings", {})
        avatar_path: Path | None = None

        for key in ("goldberg_settings", "rune_settings"):
            avatar_cfg = emu_set.get(key, {}).get("avatar", {})
            for size in ("large", "medium", "small"):
                p = avatar_cfg.get(size)
                if p:
                    avatar_p = Path(p)
                    if not avatar_p.is_absolute():
                        avatar_p = Path(config.PROJECT_ROOT) / avatar_p
                    if avatar_p.exists():
                        avatar_path = avatar_p
                        break
            if avatar_path:
                break

        if avatar_path:
            dest = Path(settings_dir) / "avatar.png"
            try:
                copy_file(avatar_path, dest)
                logger.info(f"Copied avatar to {dest}")
                return str(dest)
            except Exception as e:
                logger.warning(f"Failed to copy avatar: {e}")
        return None

    def _generate_readme(self, output_dir: str | Path, app_id: int | str, game_name: str | None = None) -> None:
        """Write a readme.txt with basic crack instructions."""
        readme_path = Path(output_dir) / "readme.txt"
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write("--- Crack Info ---\n\n")
            f.write(f"Game: {game_name or 'Unknown'}\n")
            f.write(f"App ID: {app_id}\n")
            f.write("Emu: Goldberg\n")
            f.write(f"Mode: {self.mode}\n")
            f.write("\nInstructions:\n")
            if self.mode == "steamclient_experimental":
                f.write("1. Copy files from x64 or x86 folder into the game folder containing the game executable.\n")
                f.write("2. Launch the game using steamclient_loader_x64.exe or steamclient_loader_x32.exe.\n")
            else:
                f.write("1. Copy DLLs (steam_api and/or steamclient) to game folder.\n")
                f.write("2. Copy 'steam_settings' next to DLLs.\n")
