#!/usr/bin/python3
"""Instala a entrada do Painel de APIs no menu do usuário."""

from pathlib import Path

from runtime import APP_ID, xdg_path


def desktop_argument(value):
    # Exec quoting is applied after Desktop Entry string unescaping.
    argument = "".join("\\" + char if char in '\\"$`' else char for char in value)
    argument = argument.replace("%", "%%")
    argument = argument.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
    return f'"{argument}"'


def main():
    app = Path(__file__).resolve().with_name("app.py")
    menu = xdg_path("XDG_DATA_HOME", ".local/share") / "applications"
    menu.mkdir(parents=True, exist_ok=True)
    launcher = menu / f"{APP_ID}.desktop"
    launcher.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Painel de APIs\n"
        "Comment=Execute comandos Bash e acompanhe seus logs\n"
        f"Exec=/usr/bin/python3 {desktop_argument(str(app))}\n"
        "Icon=utilities-terminal\n"
        "Terminal=false\n"
        "Categories=Development;\n",
        encoding="utf-8",
    )
    print(launcher)


if __name__ == "__main__":
    main()
