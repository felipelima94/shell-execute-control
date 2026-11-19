#!/usr/bin/env bash
# Run as root inside a disposable distribution environment.
set -euo pipefail

format=${1:?}
first=${2:?}
upgrade=${3:?}
app=shell-execute-control
app_dir="/usr/lib/$app"
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
chown nobody "$work"

case "$format" in
    deb)
        installer=(apt-get install -y --no-install-recommends)
        expected=$(dpkg-deb -f "$upgrade" Version)
        ;;
    rpm)
        installer=(dnf install -y)
        expected=$(rpm -qp --qf '%{VERSION}-%{RELEASE}' "$upgrade")
        ;;
    *)
        exit 2
        ;;
esac

as_user=(runuser -u nobody -- env "HOME=$work" "XDG_CONFIG_HOME=$work/config"
    "XDG_STATE_HOME=$work/state" "PYTHONPATH=$app_dir")

test_data() {
    "${as_user[@]}" /usr/bin/python3 - "$1" <<'PY'
import sys
import tkinter
from pathlib import Path

import app
import sudo_support
from runtime import APP_ID, Command, Registry, xdg_path

assert Path(app.__file__).parent == Path("/usr/lib") / APP_ID
assert sudo_support.HERE == Path(app.__file__).parent
assert tkinter.Tcl().eval("info patchlevel")
config = xdg_path("XDG_CONFIG_HOME", ".config") / APP_ID
state = xdg_path("XDG_STATE_HOME", ".local/state") / APP_ID
registry = Registry(config, state)
try:
    command = Command("a" * 32, "API de teste", "", "echo pronto", True)
    commands = {command.id: command}
    log = registry.log(command.id)
    if sys.argv[1] == "seed":
        registry.save(commands)
        log.write_text("log anterior\n", encoding="utf-8")
    else:
        assert registry.load() == commands
        assert log.read_text(encoding="utf-8") == "log anterior\n"
finally:
    registry.close()
PY
}

cd "$work"
"${installer[@]}" "$first"
test_data seed
"${installer[@]}" "$upgrade"
if [[ "$format" == deb ]]; then
    actual=$(dpkg-query -W -f '${Version}' "$app")
else
    actual=$(rpm -q --qf '%{VERSION}-%{RELEASE}' "$app")
fi
[[ "$actual" == "$expected" ]]
test_data check

desktop="/usr/share/applications/$app.desktop"
desktop-file-validate "$desktop"
grep -Fx "Exec=$app" "$desktop"
[[ "$(command -v "$app")" == "/usr/bin/$app" ]]
for executable in "/usr/bin/$app" "$app_dir/sudo_askpass"; do
    [[ "$(stat -c '%u:%g %a' "$executable")" == "0:0 755" ]]
done
[[ "$(stat -c '%u:%g %a' "$desktop")" == "0:0 644" ]]
for module in app.py runtime.py sudo_support.py; do
    [[ "$(stat -c '%u:%g %a' "$app_dir/$module")" == "0:0 644" ]]
done

# Run the application suite against the installed modules on the Ubuntu image.
if [[ "${RUN_GUI_TESTS:-0}" == 1 ]]; then
    cp "$source_dir"/test_{panel,sudo}.py "$work/"
    "${as_user[@]}" xvfb-run -a /usr/bin/python3 -m unittest discover -v
fi
