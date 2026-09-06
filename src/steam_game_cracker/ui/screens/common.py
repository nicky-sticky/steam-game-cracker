import contextlib
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any, ClassVar

from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from steam_game_cracker.ui.handlers import TuiLogHandler
from steam_game_cracker.ui.screens.base import BaseOptionListScreen, BaseScreen
from steam_game_cracker.ui.widgets import clean_option_prompt

logger = logging.getLogger(__name__)


class ProgressLogScreen(BaseScreen):
    """Execute background worker task while streaming RichLog records and timing."""

    nav_label: str = "PROGRESS"
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+c", "abort_task", "Abort", priority=True, show=True),
        Binding("escape", "back", "Return", show=True),
    ]

    def __init__(
        self,
        title: str,
        task_func: Callable[..., Any],
        nav_label: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.sub_title = title
        if nav_label:
            self.nav_label = nav_label
        elif title:
            clean = clean_option_prompt(title).split(":")[-1].strip()
            self.nav_label = clean.split()[0].upper()
        self.task_func = task_func
        self.task_args = args
        self.task_kwargs = kwargs
        self.start_time: float = 0.0
        self.is_busy: bool = True
        self._aborted: bool = False
        self._child_procs: list[subprocess.Popen] = []
        self._worker_thread_id: int | None = None

    def compose_body(self) -> Iterable[Widget]:
        yield Static("[$text]Status:[/] [$primary]Executing...[/] [dim](Ctrl+C to abort)[/]", id="status_banner")
        yield RichLog(id="progress_log", highlight=False, markup=True)

    def on_mount(self) -> None:
        log_widget = self.query_one("#progress_log", RichLog)
        self.start_time = time.time()

        def write_log(message: str) -> None:
            with contextlib.suppress(Exception):
                self.app.call_from_thread(log_widget.write, message)

        self.run_worker(lambda: self._execute_task(write_log), thread=True)

    def _execute_task(self, write_log: Callable[[str], None]) -> None:
        self._worker_thread_id = threading.get_ident()
        handler = TuiLogHandler(write_log)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)

        orig_popen = subprocess.Popen

        def tracked_popen(*p_args: Any, **p_kwargs: Any) -> subprocess.Popen:
            proc = orig_popen(*p_args, **p_kwargs)
            if threading.get_ident() == self._worker_thread_id:
                self._child_procs.append(proc)
            return proc

        subprocess.Popen = tracked_popen
        success = True
        try:
            result = self.task_func(*self.task_args, **self.task_kwargs)
            if isinstance(result, bool):
                success = result
        except Exception as e:
            if not self._aborted:
                success = False
                logger.exception("Task failed with exception")
                logger.error(f"[red]Error: {e}[/red]")
        finally:
            subprocess.Popen = orig_popen
            root_logger.removeHandler(handler)
            elapsed = time.time() - self.start_time
            self.app.call_from_thread(self._finish_display, success, elapsed)

    def action_abort_task(self) -> None:
        """Handler for Ctrl+C. Kills active subprocesses while keeping TUI alive."""
        if not self.is_busy or self._aborted:
            return

        self._aborted = True
        self.is_busy = False
        self._kill_child_processes()

        with contextlib.suppress(Exception):
            log_widget = self.query_one("#progress_log", RichLog)
            log_widget.write("[bold red]>> Operation aborted by user (Ctrl+C).[/bold red]")

        status_banner = self.query_one("#status_banner", Static)
        status_banner.update("[$text]Status:[/] [$error]Aborted[/] - [$warning]Press Escape to return[/]")
        self.notify("Task aborted.", severity="error")

    def _finish_display(self, success: bool, elapsed: float) -> None:
        self.is_busy = False
        if self._aborted:
            return

        status_banner = self.query_one("#status_banner", Static)
        if success:
            status_banner.update(
                f"[$text]Status:[/] [$success]Completed[/] [dim]({elapsed:.1f}s)[/] - "
                f"[$warning]Press Escape to return[/]"
            )
        else:
            status_banner.update(
                f"[$text]Status:[/] [$error]Failed[/] [dim]({elapsed:.1f}s)[/] - [$warning]Press Escape to return[/]"
            )

    def _kill_child_processes(self) -> None:
        """Forcibly kill any running child processes in the process tree."""
        for proc in self._child_procs:
            if proc.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    proc.kill()

    def on_unmount(self) -> None:
        """Safety net: clean up any orphaned processes if screen is popped."""
        self.is_busy = False
        self._kill_child_processes()


class WatchProgressScreen(BaseScreen):
    """Run continuous watcher task, streaming logs until user presses Escape/Back."""

    nav_label: str = "WATCHER"
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "back", "Stop & Return", show=True),
    ]

    def __init__(
        self,
        title: str,
        task_func: Callable[..., Any],
        stop_func: Callable[[], None] | None = None,
        nav_label: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.sub_title = title
        if nav_label:
            self.nav_label = nav_label
        self.task_func = task_func
        self.stop_func = stop_func
        self.task_args = args
        self.task_kwargs = kwargs

    def compose_body(self) -> Iterable[Widget]:
        banner_text = "[$text]Status:[/] [$primary]Active[/] - [$warning]Press Escape to stop[/]"
        yield Static(banner_text, id="status_banner")
        yield RichLog(id="watch_log", highlight=False, markup=True)

    def on_mount(self) -> None:
        log_widget = self.query_one("#watch_log", RichLog)

        def write_log(message: str) -> None:
            with contextlib.suppress(Exception):
                self.app.call_from_thread(log_widget.write, message)

        self.run_worker(lambda: self._run_watch(write_log), thread=True)

    def on_unmount(self) -> None:
        if self.stop_func:
            self.stop_func()

    def _run_watch(self, write_log: Callable[[str], None]) -> None:
        handler = TuiLogHandler(write_log)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            self.task_func(*self.task_args, **self.task_kwargs)
        except Exception as e:
            logger.exception("Watcher error")
            logger.error(f"[red]Watcher error: {e}[/red]")
        finally:
            root_logger.removeHandler(handler)


class ConfirmScreen(BaseOptionListScreen):
    """Prompt user with standard Yes/No confirmation dialog."""

    nav_label: str = "CONFIRM"
    sub_title: str = "Confirmation Dialog"

    def __init__(
        self,
        prompt: str,
        on_confirm: Callable[[], Any],
        on_cancel: Callable[[], Any] | None = None,
        nav_label: str | None = None,
    ) -> None:
        super().__init__()
        if nav_label:
            self.nav_label = nav_label
        self.prompt = prompt
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel

    def compose_body(self) -> Iterable[Widget]:
        yield Static(f"[$warning]{self.prompt}[/]", id="summary")
        yield OptionList(id="options_list")

    def get_options(self) -> list[Option | None]:
        return [
            Option("[$error]✕  NO (CANCEL)[/]", id="opt_cancel"),
            None,
            Option("[$success]▶  YES (CONFIRM)[/]", id="opt_confirm"),
        ]

    def on_option_selected(self, option_id: str | None, option_prompt: str) -> None:
        self.app.pop_screen()
        if option_id == "opt_confirm":
            self.on_confirm()
        elif self.on_cancel:
            self.on_cancel()


class OptionSelectionScreen(BaseOptionListScreen):
    """Single-choice selector from a list of strings or tuples."""

    nav_label: str = "SELECT"

    def __init__(
        self,
        title: str,
        options: list[str] | list[tuple[str, str]],
        on_select: Callable[[str], Any],
        nav_label: str | None = None,
    ) -> None:
        super().__init__()
        self.sub_title = title
        if nav_label:
            self.nav_label = nav_label
        self._raw_options = options
        self.on_select = on_select

    def compose_body(self) -> Iterable[Widget]:
        summary_text = f"[$text]{self.sub_title}[/] - [$warning]Select an option to proceed:[/]"
        yield Static(summary_text, id="summary")
        yield OptionList(id="options_list")

    def get_options(self) -> list[Option | None]:
        opts: list[Option | None] = []
        for idx, item in enumerate(self._raw_options):
            if idx > 0:
                opts.append(None)
            if isinstance(item, tuple):
                opt_id, opt_label = item
            else:
                opt_id, opt_label = item, item
            opts.append(Option(f"[bold $text]>  {opt_label.upper()}[/]", id=opt_id))
        return opts

    def on_option_selected(self, option_id: str | None, option_prompt: str) -> None:
        self.app.pop_screen()
        if option_id is not None:
            self.on_select(option_id)


class TextPromptScreen(BaseScreen):
    """Single-line text prompt dialog with validation."""

    nav_label: str = "PROMPT"

    def __init__(
        self,
        title: str,
        placeholder: str,
        on_submit: Callable[[str], Any],
        allow_blank: bool = True,
        on_cancel: Callable[[], Any] | None = None,
        nav_label: str | None = None,
    ) -> None:
        super().__init__()
        self.sub_title = title
        if nav_label:
            self.nav_label = nav_label
        self._placeholder = placeholder
        self.on_submit = on_submit
        self.allow_blank = allow_blank
        self.on_cancel = on_cancel

    def compose_body(self) -> Iterable[Widget]:
        summary_text = f"[$text]{self.sub_title}[/] - [$warning]Enter a value to submit:[/]"
        yield Static(summary_text, id="summary")
        yield Input(placeholder=self._placeholder, id="text_input")

    def on_mount(self) -> None:
        self.query_one("#text_input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        if self.allow_blank or val:
            self.app.pop_screen()
            self.on_submit(val)

    def action_back(self) -> None:
        self.app.pop_screen()
        if self.on_cancel:
            self.on_cancel()


class InfoScreen(BaseOptionListScreen):
    """Informational modal displaying a notice and Dismiss option."""

    nav_label: str = "NOTICE"

    def __init__(
        self,
        message: str,
        title: str = "System Notice",
        nav_label: str | None = None,
    ) -> None:
        super().__init__()
        self.sub_title = title
        if nav_label:
            self.nav_label = nav_label
        self.message = message

    def compose_body(self) -> Iterable[Widget]:
        yield Static(f"[$text]{self.message}[/]", id="summary")
        yield OptionList(id="options_list")

    def get_options(self) -> list[Option | None]:
        return [Option("[$success]▶  DISMISS[/]", id="opt_dismiss")]

    def on_option_selected(self, option_id: str | None, option_prompt: str) -> None:
        self.app.pop_screen()
