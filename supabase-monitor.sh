#!/bin/bash
# Script wrapper per eseguire il monitor Supabase con le variabili di ambiente corrette

# Carica le variabili di ambiente
if [ -f "/home/docker/.env.supabase-monitor" ]; then
    source /home/docker/.env.supabase-monitor
else
    echo "✗ File di configurazione non trovato: /home/docker/.env.supabase-monitor"
    exit 1
fi

# Crea il file di log se non esiste
LOG_DIR=$(dirname "$LOG_FILE")
if [ ! -d "$LOG_DIR" ]; then
    mkdir -p "$LOG_DIR" 2>/dev/null || LOG_FILE="/tmp/supabase-monitor.log"
fi

# Esegui lo script Python con logging
echo "$(date '+%Y-%m-%d %H:%M:%S') - Avvio monitoraggio Supabase" >> "$LOG_FILE"
/usr/bin/python3 /home/docker/supabase_monitor.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?
echo "$(date '+%Y-%m-%d %H:%M:%S') - Fine monitoraggio (exit code: $EXIT_CODE)" >> "$LOG_FILE"

exit $EXIT_CODE
