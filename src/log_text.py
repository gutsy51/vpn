import shutil
import subprocess

from textual import events
from textual.binding import Binding
from textual.widgets import TextArea


class LogText(TextArea):
    BINDINGS = [Binding("ctrl+c,ctrl+shift+c,f8", "copy", "Копировать", show=False)]

    def action_copy(self) -> None:
        text = self.selected_text or self.text
        if not text:
            return

        clip = shutil.which("clip.exe")
        if clip is None:
            self.app.copy_to_clipboard(text)
            return

        try:
            subprocess.run([clip], input=text.encode("utf-16"), check=True, timeout=2)
        except (OSError, subprocess.SubprocessError):
            self.notify("Не удалось записать текст в буфер Windows", severity="error")
        else:
            self.notify("Журнал скопирован")

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button == 3:
            event.prevent_default()
            event.stop()
            self.action_copy()
