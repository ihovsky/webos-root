#!/bin/zsh
cd "${0:A:h}"

if ! command -v python3 >/dev/null 2>&1; then
  osascript -e 'display dialog "Не найден Python 3. Установите Python 3 с python.org и запустите мастер снова." buttons {"OK"} default button "OK" with icon stop'
  exit 1
fi

exec python3 webos_root_wizard.py
