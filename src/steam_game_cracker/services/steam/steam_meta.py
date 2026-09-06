import contextlib
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from steam.client import SteamClient
from steam.enums import EResult

from steam_game_cracker.core import config
from steam_game_cracker.core.utils import read_json, write_json

logger = logging.getLogger(__name__)

DLC_SECTION_KEYS = ["common", "extended", "depots"]
DLC_FIELD_NAMES = ["dlc", "dlc_list", "listofdlc"]
DLC_DEPOT_FIELDS = ["dlcid", "dlcappid"]


class SteamMetadataClient:
    """Handles connection to Steam API and extraction of game and DLC metadata."""

    def __init__(self, emu_settings: dict[str, Any] | None = None) -> None:
        self.emu_settings = emu_settings or {}
        self._client: SteamClient | None = None

    def close(self) -> None:
        """Disconnect and log out the cached Steam client session."""
        if self._client:
            with contextlib.suppress(Exception):
                self._client.logout()
                self._client.disconnect()
            self._client = None

    def __enter__(self) -> "SteamMetadataClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    @staticmethod
    def search_store_by_name(query: str) -> tuple[int, str] | None:
        """
        Search Steam Store Web API for a game title without requiring credentials.
        Returns (app_id, official_name) or None if no match found.
        """

        # 1. Clean query (strip release tags and replace symbols with spaces)
        clean_query = re.sub(r"[\._\-]+", " ", query)
        clean_query = re.sub(r"(?i)\b(v?\d+\.\d+.*|fitgirl|dodi|repack|crack|steam)\b.*", "", clean_query).strip()
        if not clean_query:
            clean_query = query.strip()

        url = f"https://store.steampowered.com/api/storesearch/?term={urllib.parse.quote(clean_query)}&l=english&cc=US"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                items = data.get("items", [])
                if items:
                    top_item = items[0]
                    return int(top_item["id"]), str(top_item["name"])
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            logger.warning(f"Network error searching Steam Store for '{query}': {e}")
            raise ConnectionError(f"Steam API network request failed: {e}") from e
        except Exception as e:
            logger.debug(f"Store search parse error for query '{query}': {e}")

        return None

    def fetch_metadata(self, app_id: int | str) -> dict[str, Any]:
        """
        Fetch game details and DLC information from Steam or cache.
        Args:
            app_id: Steam Application ID.
        Returns:
            Game name and map of DLCs.
        """
        if not self.emu_settings.get("generate_emu_game_info"):
            return {"name": None, "dlcs": {}}

        if self.emu_settings.get("fetch_dlcs_from_steam", True):
            cache_dir = Path(config.TEMP_PATH) / "metadata_cache"
            cache_file = cache_dir / f"{app_id}.json"
            if cache_file.exists():
                logger.info(f"Using cached Steam metadata for App ID {app_id}")
                cached_data = read_json(cache_file)
                if cached_data:
                    return cached_data

            logger.info("Fetching metadata from Steam...")
            data = self._get_steam_api_data(app_id)
            if data:
                cache_dir.mkdir(parents=True, exist_ok=True)
                write_json(cache_file, data)
                return data
        else:
            logger.info("Steam API metadata polling is disabled (Offline Mode).")

        return {"name": None, "dlcs": {}}

    # --- Helpers ---

    def _connect_client(self, client: SteamClient) -> bool:
        """Attempt to establish a connection to Steam servers with limited retries."""
        if not client.connect(retry=2, delay=1):
            logger.error("Failed to establish connection to Steam servers (check your internet connection).")
            return False
        return True

    def _authenticate_client(self, client: SteamClient) -> bool:
        """Authenticate the SteamClient anonymously or with credentials if provided."""
        res = client.anonymous_login()

        if res != EResult.OK and config.settings.steam_username:
            res = client.login(username=config.settings.steam_username)

        return res == EResult.OK

    def _get_steam_client(self) -> SteamClient | None:
        """Returns an authenticated SteamClient, creating or reusing a session."""
        if self._client and getattr(self._client, "connected", False):
            return self._client

        client = SteamClient()
        if not self._connect_client(client):
            return None
        if not self._authenticate_client(client):
            with contextlib.suppress(Exception):
                client.disconnect()
            return None

        self._client = client
        return self._client

    def _get_steam_api_data(self, app_id: int | str) -> dict[str, Any] | None:
        """Connect to Steam and retrieve app metadata and DLC list."""
        app_id = int(app_id)
        try:
            client = self._get_steam_client()
            if not client:
                return None

            product = client.get_product_info(apps=[app_id])
            if not product or app_id not in product.get("apps", {}):
                return None

            app_data = product["apps"][app_id]
            dlc_ids = self._extract_dlc_ids(app_data)
            dlc_map = self._resolve_dlc_names(client, dlc_ids)
            game_name = app_data.get("common", {}).get("name", "Unknown")

            return {"name": game_name, "dlcs": dlc_map}

        except Exception as e:
            logger.error(f"Steam API query failed: {e}")
            self.close()
            return None

    def _extract_dlc_ids(self, app_data: dict[str, Any]) -> list[int]:
        """Extract all DLC App IDs from the depots and info sections."""
        dlc_ids = set()

        for section_key in DLC_SECTION_KEYS:
            section = app_data.get(section_key, {})

            if section_key == "depots":
                for depot in section.values():
                    if not hasattr(depot, "get"):
                        continue
                    for field in DLC_DEPOT_FIELDS:
                        value = depot.get(field)
                        if value:
                            dlc_ids.add(int(value))
            else:
                for field in DLC_FIELD_NAMES:
                    csv_value = section.get(field, "")
                    if not csv_value:
                        continue
                    if isinstance(csv_value, (list, tuple)):
                        dlc_ids.update(int(x) for x in csv_value)
                    else:
                        dlc_ids.update(int(x) for x in str(csv_value).split(","))

        return list(dlc_ids)

    def _resolve_dlc_names(self, client: SteamClient, dlc_ids: list[int]) -> dict[str, str]:
        """Query names for discovered DLC App IDs."""
        if not dlc_ids:
            return {}

        dlc_info = client.get_product_info(apps=dlc_ids) or {}
        return {
            str(dlc_id): (dlc_info.get("apps", {}).get(dlc_id, {}).get("common", {}).get("name", f"DLC {dlc_id}"))
            for dlc_id in dlc_ids
        }
