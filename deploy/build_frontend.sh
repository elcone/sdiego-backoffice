#!/usr/bin/env bash
# LOCAL workstation only. Never run this script on the VPS.
set -euo pipefail
if [[ $# != 3 || ! $2 =~ ^[0-9a-f]{40}$ ]]; then
    echo 'Uso local: build_frontend.sh REPOSITORIO SHA_COMPLETO DIRECTORIO_SALIDA' >&2
    exit 2
fi
source_repo=$(realpath "$1")
revision=$2
output_dir=$(realpath -m "$3")
[[ $(git -C "$source_repo" rev-parse --verify "$revision^{commit}") == "$revision" ]]
mkdir -p "$output_dir"
[[ ! -e "$output_dir/$revision.tar.gz" ]]
build_dir=$(mktemp -d)
trap 'rm -rf -- "$build_dir"' EXIT
mkdir "$build_dir/source"
git -C "$source_repo" archive "$revision" | tar -x -C "$build_dir/source"
cd "$build_dir/source"
npm ci
node --test src/api/*.test.js src/auth/*.test.js src/router/*.test.js src/views/*.test.js
npm run build
python3 - <<'PY'
from pathlib import Path
import re
root = Path('dist')
assert (root / 'index.html').is_file(), 'Falta index.html'
assert (root / 'assets').is_dir(), 'Faltan assets'
for path in root.rglob('*'):
    assert not path.is_symlink(), 'No se permiten enlaces en dist'
    assert not path.name.startswith('.'), 'No se permiten archivos ocultos'
    assert path.suffix not in {'.map', '.py', '.sql', '.env'}, 'Archivo no publicable'
    assert path.relative_to(root).parts[0] in {'index.html', 'assets'}, 'Contenido inesperado'
for asset in re.findall(r'(?:src|href)="(/assets/[^\"]+)"', (root / 'index.html').read_text()):
    assert (root / asset.lstrip('/')).is_file(), 'Asset faltante'
print('Contenido de dist validado; sin archivos ocultos ni archivos de backend incluidos.')
PY
tar -czf "$output_dir/$revision.tar.gz" -C dist .
cd "$output_dir"
sha256sum "$revision.tar.gz" > "$revision.tar.gz.sha256"
printf 'Artefacto local: %s/%s.tar.gz\n' "$output_dir" "$revision"
