import logging
from collections.abc import Callable, Iterable
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Input, OptionList
from textual.widgets.option_list import Option

from steam_game_cracker.ui.widgets import AppTitleBar, Breadcrumb, SelectionBar

logger = logging.getLogger(__name__)

SELECTED_BADGE = "[green]●[/]"
UNSELECTED_BADGE = "[dim]○[/]"


class BaseScreen(Screen):
    """Universal base screen mounting AppTitleBar, Breadcrumb, Content body, and Footer."""

    sub_title: str = ""
    nav_label: str = ""
    is_busy: bool = False
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "back", "Back"),
    ]

    def compose(self) -> ComposeResult:
        yield AppTitleBar()
        yield Breadcrumb()
        yield from self.compose_body()
        yield Footer()

    def compose_body(self) -> Iterable[Widget]:
        """Yield the screen's core content widgets. Override in subclasses."""
        return []

    def on_screen_resume(self) -> None:
        """Refresh the breadcrumb trail and trigger view refreshes."""
        for bc in self.query(Breadcrumb):
            bc.update_trail()

    def action_back(self) -> None:
        """Pop the current screen off the stack."""
        if getattr(self, "is_busy", False):
            self.notify("Operation in progress. Press Ctrl+C to abort.", severity="warning")
            return
        self.app.pop_screen()


class BaseOptionListScreen(BaseScreen):
    """Base screen hosting an OptionList menu populated from get_options()."""

    def compose_body(self) -> Iterable[Widget]:
        yield OptionList(id="options_list")

    def on_mount(self) -> None:
        opt_list = self.query_one("#options_list", OptionList)
        if opt_list.option_count == 0:
            for opt in self.get_options():
                opt_list.add_option(opt)
        opt_list.focus()

    def refresh_options(self) -> None:
        """Re-populate options list from get_options()."""
        opt_list = self.query_one("#options_list", OptionList)
        opt_list.clear_options()
        for opt in self.get_options():
            opt_list.add_option(opt)

    def get_options(self) -> list[Option | None]:
        """Return the options to render. Subclasses must implement."""
        return []

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.on_option_selected(event.option.id, str(event.option.prompt))

    def on_option_selected(self, option_id: str | None, option_prompt: str) -> None:
        """Handle option selection. Override in subclasses."""
        pass


class BaseDataTableScreen(BaseScreen):
    """Base screen hosting an async-populated DataTable with worker thread loading."""

    def compose_body(self) -> Iterable[Widget]:
        yield DataTable(id="data_table")

    def on_mount(self) -> None:
        table = self.query_one("#data_table", DataTable)
        table.cursor_type = "row"
        self.setup_columns(table)
        self.loading = True
        self.run_worker(self.load_and_populate, thread=True)

    def setup_columns(self, table: DataTable) -> None:
        """Configure table column names and widths."""
        pass

    def load_and_populate(self) -> None:
        """Fetch records in background thread and push to UI."""
        try:
            records = self.get_records()
        except Exception:
            logger.exception("Failed to load table records")
            records = []
        self.app.call_from_thread(self.populate, records)

    def get_records(self) -> list[Any]:
        """Return raw data records. Override in subclasses."""
        return []

    def populate(self, records: list[Any]) -> None:
        """Add rows to DataTable. Override in subclasses."""
        self.loading = False

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.on_row_selected(event.row_key.value if event.row_key else None)

    def on_row_selected(self, row_key: str | None) -> None:
        """Handle table row selection."""
        pass


class BaseSearchTableScreen(BaseScreen):
    """Base screen providing live search filtering over a DataTable."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[Any] = []

    def compose_body(self) -> Iterable[Widget]:
        yield Input(placeholder="Search / Filter...", id="search_input")
        yield DataTable(id="data_table")

    def on_mount(self) -> None:
        table = self.query_one("#data_table", DataTable)
        table.cursor_type = "row"
        self.setup_columns(table)
        self.loading = True
        self.run_worker(self.load_records, thread=True)
        self.query_one("#search_input", Input).focus()

    def setup_columns(self, table: DataTable) -> None:
        pass

    def load_records(self) -> None:
        try:
            records = self.get_records()
        except Exception:
            records = []
        self.app.call_from_thread(self.finish_loading, records)

    def get_records(self) -> list[Any]:
        return []

    def finish_loading(self, records: list[Any]) -> None:
        self.records = records
        self.loading = False
        search_val = self.query_one("#search_input", Input).value
        self.update_table_view(search_val)

    def update_table_view(self, filter_text: str) -> None:
        """Rebuild table rows matching filter_text."""
        table = self.query_one("#data_table", DataTable)
        table.clear()
        for record in self.records:
            if self.match_record(record, filter_text):
                self.add_record_row(table, record)

    def match_record(self, record: Any, filter_text: str) -> bool:
        """Return True if record matches filter query."""
        return True

    def add_record_row(self, table: DataTable, record: Any) -> None:
        """Add single row representing record to table."""
        pass

    def on_input_changed(self, event: Input.Changed) -> None:
        self.update_table_view(event.value)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.on_row_selected(event.row_key.value if event.row_key else None)

    def on_row_selected(self, row_key: str | None) -> None:
        pass


class BaseMultiSelectTableScreen(BaseSearchTableScreen):
    """Searchable multi-select browser with toggle, select-all, and batch action execution."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("space", "toggle", "Toggle Item"),
        Binding("a", "toggle_all", "Select All/None"),
        Binding("r", "confirm", "Run Action", priority=True),
        Binding("ctrl+enter", "confirm", "Run Action", show=False, priority=True),
    ]

    def __init__(self, on_confirm: Callable[[list[Any]], Any]) -> None:
        super().__init__()
        self.on_confirm = on_confirm
        self._checked: set[str] = set()
        self._visible_keys: list[str] = []

    def compose_body(self) -> Iterable[Widget]:
        yield SelectionBar()
        yield Input(placeholder="Search / Filter...", id="search_input")
        yield DataTable(id="data_table")

    def finish_loading(self, records: list[Any]) -> None:
        super().finish_loading(records)
        self.update_selection_bar()

    def update_selection_bar(self) -> None:
        """Update live selection counter and action instruction."""
        count = len(self._checked)
        total = len(self.records)
        badge = f"[$success]{count}[/]" if count > 0 else f"[$text-muted]{count}[/]"
        if count > 0:
            action_hint = f"[@click=confirm]▶  RUN ACTION ({count} SELECTED)[/]"
        else:
            action_hint = "[$warning]Select items below to proceed:[/]"
        msg = f"[$text]Selected:[/] {badge} / {total} items - {action_hint}"
        for bar in self.query(SelectionBar):
            bar.update(msg)

    def update_table_view(self, filter_text: str) -> None:
        self._visible_keys = []
        super().update_table_view(filter_text)
        self.update_selection_bar()

    def toggle_key(self, key: str) -> None:
        """Toggle selection state for a specific record key."""
        table = self.query_one("#data_table", DataTable)
        if key in self._checked:
            self._checked.discard(key)
        else:
            self._checked.add(key)
        table.update_cell(key, "sel", SELECTED_BADGE if key in self._checked else UNSELECTED_BADGE)
        self.update_selection_bar()

    def action_toggle(self) -> None:
        """Toggle selection on the active cursor row."""
        table = self.query_one("#data_table", DataTable)
        row_idx = table.cursor_row
        if row_idx is None or row_idx >= len(self._visible_keys):
            return
        key = self._visible_keys[row_idx]
        self.toggle_key(key)

    def on_row_selected(self, row_key: str | None) -> None:
        """Handle row click or enter to toggle checkbox."""
        if row_key:
            self.toggle_key(row_key)

    def action_toggle_all(self) -> None:
        """Toggle all currently filtered/visible items."""
        if not self._visible_keys:
            return
        all_selected = all(k in self._checked for k in self._visible_keys)
        table = self.query_one("#data_table", DataTable)
        for k in self._visible_keys:
            if all_selected:
                self._checked.discard(k)
            else:
                self._checked.add(k)
            table.update_cell(k, "sel", UNSELECTED_BADGE if all_selected else SELECTED_BADGE)
        self.update_selection_bar()

    def action_confirm(self) -> None:
        """Submit selected items and execute batch callback."""
        selected_records = [r for r in self.records if self.get_record_key(r) in self._checked]
        self.app.pop_screen()
        self.on_confirm(selected_records)

    def get_record_key(self, record: Any) -> str:
        """Return unique key identifier for record. Override in subclasses."""
        return str(record)
