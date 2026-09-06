import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Regex to find Steam interface strings in binary data
# Pattern 1: SteamUser021, SteamFriends017, SteamClient017
# Pattern 2: STEAMAPPS_INTERFACE_VERSION008, STEAMAPPLIST_INTERFACE_VERSION001
# Pattern 3: STEAMVIDEO_INTERFACE_V002, STEAMHTMLSURFACE_INTERFACE_VERSION_005
INTERFACE_PATTERNS = [
    re.compile(rb"Steam[a-zA-Z]+[0-9]{3}"),
    re.compile(rb"STEAM[A-Z_]+_INTERFACE_(?:VERSION|V)_?[0-9]{3}"),
]

# Mapping of common interface strings to their RUNE INI keys
INTERFACE_KEY_MAP = {
    "SteamAppList": "SteamAppList",
    "STEAMAPPLIST": "SteamAppList",
    "SteamApps": "SteamApps",
    "STEAMAPPS": "SteamApps",
    "SteamClient": "SteamClient",
    "STEAMCLIENT": "SteamClient",
    "SteamController": "SteamController",
    "STEAMCONTROLLER": "SteamController",
    "SteamFriends": "SteamFriends",
    "STEAMFRIENDS": "SteamFriends",
    "SteamGameServer": "SteamGameServer",
    "STEAMGAMESERVER": "SteamGameServer",
    "SteamGameServerStats": "SteamGameServerStats",
    "STEAMGAMESERVERSTATS": "SteamGameServerStats",
    "SteamHTMLSurface": "SteamHTMLSurface",
    "STEAMHTMLSURFACE": "SteamHTMLSurface",
    "SteamHTTP": "SteamHTTP",
    "STEAMHTTP": "SteamHTTP",
    "SteamInput": "SteamInput",
    "STEAMINPUT": "SteamInput",
    "SteamInventory": "SteamInventory",
    "STEAMINVENTORY": "SteamInventory",
    "SteamMatchGameSearch": "SteamMatchGameSearch",
    "STEAMMATCHGAMESEARCH": "SteamMatchGameSearch",
    "SteamMatchMaking": "SteamMatchMaking",
    "STEAMMATCHMAKING": "SteamMatchMaking",
    "SteamMatchMakingServers": "SteamMatchMakingServers",
    "STEAMMATCHMAKINGSERVERS": "SteamMatchMakingServers",
    "SteamMusic": "SteamMusic",
    "STEAMMUSIC": "SteamMusic",
    "SteamMusicRemote": "SteamMusicRemote",
    "STEAMMUSICREMOTE": "SteamMusicRemote",
    "SteamNetworking": "SteamNetworking",
    "STEAMNETWORKING": "SteamNetworking",
    "SteamNetworkingMessages": "SteamNetworkingMessages",
    "SteamNetworkingSockets": "SteamNetworkingSockets",
    "SteamNetworkingUtils": "SteamNetworkingUtils",
    "SteamParentalSettings": "SteamParentalSettings",
    "STEAMPARENTALSETTINGS": "SteamParentalSettings",
    "SteamParties": "SteamParties",
    "STEAMPARTIES": "SteamParties",
    "SteamRemotePlay": "SteamRemotePlay",
    "STEAMREMOTEPLAY": "SteamRemotePlay",
    "SteamRemoteStorage": "SteamRemoteStorage",
    "STEAMREMOTESTORAGE": "SteamRemoteStorage",
    "SteamScreenshots": "SteamScreenshots",
    "STEAMSCREENSHOTS": "SteamScreenshots",
    "SteamUGC": "SteamUGC",
    "STEAMUGC": "SteamUGC",
    "SteamUser": "SteamUser",
    "STEAMUSER": "SteamUser",
    "SteamUserStats": "SteamUserStats",
    "STEAMUSERSTATS": "SteamUserStats",
    "SteamUtils": "SteamUtils",
    "STEAMUTILS": "SteamUtils",
    "SteamVideo": "SteamVideo",
    "STEAMVIDEO": "SteamVideo",
}


class InterfaceScanner:
    """
    Scans a Steam API DLL for interface version strings and maps them
    to the format required by emulator INI files.
    """

    def scan(self, dll_path: str | Path) -> dict[str, str]:
        """
        Scan a binary file for Steam interface strings.
        Args:
            dll_path: Path to the steam_api.dll or steam_api64.dll.
        Returns:
            Mapping of {InterfaceName: FullInterfaceString}.
        """
        if not Path(dll_path).exists():
            logger.warning(f"Interface scanner: File not found {dll_path}")
            return {}

        try:
            with open(dll_path, "rb") as f:
                data = f.read()
        except Exception as e:
            logger.error(f"Interface scanner: Failed to read {dll_path}: {e}")
            return {}

        results = {}
        for pattern in INTERFACE_PATTERNS:
            matches = pattern.findall(data)
            for m in matches:
                try:
                    full_str = m.decode("ascii")
                    key = self._resolve_key(full_str)
                    if key and key not in results:
                        results[key] = full_str
                except Exception:
                    continue

        return results

    # --- Helpers ---

    def _resolve_key(self, interface_str: str) -> str | None:
        """Extract the canonical interface name from a versioned string."""
        # Handle Pattern 2 & 3: STEAM[A-Z_]+_INTERFACE_(VERSION|V)_?[0-9]{3}
        if "_INTERFACE_" in interface_str:
            base = interface_str.split("_INTERFACE_")[0]
            return INTERFACE_KEY_MAP.get(base)

        # Handle Pattern 1: SteamUser021
        # Match name part before the trailing digits
        match = re.match(r"(Steam[a-zA-Z]+?)([0-9]+)$", interface_str)
        if match:
            base = match.group(1)
            return INTERFACE_KEY_MAP.get(base)

        return None
