import re
from typing import Any

from textual.widgets import Static


def clean_option_prompt(prompt: str) -> str:
    """Strip rich markup tags, badges, and glyph prefixes from an option prompt."""
    clean = re.sub(r"\[.*?\]", "", prompt)
    clean = clean.split("\n")[0]
    for prefix in [">", "▶", "⇄", "✕", " "]:
        clean = clean.lstrip(prefix)
    return clean.strip()


class AppTitleBar(Static):
    """Universal centered application header bar."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("id", "app_title_bar")
        kwargs.setdefault("markup", True)
        super().__init__(*args, **kwargs)

    def on_mount(self) -> None:
        title = getattr(self.app, "TITLE", "APPLICATION")
        self.update(f":: {title.upper()} ::")


class Breadcrumb(Static):
    """Render an interactive clickable breadcrumb trail of the active screen stack."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("markup", True)
        super().__init__(*args, **kwargs)

    def on_mount(self) -> None:
        self.update_trail()

    def update_trail(self) -> None:
        """Re-render breadcrumb trail from active screen stack."""
        parts: list[str] = []
        stack = list(self.app.screen_stack)
        if self.screen not in stack:
            stack.append(self.screen)

        try:
            current_index = stack.index(self.screen)
        except ValueError:
            current_index = len(stack) - 1

        for index in range(current_index + 1):
            screen = stack[index]
            raw_label = (
                getattr(screen, "nav_label", None)
                or getattr(screen, "sub_title", None)
                or getattr(screen, "title", None)
            )
            if not raw_label:
                continue
            label = clean_option_prompt(str(raw_label)).upper()
            if not label:
                continue
            if index == current_index:
                parts.append(f"[bold $text]{label}[/]")
            else:
                parts.append(f"[@click=pop_to_screen_index({index})]{label}[/]")

        self.update(" [$text-muted]>[/] ".join(parts) if parts else "[bold $text]HOME[/]")

    def action_pop_to_screen_index(self, index: str | int) -> None:
        """Pop screens until the target ancestor becomes top."""
        if getattr(self.app.screen, "is_busy", False):
            self.notify("Operation in progress. Press Ctrl+C to abort.", severity="warning")
            return
        target_idx = int(index)
        pops_needed = len(self.app.screen_stack) - 1 - target_idx
        for _ in range(max(0, pops_needed)):
            self.app.pop_screen()


class SummaryCard(Static):
    """Standard hero summary box displaying operational metrics and status."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("id", "summary")
        kwargs.setdefault("markup", True)
        super().__init__(*args, **kwargs)


class SelectionBar(Static):
    """Dynamic multi-select status card with click-to-run action."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("id", "selection_bar")
        kwargs.setdefault("markup", True)
        super().__init__(*args, **kwargs)

    def action_confirm(self) -> None:
        """Execute confirmation on parent screen when action link is clicked."""
        screen = self.screen
        if hasattr(screen, "action_confirm") and getattr(screen, "_checked", None):
            screen.action_confirm()

    def on_click(self) -> None:
        """Also trigger when clicked directly."""
        self.action_confirm()
