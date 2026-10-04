#!/usr/bin/env bash
# Single verification entry point, used locally and by CI:
# lint every shell script and syntax-check every JavaScript file.
set -euo pipefail
cd "$(dirname "$0")"

status=0

while IFS= read -r -d '' file; do
    shellcheck "$file" || status=1
done < <(git ls-files -z -- '*.sh')

while IFS= read -r -d '' file; do
    node --check "$file" || status=1
done < <(git ls-files -z -- '*.js')

exit "$status"
