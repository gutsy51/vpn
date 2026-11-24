import os
import shutil
import sys

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import choice


def interactive():
    return (
        sys.stdin.isatty()
        and sys.stdout.isatty()
        and (os.name == "nt" or os.environ.get("TERM", "dumb") != "dumb")
        and shutil.get_terminal_size().lines >= 9
    )


def accent(text, code="1;36"):
    if interactive() and "NO_COLOR" not in os.environ:
        return f"\x1b[{code}m{text}\x1b[0m"

    return text


def pick(title, options, selected=0, shortcuts=None):
    bindings = KeyBindings()

    def bind(key, result):
        @bindings.add(key, eager=True)
        def select(event):
            event.app.exit(result=result)

    for key in ("escape", "left", "q", "0", "c-d"):
        bind(key, None)

    for index in range(min(9, len(options))):
        bind(str(index + 1), index)

    for key, index in (shortcuts or {}).items():
        bind(key.decode(), index)

    return choice(title, options=list(enumerate(options)), default=selected, key_bindings=bindings)
