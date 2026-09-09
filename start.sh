#!/bin/sh
# Runs alongside python server.py in the same container. cloudflared is
# backgrounded and unsupervised on purpose - it mirrors the old manual
# deployment's shape (two independently-supervised processes), so a tunnel
# hiccup does not take FastAPI down. Container restart policy in Dokploy
# is the equivalent of what launchd provided in that setup.
set -e
cloudflared tunnel --url http://127.0.0.1:8000 &
exec python server.py
