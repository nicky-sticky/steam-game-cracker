import logging
from collections.abc import Iterable
from typing import ClassVar

from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from steam_game_cracker import tasks
from steam_game_cracker.core import config
from steam_game_cracker.core.utils import read_json
from steam_game_cracker.ui.helpers import get_unified_games
from steam_game_cracker.ui.screens.base import BaseOptionListScreen
from steam_game_cracker.ui.screens.browser import GameListScreen, GameSelectScreen
from steam_game_cracker.ui.screens.common import ProgressLogScreen

logger = logging.getLogger(__name__)


class MainMenuScreen(BaseOptionListScreen):
    nav_label: str = "HOME"

    def __init__(self) -> None:
        super().__init__()
        self.sub_title = "Steam Game Cracker"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Exit"),
    ]

    def compose_body(self) -> Iterable[Widget]:
        yield Static(id="summary")
        yield OptionList(id="options_list")

    def on_mount(self) -> None:
        super().on_mount()
        self.update_summary()

    def on_screen_resume(self) -> None:
        super().on_screen_resume()
        self.update_summary()

    def action_quit(self) -> None:
        self.app.exit()

    def update_summary(self) -> None:
        """Refresh the summary counts shown on the home screen."""
        records = get_unified_games()
        cfg = read_json(config.GAMES_JSON_PATH) or {}
        cracked_count = sum(1 for g in records if "[CRACKED]" in g["status"] and g["app_id"] in cfg)
        summary_text = (
            f"[$text]Steam Game Cracker[/] - [$warning]Select a cracking workflow to execute:[/]\n"
            f"[$text]Configured:[/] [dim]{len(cfg)} games[/dim] · "
            f"[$text]Status:[/]     [dim]{cracked_count} Cracked[/dim]"
        )
        self.query_one("#summary", Static).update(summary_text)

    def get_options(self) -> list[Option | None]:
        return [
            Option(
                "[bold $text]>  SCAN LIBRARY[/]\n"
                "   [dim]Scan game directories to detect and index installed games[/dim]",
                id="opt_scan",
            ),
            None,
            Option(
                "[bold $text]>  TARGET SPECIFIC GAME[/]\n"
                "   [dim]Browse and apply/restore cracks or firewall rules for a single game[/dim]",
                id="opt_crack",
            ),
            None,
            Option(
                "[bold $text]▶  RUN BULK CRACK (ALL CONFIGURED)[/]\n"
                "   [dim]Apply Goldberg/emulator cracks across all configured games[/dim]",
                id="opt_bulk_crack",
            ),
            None,
            Option(
                "[bold $text]▶  RUN BULK RESTORE (ALL CONFIGURED)[/]\n"
                "   [dim]Restore original Steam binaries across all configured games[/dim]",
                id="opt_bulk_restore",
            ),
            None,
            Option(
                "[bold $text]>  LIST CONFIGURED GAMES[/]\n"
                "   [dim]View read-only data table of all configured games and statuses[/dim]",
                id="opt_list",
            ),
            None,
            Option(
                "[$error]✕  EXIT[/]\n   [dim]Exit Steam Game Cracker[/dim]",
                id="opt_exit",
            ),
        ]

    def on_option_selected(self, option_id: str | None, option_prompt: str) -> None:
        if option_id == "opt_crack":
            self.app.push_screen(GameSelectScreen())
        elif option_id == "opt_scan":
            self.app.push_screen(ProgressLogScreen("Scan Library", tasks.run_scan_library))
        elif option_id == "opt_list":
            self.app.push_screen(GameListScreen())
        elif option_id == "opt_bulk_crack":
            self.app.push_screen(ProgressLogScreen("Bulk Crack", tasks.run_crack_all))
        elif option_id == "opt_bulk_restore":
            self.app.push_screen(ProgressLogScreen("Bulk Restore", tasks.run_restore_all))
        elif option_id == "opt_exit":
            self.app.exit()
