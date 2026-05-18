#!/usr/bin/env sh
set -eu

docker compose -f infra/docker-compose.yml up -d --build bot
docker compose -f infra/docker-compose.yml ps
docker compose -f infra/docker-compose.yml logs --tail=100 bot
