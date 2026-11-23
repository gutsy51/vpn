from pathlib import Path

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from .config import Node, add_node


class AddServer(ModalScreen[Node | None]):
    BINDINGS = [("escape", "cancel", "Отмена")]
    CSS = """
    AddServer { align: center middle; }
    AddServer VerticalScroll { width: 64; max-width: 95%; height: auto; max-height: 95%;
        background: $surface; border: round $accent; padding: 1 2; }
    AddServer Input { height: 3; }
    """
    fields = (
        ("name", "Имя узла", ""),
        ("SSH_HOST", "Адрес SSH", ""),
        ("SSH_PORT", "Порт", "22"),
        ("SSH_USER", "Пользователь", ""),
        ("SSH_PASSWORD", "Пароль", ""),
        ("SERVICES", "Службы systemd через запятую", "x-ui"),
        ("SSH_KNOWN_HOSTS", "Файл known_hosts (необязательно)", ""),
    )

    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label("Добавить сервер")
            for key, label, default in self.fields:
                yield Label(label)
                yield Input(default, id=key, password=key == "SSH_PASSWORD")
            yield Static(id="error", markup=False)
            yield Button("Сохранить", id="save", variant="primary")
            yield Button("Отмена", id="cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_cancel()
            return

        values = {field.id: field.value for field in self.query(Input) if field.id}
        name = values.pop("name")

        try:
            node = add_node(self.path, name, values)
        except (OSError, ValueError) as error:
            self.query_one("#error", Static).update(str(error))
            return

        self.dismiss(node)
