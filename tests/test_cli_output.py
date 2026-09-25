"""CLI output when stdout is not a console. Only a real pipe shows the Windows behaviour (the ANSI code
page, cp1252 here), so these run Python in a subprocess. Nothing here touches the network."""

import os
import subprocess
import sys

import pytest

TEXT = "a ≈ b ‑ c"  # ≈ and U+2011 are not in cp1252


def run_python(code, *, encoding):
    env = {name: value for name, value in os.environ.items() if name not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    if encoding is not None:
        env["PYTHONIOENCODING"] = encoding  # what Windows gives a redirected stdout, on any platform
    return subprocess.run([sys.executable, "-c", code], capture_output=True, env=env, timeout=60)


ENCODINGS = pytest.mark.parametrize("encoding", [None, "cp1252"], ids=["native", "cp1252"])


@ENCODINGS
def test_utf8_output_lets_a_redirected_stdout_print_any_character(encoding):
    code = f"from stickman import cli; cli._utf8_output(); cli.console.print({TEXT!r})"
    result = run_python(code, encoding=encoding)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert TEXT in result.stdout.decode("utf-8")


@ENCODINGS
def test_every_command_writes_utf8_when_redirected(encoding):
    """The app's callback runs before every command, so a command's output reaches a pipe as UTF-8."""
    code = (
        "from stickman import cli\n"
        "@cli.app.command('probe')\n"
        "def probe():\n"
        f"    cli.console.print({TEXT!r})\n"
        "cli.app(['probe'])\n"
    )
    result = run_python(code, encoding=encoding)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert TEXT in result.stdout.decode("utf-8")
