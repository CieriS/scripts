#!/usr/bin/env bash
# Single verification entry point, used locally and by CI:
# lint every shell script, syntax-check every JavaScript file, run the Bash and Node tests.
set -euo pipefail
cd "$(dirname "$0")"

status=0

while IFS= read -r -d '' file; do
    shellcheck "$file" || status=1
done < <(git ls-files -z -- '*.sh')

while IFS= read -r -d '' file; do
    node --check "$file" || status=1
done < <(git ls-files -z -- '*.js')

while IFS= read -r -d '' file; do
    bash "$file" || status=1
done < <(git ls-files -z -- 'tests/*.test.sh')

if git ls-files --error-unmatch 'tests/*.test.js' >/dev/null 2>&1; then
    node --test tests/ || status=1
fi

exit "$status"
