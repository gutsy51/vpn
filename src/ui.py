import asyncio
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Label, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from .add_server import AddServer
from .bot_screen import BotLogs, ManagerBackend
from .config import Node
from .log_text import LogText
from .services import inspect_service


class Dashboard(App):
    TITLE = "VPN Manager"
    SUB_TITLE = "Службы Linux"
    BINDINGS = [
        Binding("1", "summary", "Сводка"),
        Binding("2", "status", "Состояние"),
        Binding("3", "logs", "Журнал"),
        Binding("4", "overview", "Обновить все"),
        Binding("5", "add", "Добавить"),
        Binding("6", "bots", "Боты"),
        Binding("tab", "focus_next", "Фокус"),
        Binding("0", "quit", "Выход"),
    ]
    CSS = """
    #nodes { width: 30%; min-width: 16; max-width: 38; }
    #nodes, #details { border: round $panel; padding: 0 1; }
    #nodes:focus-within, #details:focus-within { border: round $accent; }
    Label { color: $accent; text-style: bold; padding: 0 1; }
    OptionList { height: 1fr; }
    #target { height: auto; padding: 1; }
    RichLog { height: 1fr; padding: 0 1; }
    #server-log { height: 1fr; display: none; }
    #activity { height: auto; padding-left: 1; }
    .failure { color: $error; }
    """

    def __init__(
        self,
        nodes: list[Node],
        env_file: Path = Path(".env"),
        manager: ManagerBackend | None = None,
    ):
        super().__init__()
        self.targets = [(node, service) for node in nodes for service in node.services]
        self.env_file = env_file
        self.manager = manager
        self.results: dict[int, tuple[str, str]] = {}
        self.summary = True
        self.selected = 0
        self.logs = False
        self.revision = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="nodes"):
                yield Label("Узлы и службы")
                yield OptionList(*(Option(f"{node.name} / {unit}") for node, unit in self.targets))
            with Vertical(id="details"):
                yield Label("Состояние", id="view")
                yield Static(id="target", markup=False)
                yield RichLog(wrap=True, markup=False, highlight=False, auto_scroll=False)
                yield LogText(read_only=True, show_line_numbers=False, id="server-log")
                yield Static(id="activity", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()
        if self.manager is not None:
            self.draw_summary()
            self.action_bots()
        else:
            self.action_overview()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool:
        if isinstance(self.screen, (AddServer, BotLogs)) and action in {
            "bots",
            "summary",
            "overview",
            "status",
            "logs",
            "add",
            "quit",
        }:
            return False

        return True

    def action_bots(self) -> None:
        self.revision += 1
        self.workers.cancel_group(self, "ssh")
        self.push_screen(BotLogs(self.env_file, self.manager))

    def action_add(self) -> None:
        self.revision += 1
        self.workers.cancel_group(self, "ssh")
        self.push_screen(AddServer(self.env_file), self.server_added)

    def server_added(self, node: Node | None) -> None:
        if node is None:
            return

        options = self.query_one(OptionList)
        for unit in node.services:
            self.targets.append((node, unit))
            options.add_option(Option(f"{node.name} / {unit}"))

        self.action_overview()

    def action_summary(self) -> None:
        self.summary = True
        self.revision += 1
        self.workers.cancel_group(self, "ssh")
        self.draw_summary()

    def action_overview(self) -> None:
        self.action_summary()
        self.scan()

    def draw_summary(self) -> None:
        screen = self.screen_stack[0]
        screen.query_one("#view", Label).update("Сводка")
        screen.query_one("#target", Static).update("Последние результаты проверок")
        pane = screen.query_one(RichLog)
        pane.display = True
        screen.query_one("#server-log", LogText).display = False
        pane.clear()

        for index, (node, unit) in enumerate(self.targets):
            state, stamp = self.results.get(index, ("Ожидание", ""))
            line = Text(f"{node.name} / {unit}   ")
            if state.split(" / ", 1)[0] == "active":
                color = self.current_theme.success
            elif state == "Ожидание":
                color = self.current_theme.warning
            else:
                color = self.current_theme.error

            line.append(state, style=color)
            line.append(f"   {stamp}", style="dim")
            pane.write(line)

        message = (
            "Нет серверов. Нажмите 5 для добавления."
            if not self.targets
            else "4 - проверить все службы"
        )
        screen.query_one("#activity", Static).update(message)

    @work(exclusive=True, group="summary")
    async def scan(self) -> None:
        limit = asyncio.Semaphore(6)

        async def probe(index: int, node: Node, unit: str) -> None:
            async with limit:
                try:
                    details = await inspect_service(node, unit, logs=False)
                    fields = dict(
                        line.split("=", 1) for line in details.splitlines() if "=" in line
                    )
                    state = fields.get("ActiveState", details)
                    if fields.get("SubState"):
                        state += " / " + fields["SubState"]
                except ValueError as error:
                    state = str(error)

            self.results[index] = state, datetime.now().strftime("%H:%M:%S")
            if self.summary:
                self.draw_summary()

        await asyncio.gather(*(probe(i, node, unit) for i, (node, unit) in enumerate(self.targets)))

    def show_target(self) -> None:
        node, unit = self.targets[self.selected]
        self.revision += 1
        self.workers.cancel_group(self, "ssh")
        self.query_one("#target", Static).update(f"{node.name} / {unit}   SSH :{node.port}")
        self.query_one("#server-log", LogText).display = False
        self.logs = False
        self.query_one("#view", Label).update("Карточка службы")
        pane = self.query_one(RichLog)
        pane.display = True
        pane.clear()
        pane.write(f"Узел           {node.name}")
        pane.write(f"Служба         {unit}")
        pane.write(f"Транспорт      SSH, порт {node.port}")
        pane.write("Состояние      ещё не запрошено")
        self.activity("2 - состояние, 3 - журнал")

    def activity(self, text: str, failed: bool = False) -> None:
        widget = self.query_one("#activity", Static)
        widget.set_class(failed, "failure")
        widget.update(text)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self.selected = event.option_index
        if not self.summary:
            self.show_target()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.selected = event.option_index
        self.action_status()

    def action_status(self) -> None:
        self.summary = False
        self.logs = False
        self.action_refresh()

    def action_logs(self) -> None:
        self.summary = False
        self.logs = True
        self.action_refresh()

    def action_refresh(self) -> None:
        if not self.targets:
            self.action_summary()
            return

        self.revision += 1
        node, unit = self.targets[self.selected]
        self.query_one("#target", Static).update(f"{node.name} / {unit}")
        self.query_one("#view", Label).update("Журнал" if self.logs else "Состояние")
        pane = self.query_one(RichLog)
        pane.display = not self.logs
        pane.clear()
        log = self.query_one("#server-log", LogText)
        log.display = self.logs
        log.load_text("")
        self.activity("Запрос по SSH...")
        self.fetch(node, unit, self.logs, self.revision)

    @work(exclusive=True, group="ssh")
    async def fetch(self, node: Node, unit: str, logs: bool, revision: int) -> None:
        try:
            result = await inspect_service(node, unit, logs=logs)
        except ValueError as error:
            if revision == self.revision:
                self.activity(str(error), failed=True)

            return

        if revision == self.revision:
            if logs:
                pane = self.query_one("#server-log", LogText)
                pane.load_text(result)
                pane.scroll_end(animate=False)
            else:
                self.query_one(RichLog).write(result)

            self.activity("Обновлено в " + datetime.now().strftime("%H:%M:%S"))
