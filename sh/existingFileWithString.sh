#!/bin/bash

findOccurenceInDirectoryFiles() {
    local stringToFind="$1" 
    local directorySearchedFor=$(echo "$2" | tr '\\' '/')

    echo -e "stringa da cercare: $stringToFind \n"
    echo -e "directory di ricerca: $directorySearchedFor/*\n"

    local countAllFiles=0
    local countCorrespondentFiles=0

    for file in "$directorySearchedFor"/*; do 
        #uso grep per trovare la stringa nel file
        if grep -q "$stringToFind" "$file"; then 
            ((countAllFiles++))
            ((countCorrespondentFiles++))
            fileWithoutPath="${file//$directorySearchedFor\//}"
            echo 
            echo "$countCorrespondentFiles) $fileWithoutPath"
        else
            ((countAllFiles++))
        fi
    done

    echo -e "\nsono stati analizzati $countAllFiles documenti e $countCorrespondentFiles sono quelli in cui è stata trovata una corrispondenza"
}

findOccurenceInDirectoryFiles "" ""

sleep 3
exit
