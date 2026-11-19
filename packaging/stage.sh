#!/usr/bin/env bash
set -euo pipefail

root=${1:?Informe a pasta de preparação do pacote.}
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
app=shell-execute-control

install -d -m 0755 "$root/usr/lib/$app" "$root/usr/bin" "$root/usr/share/applications"
install -m 0644 "$source_dir"/{app.py,runtime.py,sudo_support.py} "$root/usr/lib/$app/"
install -m 0755 "$source_dir/sudo_askpass" "$root/usr/lib/$app/"
install -m 0755 "$source_dir/packaging/$app" "$root/usr/bin/$app"
install -m 0644 "$source_dir/packaging/$app.desktop" "$root/usr/share/applications/$app.desktop"
