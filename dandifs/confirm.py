# NOTICE
#   This file was copied and modified from [click]py,
#   which is distributed under the BSD 3 license.
#   See: https://github.com/pallets/click/blob/main/LICENSE.txt
"""A lightweight dropin for click.confirm()."""

import sys
import typing as t
from gettext import gettext


def confirm(
    text: str,
    default: bool | None = False,
    abort: bool = False,
    prompt_suffix: str = ": ",
    show_default: bool = True,
    err: bool = False,
) -> bool:
    """Prompts for confirmation (yes/no question).

    If the user aborts the input by sending a interrupt signal this
    function will catch it and raise a :exc:`Abort` exception.

    :param text: the question to ask.
    :param default: The default value to use when no input is given. If
        ``None``, repeat until input is given.
    :param abort: if this is set to `True` a negative answer aborts the
                  exception by raising :exc:`Abort`.
    :param prompt_suffix: a suffix that should be added to the prompt.
    :param show_default: shows or hides the default value in the prompt.
    :param err: if set to true the file defaults to ``stderr`` instead of
                ``stdout``, the same as with echo.

    .. versionchanged:: 8.0
        Repeat until input is given if ``default`` is ``None``.

    .. versionadded:: 4.0
        Added the ``err`` parameter.
    """
    prompt = _build_prompt(
        text,
        prompt_suffix,
        show_default,
        "y/n" if default is None else ("Y/n" if default else "y/N"),
    )

    while True:
        try:
            # Write the prompt separately so that we get nice
            # coloring through colorama on Windows
            _echo(prompt.rstrip(" "), nl=False, err=err)
            # Echo a space to stdout to work around an issue where
            # readline causes backspace to clear the whole line.
            value = input(" ").lower().strip()
        except (KeyboardInterrupt, EOFError):
            raise RuntimeError() from None
        if value in ("y", "yes"):
            rv = True
        elif value in ("n", "no"):
            rv = False
        elif default is not None and value == "":
            rv = default
        else:
            _echo(gettext("Error: invalid input"), err=err)
            continue
        break
    if abort and not rv:
        raise RuntimeError()
    return rv


def _echo(
    message: t.Any | None = None,
    file: t.IO[t.Any] | None = None,
    nl: bool = True,
    err: bool = False,
) -> None:
    """Print a message and newline to stdout or a file. This should be
    used instead of :func:`print` because it provides better support
    for different data, files, and environments.

    Compared to :func:`print`, this does the following:

    -   Ensures that the output encoding is not misconfigured on Linux.
    -   Supports Unicode in the Windows console.
    -   Supports writing to binary outputs, and supports writing bytes
        to text outputs.
    -   Supports colors and styles on Windows.
    -   Removes ANSI color and style codes if the output does not look
        like an interactive terminal.
    -   Always flushes the output.

    :param message: The string or bytes to output. Other objects are
        converted to strings.
    :param file: The file to write to. Defaults to ``stdout``.
    :param err: Write to ``stderr`` instead of ``stdout``.
    :param nl: Print a newline after the message. Enabled by default.
    """
    if file is None:
        if err:
            file = sys.stderr
        else:
            file = sys.stdin

        # There are no standard streams attached to write to. For example,
        # pythonw on Windows.
        if file is None:
            return

    # Convert non bytes/text into the native string type.
    if (
        message is not None and
        not isinstance(message, (str, bytes, bytearray))
    ):
        out: str | bytes | bytearray | None = str(message)
    else:
        out = message

    if nl:
        out = out or ""
        if isinstance(out, str):
            out += "\n"
        else:
            out += b"\n"

    if not out:
        file.flush()
        return

    file.write(out)  # type: ignore
    file.flush()


def _build_prompt(
    text: str,
    suffix: str,
    show_default: bool = False,
    default: t.Any | None = None,
) -> str:
    prompt = text
    if default is not None and show_default:
        prompt = f"{prompt} [{default}]"
    return f"{prompt}{suffix}"
