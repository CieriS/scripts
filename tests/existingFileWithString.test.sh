#!/usr/bin/env bash
# Tests for sh/existingFileWithString.sh on a temporary directory of synthetic files.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

script="sh/existingFileWithString.sh"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
failures=0

check() { # check <description> <expected> <actual>
    if [ "$2" = "$3" ]; then
        echo "ok   $1"
    else
        echo "FAIL $1: expected '$2', got '$3'"
        failures=$((failures + 1))
    fi
}

contains() { # contains <description> <needle> <haystack>
    case "$3" in
        *"$2"*) echo "ok   $1" ;;
        *) echo "FAIL $1: '$2' not found in output"; failures=$((failures + 1)) ;;
    esac
}

mkdir -p "$tmp/data/nested" "$tmp/empty" "$tmp/with space"
echo "alpha needle beta" > "$tmp/data/a.txt"
echo "nothing here" > "$tmp/data/b.txt"
echo "a.c literal" > "$tmp/data/c.txt"
echo "abc regex bait" > "$tmp/data/d.txt"
echo "needle in a subdirectory" > "$tmp/data/nested/e.txt"
echo "needle" > "$tmp/with space/f g.txt"

out=$(bash "$script" needle "$tmp/data"); status=$?
check "match: exit code" 0 "$status"
contains "match: lists the matching file" "1) a.txt" "$out"
contains "match: counts files, skipping the subdirectory" "analizzati 4 documenti e 1 sono" "$out"

out=$(bash "$script" missing "$tmp/data"); status=$?
check "no match: exit code" 0 "$status"
contains "no match: zero matches" "analizzati 4 documenti e 0 sono" "$out"

out=$(bash "$script" "a.c" "$tmp/data")
contains "literal match: the dot is not a wildcard" "analizzati 4 documenti e 1 sono" "$out"
contains "literal match: right file" "1) c.txt" "$out"

out=$(bash "$script" "-e" "$tmp/data"); status=$?
check "string starting with a dash: exit code" 0 "$status"
contains "string starting with a dash: not taken as an option" "e 0 sono" "$out"

out=$(bash "$script" needle "$tmp/empty"); status=$?
check "empty directory: exit code" 0 "$status"
contains "empty directory: nothing scanned" "analizzati 0 documenti e 0 sono" "$out"

out=$(bash "$script" needle "$tmp/with space")
contains "spaces in directory and file names" "1) f g.txt" "$out"

out=$(bash "$script" needle "$tmp/data/")
contains "trailing slash" "1) a.txt" "$out"

out=$(cd "$tmp" && bash "$OLDPWD/$script" needle 'data\nested')
contains "backslashes in the path become slashes" "1) e.txt" "$out"

out=$(bash "$script" needle "$tmp/does-not-exist" 2>&1); status=$?
check "missing directory: exit code" 1 "$status"
contains "missing directory: message" "directory non trovata" "$out"

out=$(bash "$script" 2>&1); status=$?
check "no arguments: exit code" 2 "$status"
contains "no arguments: usage" "uso:" "$out"

bash "$script" needle >/dev/null 2>&1; check "one argument: exit code" 2 "$?"
bash "$script" "" "$tmp/data" >/dev/null 2>&1; check "empty string: exit code" 2 "$?"
bash "$script" a b c >/dev/null 2>&1; check "three arguments: exit code" 2 "$?"

if [ "$failures" -gt 0 ]; then
    echo "$failures test(s) failed"
    exit 1
fi
echo "all tests passed"
