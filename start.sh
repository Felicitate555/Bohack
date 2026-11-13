#!/bin/sh
set -eu
cd "$(dirname "$0")"
command -v docker >/dev/null || { echo 'Install and start Docker first.'; exit 1; }
docker compose up -d --build
echo 'VibeLab: http://localhost:3000'
