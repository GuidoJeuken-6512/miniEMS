#!/usr/bin/with-contenv bashio

set -e

bashio::log.info "Starting miniEMS..."

exec python3 /usr/bin/main.py
