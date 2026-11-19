#!/usr/bin/env bash
set -euo pipefail

format=${1:?Informe o formato deb ou rpm.}
tag=${2:?Informe a tag da release.}
output=${3:?Informe a pasta de saída.}

if [[ ! "$tag" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
    printf 'Tag inválida: %s. Publique uma release estável com uma tag como v1.2.3.\n' "$tag" >&2
    exit 1
fi

version=${tag#v}
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
app=shell-execute-control
build=$(mktemp -d)
trap 'rm -rf -- "$build"' EXIT
mkdir -p -- "$output"
output=$(cd -- "$output" && pwd)

case "$format" in
    deb)
        root="$build/deb"
        bash "$source_dir/packaging/stage.sh" "$root"
        install -d -m 0755 "$root/DEBIAN"
        cat > "$root/DEBIAN/control" <<CONTROL
Package: $app
Version: $version
Section: utils
Priority: optional
Architecture: all
Maintainer: Shell Execute Control <felipelima94@users.noreply.github.com>
Depends: bash, python3 (>= 3.10), python3-tk, sudo
Homepage: https://github.com/felipelima94/shell-execute-control
Description: Painel para executar comandos Bash e acompanhar seus logs
 Gerencia comandos em segundo plano e exibe a saída padrão e os erros
 de cada execução no desktop Linux.
CONTROL
        chmod 0644 "$root/DEBIAN/control"
        dpkg-deb --root-owner-group --build "$root" "$output/${app}_${version}_all.deb"
        ;;
    rpm)
        rpmbuild -bb --define "_topdir $build" --define "_tmppath $build" \
            --define "source_dir $source_dir" \
            --define "package_version $version" "$source_dir/packaging/$app.spec"
        install -m 0644 "$build/RPMS/noarch/$app-$version-1.noarch.rpm" "$output/"
        ;;
    *)
        printf 'Formato inválido: %s. Use deb ou rpm.\n' "$format" >&2
        exit 1
        ;;
esac
