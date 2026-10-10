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

# explicit file list: what `node --test <directory>` picks up changes between Node versions
js_tests=()
while IFS= read -r -d '' file; do
    js_tests+=("$file")
done < <(git ls-files -z -- 'tests/*.test.js')
if [ "${#js_tests[@]}" -gt 0 ]; then
    node --test "${js_tests[@]}" || status=1
fi

exit "$status"
