#!/bin/sh
release_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
/bin/sh "$release_root/Open Job Finder.sh"
result=$?
if [ "$result" -ne 0 ]; then
  printf '\n%s\n' 'Job Finder could not start. Read the error above. Press Return to close.'
  read -r answer
fi
exit "$result"
