from pathlib import Path
from typing import Protocol

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Label, Static

from .log_text import LogText
from .manager_control import ManagerControl


class ManagerBackend(Protocol):
    async def snapshot(self) -> tuple[str, str]: ...

    async def change(self, action: str) -> None: ...


class BotLogs(ModalScreen):
    BINDINGS = [
        Binding("1", "manage('start')", "Запустить"),
        Binding("2", "manage('stop')", "Остановить"),
        Binding("3", "manage('restart')", "Перезапустить"),
        Binding("4", "refresh_log", "Обновить"),
        Binding("f8", "copy_log", "Копировать"),
        Binding("f12", "manage('stop')", "Аварийный стоп", priority=True),
        Binding("0,escape", "back", "Назад"),
    ]

    def __init__(self, env_file: Path, control: ManagerBackend | None = None):
        super().__init__()
        self.control = ManagerControl(env_file) if control is None else control
        self.busy = False
        self.refreshing = False
        self.content = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Боты / Менеджер")
        yield Static(id="manager-state", markup=False)
        yield Static(id="control-result", markup=False)
        yield Label("Журнал Telegram")
        yield LogText(read_only=True, show_line_numbers=False)
        yield Footer()

    def on_mount(self) -> None:
        self.action_refresh_log()
        self.set_interval(2, self.action_refresh_log)
        self.query_one(LogText).focus()

    def action_copy_log(self) -> None:
        self.query_one(LogText).action_copy()

    @work
    async def action_refresh_log(self) -> None:
        if self.refreshing:
            return

        self.refreshing = True
        status = self.query_one("#manager-state", Static)
        try:
            state, text = await self.control.snapshot()
        except (OSError, ValueError) as error:
            status.update(f"Менеджер: {error}")
            return
        finally:
            self.refreshing = False

        status.update(f"Менеджер: {state}")

        pane = self.query_one(LogText)
        if text != self.content and not pane.selected_text:
            self.content = text
            pane.load_text(text)
            pane.scroll_end(animate=False)

    def action_manage(self, action: str) -> None:
        if self.busy:
            return

        self.busy = True
        self.change_state(action)

    @work
    async def change_state(self, action: str) -> None:
        result = self.query_one("#control-result", Static)
        result.update("Выполняется операция...")

        try:
            await self.control.change(action)
            result.update("")
        except (OSError, ValueError) as error:
            result.update(str(error))
        finally:
            self.busy = False
            self.action_refresh_log()

    def action_back(self) -> None:
        if not self.busy:
            self.dismiss()
