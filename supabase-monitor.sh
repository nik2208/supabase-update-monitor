#!/bin/bash
# Script wrapper per eseguire il monitor Supabase con le variabili di ambiente corrette

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Carica le variabili di ambiente
ENV_FILE="$SCRIPT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "✗ File di configurazione non trovato: $ENV_FILE"
    echo "  Crea una copia di .env.example: cp .env.example .env"
    exit 1
fi
source "$ENV_FILE"

# Crea il file di log se non esiste
LOG_DIR=$(dirname "$LOG_FILE")
if [ ! -d "$LOG_DIR" ]; then
    mkdir -p "$LOG_DIR" 2>/dev/null || LOG_FILE="/tmp/supabase-monitor.log"
fi

# Esegui lo script Python con logging
echo "$(date '+%Y-%m-%d %H:%M:%S') - Avvio monitoraggio Supabase" >> "$LOG_FILE"
"$SCRIPT_DIR/venv/bin/python3" "$SCRIPT_DIR/supabase_monitor.py" >> "$LOG_FILE" 2>&1
EXIT_CODE=$?
echo "$(date '+%Y-%m-%d %H:%M:%S') - Fine monitoraggio (exit code: $EXIT_CODE)" >> "$LOG_FILE"

exit $EXIT_CODE
