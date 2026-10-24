#!/bin/sh
# Resolve the adjacent app independently of the terminal's current directory.
set -eu
release_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "$(uname -s)" in
  Darwin) platform=macos ;;
  Linux) platform=linux ;;
  *) printf '%s\n' 'LAUNCH-PLATFORM: Use Open Job Finder.cmd on Windows.' >&2; exit 1 ;;
esac
case "$(uname -m)" in
  arm64|aarch64) architecture=arm64 ;;
  x86_64|amd64) architecture=x64 ;;
  *) printf '%s\n' 'LAUNCH-PLATFORM: This processor is not supported.' >&2; exit 1 ;;
esac
launcher="$release_root/app/launcher/bin/$platform-$architecture/launcher"
if [ ! -x "$launcher" ]; then
  printf '%s\n' 'LAUNCH-ROOT: Extract the complete Job Finder script ZIP, including its app folder.' >&2
  exit 1
fi
export GOOSNAV_RELEASE_ROOT="$release_root"
exec "$launcher"
