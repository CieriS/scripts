#!/bin/bash
# Lists the files directly inside a directory (not recursive) that contain a string.
# The string is matched literally, not as a regular expression.
#
# Usage: existingFileWithString.sh <string> <directory>
# Exit codes: 0 scan completed, 1 directory not found, 2 wrong arguments.

findOccurenceInDirectoryFiles() {
    local stringToFind="$1"
    local directorySearchedFor
    # shellcheck disable=SC1003 # a literal backslash, not an escaped quote
    directorySearchedFor=$(echo "$2" | tr '\\' '/')
    directorySearchedFor="${directorySearchedFor%/}"

    if [ ! -d "$directorySearchedFor" ]; then
        echo "directory non trovata: $directorySearchedFor" >&2
        return 1
    fi

    echo -e "stringa da cercare: $stringToFind \n"
    echo -e "directory di ricerca: $directorySearchedFor/*\n"

    local countAllFiles=0
    local countCorrespondentFiles=0
    local file

    for file in "$directorySearchedFor"/*; do
        # only regular files: skips subdirectories and the unexpanded glob of an empty directory
        [ -f "$file" ] || continue
        ((countAllFiles++))
        if grep -qF -- "$stringToFind" "$file"; then
            ((countCorrespondentFiles++))
            echo
            echo "$countCorrespondentFiles) ${file##*/}"
        fi
    done

    echo -e "\nsono stati analizzati $countAllFiles documenti e $countCorrespondentFiles sono quelli in cui è stata trovata una corrispondenza"
}

if [ "$#" -ne 2 ] || [ -z "$1" ]; then
    echo "uso: $(basename "$0") <stringa> <directory>" >&2
    exit 2
fi

findOccurenceInDirectoryFiles "$1" "$2"
