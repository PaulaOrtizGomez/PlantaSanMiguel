"""Command-line utility for administrative Django tasks."""

import os
import sys


def main():
    """Run Django administrative commands.

    Parameters:
        None.

    Returns:
        None. Delegates execution to Django's command-line handler.

    Raises:
        ImportError: If Django is not installed in the active environment.
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django is not installed. Activate your virtual environment and run "
            "'pip install -r requirements.txt'."
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
