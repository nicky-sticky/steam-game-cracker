from pathlib import Path

from textual.app import App

from steam_game_cracker.ui.screens.menus import MainMenuScreen


class CrackerApp(App):
    TITLE = "STEAM GAME CRACKER"
    ENABLE_COMMAND_PALETTE = True
    CSS_PATH = str(Path(__file__).parent / "style.tcss")

    def on_mount(self) -> None:
        self.theme = "textual-dark"
        self.push_screen(MainMenuScreen())
