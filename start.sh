#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f config.env ]; then
  echo "Не найден config.env"
  exit 1
fi

while IFS= read -r line || [ -n "$line" ]; do
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"

  if [ -z "$line" ] || [[ "$line" == \#* ]]; then
    continue
  fi

  key="${line%%=*}"
  value="${line#*=}"
  export "$key=$value"
done < config.env

python3 bot.py
