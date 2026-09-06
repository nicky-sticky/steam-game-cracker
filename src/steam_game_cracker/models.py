from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

_E = TypeVar("_E", bound=StrEnum)


def _as_bool(value: Any, default: bool = False) -> bool:
    """Coerce value to bool, falling back to default when missing."""
    if value is not None:
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("true", "1", "yes", "on")
    return default


def _as_str(value: Any, default: str = "") -> str:
    """Coerce value to str, falling back to default when missing."""
    return str(value) if value is not None else default


def _as_enum(value: Any, enum_type: type[_E], default: _E) -> _E:
    """Coerce value to an enum member, falling back to the default member."""
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value.strip().lower())
        except ValueError:
            return default
    return default


# --- File Config ---


@dataclass(frozen=True)
class FileConfig:
    """Shape of config.toml file."""

    game_root_dirs: list[str] = field(default_factory=list)
    crack_info_filename: str = "!crack.info"
    zip7_path: str = r"C:\Program Files\7-Zip\7z.exe"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FileConfig":
        """Build a FileConfig from raw TOML data, coercing values tolerantly."""
        raw_dirs = data.get("game_root_dirs")
        if isinstance(raw_dirs, list):
            dirs = [str(d).strip() for d in raw_dirs if str(d).strip()]
        elif isinstance(raw_dirs, str):
            dirs = [d.strip() for d in raw_dirs.split(",") if d.strip()]
        else:
            dirs = []

        return cls(
            game_root_dirs=dirs,
            crack_info_filename=_as_str(data.get("crack_info_filename"), "!crack.info"),
            zip7_path=_as_str(data.get("zip7_path"), r"C:\Program Files\7-Zip\7z.exe"),
        )


# --- Configuration Sub-Models ---


@dataclass
class ConfigSettings:
    """Pipeline feature toggle switches."""

    apply_emu: bool = True
    apply_steam_stub_unpacker: bool = True
    apply_hypervisor: bool = False
    copy_extra_files: bool = False
    generate_crack_only: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConfigSettings":
        """Build a ConfigSettings from raw config data."""
        return cls(
            apply_emu=_as_bool(data.get("apply_emu"), True),
            apply_steam_stub_unpacker=_as_bool(data.get("apply_steam_stub_unpacker"), True),
            apply_hypervisor=_as_bool(data.get("apply_hypervisor"), False),
            copy_extra_files=_as_bool(data.get("copy_extra_files"), False),
            generate_crack_only=_as_bool(data.get("generate_crack_only"), False),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a snake_case dict matching the settings.json contract."""
        return {
            "apply_emu": self.apply_emu,
            "apply_steam_stub_unpacker": self.apply_steam_stub_unpacker,
            "apply_hypervisor": self.apply_hypervisor,
            "copy_extra_files": self.copy_extra_files,
            "generate_crack_only": self.generate_crack_only,
        }


class EmulatorType(StrEnum):
    """Supported Steam emulator backends."""

    GOLDBERG = "goldberg"
    RUNE = "rune"


@dataclass
class EmuSettings:
    """Emulator configuration block."""

    emulator: EmulatorType = EmulatorType.GOLDBERG
    rune_settings: dict[str, Any] = field(default_factory=dict)
    goldberg_settings: dict[str, Any] = field(default_factory=dict)
    fetch_dlcs_from_steam: bool = True
    generate_emu_game_info: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EmuSettings":
        """Build an EmuSettings from raw config data."""
        return cls(
            emulator=_as_enum(data.get("emulator"), EmulatorType, EmulatorType.GOLDBERG),
            rune_settings=dict(data.get("rune_settings", {})),
            goldberg_settings=dict(data.get("goldberg_settings", {})),
            fetch_dlcs_from_steam=_as_bool(data.get("fetch_dlcs_from_steam"), True),
            generate_emu_game_info=_as_bool(data.get("generate_emu_game_info"), True),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a snake_case dict matching the settings.json contract."""
        return {
            "emulator": str(self.emulator),
            "rune_settings": self.rune_settings,
            "goldberg_settings": self.goldberg_settings,
            "fetch_dlcs_from_steam": self.fetch_dlcs_from_steam,
            "generate_emu_game_info": self.generate_emu_game_info,
        }


@dataclass
class MasterSettings:
    """Complete merged settings document (settings.json + overrides)."""

    config_settings: ConfigSettings = field(default_factory=ConfigSettings)
    emu_settings: EmuSettings = field(default_factory=EmuSettings)
    steam_stub_unpacker_settings: dict[str, Any] = field(default_factory=dict)
    hypervisor_settings: dict[str, Any] = field(default_factory=dict)
    extra_files_settings: dict[str, Any] = field(default_factory=dict)
    crack_only_settings: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MasterSettings":
        """Build a MasterSettings from raw config data."""
        cfg = ConfigSettings.from_dict(data.get("config_settings", {}))
        emu = EmuSettings.from_dict(data.get("emu_settings", {}))
        stub = dict(data.get("steam_stub_unpacker_settings", {}))
        hyp = dict(data.get("hypervisor_settings", {}))
        extra = dict(data.get("extra_files_settings", {}))
        crack_only = dict(data.get("crack_only_settings", {}))

        return cls(
            config_settings=cfg,
            emu_settings=emu,
            steam_stub_unpacker_settings=stub,
            hypervisor_settings=hyp,
            extra_files_settings=extra,
            crack_only_settings=crack_only,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a snake_case dict matching the settings.json contract."""
        return {
            "config_settings": self.config_settings.to_dict(),
            "emu_settings": self.emu_settings.to_dict(),
            "steam_stub_unpacker_settings": self.steam_stub_unpacker_settings,
            "hypervisor_settings": self.hypervisor_settings,
            "extra_files_settings": self.extra_files_settings,
            "crack_only_settings": self.crack_only_settings,
        }


# --- Game & Verification ---
