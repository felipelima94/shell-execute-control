# Repository Guidelines

## Project Structure

Painel de APIs is a Linux desktop application for running Bash commands and watching their output. Source and tests share the repository root.

- `app.py` contains the Tkinter dashboard, command editor, and log viewer.
- `runtime.py` defines `Command`, persists commands through `Registry`, and manages process groups through `Supervisor`.
- `sudo_support.py` handles elevated execution and authentication. The executable `sudo_askpass` forwards password prompts to the panel.
- `install_launcher.py` generates the desktop menu entry using the system’s `utilities-terminal` icon.

## Development Commands

Run these commands from the repository root on Linux with system Python 3, Tkinter, and `/bin/bash` available. The application runs directly from source.

- `/usr/bin/python3 app.py` opens the dashboard.
- `/usr/bin/python3 -m unittest discover -v` runs the full test suite.
- `/usr/bin/python3 install_launcher.py` installs the current user's menu entry. Run it again after moving the checkout because the entry records the application path.

## Coding Style

Use four-space indentation, `snake_case` for functions and variables, `PascalCase` for classes, and `UPPER_CASE` for module constants. Follow the surrounding Python style when formatting changes. The repository has no configured formatter or linter. Keep user-facing messages in Portuguese to match the dashboard and README.

## Testing Guidelines

Use standard-library `unittest`. Name modules `test_*.py` and methods `test_<behavior>`. Add registry, process, and dashboard cases to `test_panel.py`. Run display-dependent checks in a graphical session because the GUI test skips when Tk cannot open a display.

For authentication changes, run `/usr/bin/python3 -m unittest -v test_sudo`. Reuse its fake sudo executable and temporary directories so tests run as an ordinary user. Keep passwords out of command logs and saved configuration. No numeric coverage threshold is configured.

## Commits and Pull Requests

This checkout has no commit history to establish a message convention. Use a short imperative subject such as `Preserve the previous log after sudo denial`. In a pull request, describe the behavior change and report the tests run. Link a related issue when one exists and include a screenshot for visible dashboard changes.
