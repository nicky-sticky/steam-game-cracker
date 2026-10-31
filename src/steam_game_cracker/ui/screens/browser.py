import logging
from collections.abc import Iterable
from typing import Any

from textual.widget import Widget
from textual.widgets import DataTable, OptionList, Static
from textual.widgets.option_list import Option

from steam_game_cracker import tasks
from steam_game_cracker.core.helpers import resolve_game_path
from steam_game_cracker.ui.helpers import get_unified_games
from steam_game_cracker.ui.screens.base import (
    BaseDataTableScreen,
    BaseOptionListScreen,
    BaseSearchTableScreen,
)
from steam_game_cracker.ui.screens.common import ProgressLogScreen

logger = logging.getLogger(__name__)


class GameListScreen(BaseDataTableScreen):
    nav_label: str = "LIST"

    def __init__(self) -> None:
        super().__init__()
        self.sub_title = "Configured Games"

    def setup_columns(self, table: DataTable) -> None:
        table.add_columns("App ID", "Game Folder Name", "Status")

    def get_records(self) -> list[dict[str, Any]]:
        return get_unified_games()

    def populate(self, records: list[dict[str, Any]]) -> None:
        table = self.query_one("#data_table", DataTable)
        table.clear()
        for r in records:
            table.add_row(str(r["app_id"]), r["name"], r["status"])
        self.loading = False


class GameSelectScreen(BaseSearchTableScreen):
    nav_label: str = "GAMES"

    def __init__(self) -> None:
        super().__init__()
        self.sub_title = "Game Browser"

    def setup_columns(self, table: DataTable) -> None:
        table.add_columns("App ID", "Game Folder Name", "Status")

    def get_records(self) -> list[dict[str, Any]]:
        return get_unified_games()

    def match_record(self, record: dict[str, Any], filter_text: str) -> bool:
        term = filter_text.lower()
        return term in record["name"].lower() or term in str(record["app_id"]).lower()

    def add_record_row(self, table: DataTable, record: dict[str, Any]) -> None:
        table.add_row(
            str(record["app_id"]),
            record["name"],
            record["status"],
            key=record["name"],
        )

    def on_screen_resume(self) -> None:
        super().on_screen_resume()
        self.loading = True
        self.run_worker(self.load_records, thread=True)

    def on_row_selected(self, row_key: str | None) -> None:
        if not row_key:
            return
        r = next((rec for rec in self.records if rec["name"] == str(row_key)), None)
        if r:
            self.app.push_screen(GameContextMenuScreen(r))


class GameContextMenuScreen(BaseOptionListScreen):
    nav_label: str = "GAME"

    def __init__(self, game_record: dict[str, Any]) -> None:
        super().__init__()
        self.game_record = game_record
        self.sub_title = game_record["name"]

    def compose_body(self) -> Iterable[Widget]:
        yield Static(id="summary")
        yield OptionList(id="options_list")

    def on_mount(self) -> None:
        super().on_mount()
        self.update_view()

    def on_screen_resume(self) -> None:
        super().on_screen_resume()
        records = get_unified_games()
        for rec in records:
            if rec["name"] == self.game_record["name"]:
                self.game_record = rec
                break
        self.update_view()

    def update_view(self) -> None:
        """Refresh the detail view for the selected game."""
        r = self.game_record
        details = (
            f"[$text]Game:[/]   [dim]{r['name']}[/dim]\n"
            f"[$text]Status:[/] {r['status']} - [$warning]Select an action to execute:[/]"
        )
        self.query_one("#summary", Static).update(details)

    def get_options(self) -> list[Option | None]:
        return [
            Option(
                "[bold $text]>  APPLY CRACK (USING CONFIG/OVERRIDES)[/]\n"
                "   [dim]Deploy emulator binaries and apply configured overrides[/dim]",
                id="opt_crack",
            ),
            None,
            Option(
                "[bold $text]>  RESTORE ORIGINAL BINARIES[/]\n"
                "   [dim]Remove emulator crack and restore clean original files[/dim]",
                id="opt_restore",
            ),
            None,
            Option(
                "[bold $text]>  ADD FIREWALL RULES[/]\n"
                "   [dim]Block game executables from outbound internet access[/dim]",
                id="opt_fw_add",
            ),
            None,
            Option(
                "[bold $text]>  REMOVE FIREWALL RULES[/]\n"
                "   [dim]Delete existing outbound firewall blocking rules[/dim]",
                id="opt_fw_remove",
            ),
            None,
            Option(
                "[bold $text]>  REFRESH FIREWALL RULES[/]\n"
                "   [dim]Re-scan executables and recreate firewall blocking rules[/dim]",
                id="opt_fw_refresh",
            ),
        ]

    def on_option_selected(self, option_id: str | None, option_prompt: str) -> None:
        r = self.game_record
        target_path = resolve_game_path(r["config_data"].get("game_folder"), silent=True)
        if not target_path:
            self.app.notify("Game folder not found for this record.", severity="error")
            return
        label = r["name"]

        if option_id == "opt_crack":
            self.app.push_screen(
                ProgressLogScreen(
                    "Applying Crack",
                    lambda: tasks.run_apply_crack(
                        target_path, r["config_data"]["app_id"], overrides=r["config_data"], label=label
                    ),
                )
            )
        elif option_id == "opt_restore":
            self.app.push_screen(
                ProgressLogScreen(
                    "Removing Crack",
                    lambda: tasks.run_restore_crack(target_path, label=label),
                )
            )
        elif option_id == "opt_fw_add":
            self.app.push_screen(
                ProgressLogScreen(
                    "Adding Firewall Rules",
                    lambda: tasks.run_apply_firewall(target_path, label=label),
                )
            )
        elif option_id == "opt_fw_remove":
            self.app.push_screen(
                ProgressLogScreen(
                    "Removing Firewall Rules",
                    lambda: tasks.run_remove_firewall(target_path, label=label),
                )
            )
        elif option_id == "opt_fw_refresh":
            self.app.push_screen(
                ProgressLogScreen(
                    "Refreshing Firewall Rules",
                    lambda: tasks.run_refresh_firewall(target_path, label=label),
                )
            )
