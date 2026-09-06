from steam_game_cracker.core.logger import setup_logger
from steam_game_cracker.core.utils import enable_ansi_utf8
from steam_game_cracker.ui.app import CrackerApp

enable_ansi_utf8()

setup_logger(verbose=False, console=False)


def main() -> None:
    """Launch the Textual application."""
    app = CrackerApp()
    app.run()


if __name__ == "__main__":
    main()
