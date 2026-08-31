"""What the ``hisim`` command line returns to the shell.

One class, in a module of its own, because every part of the command line needs it and the parts
otherwise import each other: the verbs, the renderers that answer two of them and the grouping
workflow all decide an exit code, and putting the codes in any one of those modules would make the
other two import it for that alone.
"""

# clean

from __future__ import annotations


class ExitCodes:
    """What the process returns, and what each value tells a script that called it.

    Three outcomes are worth distinguishing, and the values follow the convention every command
    line uses: zero for success, two for a caller that spelled the command wrong, and one for a
    command that was understood and whose subject was refused. A wrapper script therefore knows
    from the code alone whether to fix its invocation or to show the user a file error.
    """

    OK: int = 0
    FILE_REJECTED: int = 1
    USAGE: int = 2
