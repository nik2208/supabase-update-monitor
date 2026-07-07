SUPABASE MONITOR - QUICK START GUIDE
=====================================

FILE INSTALLATI:
  /home/docker/github/supabase-monitor/supabase_monitor.py      (script principale)
  /home/docker/github/supabase-monitor/supabase-monitor.sh      (wrapper bash per cron)
  /home/docker/github/supabase-monitor/.env.supabase-monitor    (file configurazione)


STEP 1: CONFIGURARE LE CREDENZIALI (OPZIONALE MA CONSIGLIATO)
-------------------------------------------------------------

  Apri il file di configurazione:
  $ nano /home/docker/github/supabase-monitor/.env.supabase-monitor

  Compila i campi VUOTI.

  Per LiteLLM (analisi AI):
    - LITELLM_API_KEY = la tua API key
    - LITELLM_BASE_URL = URL del server LiteLLM

  Per SMTP (notifiche email):
    - SMTP_USER / SMTP_PASSWORD / MAIL_FROM / MAIL_TO


STEP 2: TEST MANUALE
--------------------

  $ cd /home/docker/github/supabase-monitor
  $ source .env.supabase-monitor
  $ /usr/bin/python3 supabase_monitor.py


STEP 3: CONFIGURARE CRON (AUTOMAZIONE)
--------------------------------------

  $ crontab -e

  Aggiungi una di queste righe:

  Ogni giorno alle 2 AM:
  0 2 * * * /home/docker/github/supabase-monitor/supabase-monitor.sh

  Ogni lunedì mattina:
  0 8 * * 1 /home/docker/github/supabase-monitor/supabase-monitor.sh


STEP 4: VERIFICA CRON
---------------------

  $ tail -f /tmp/supabase-monitor.log

  Output atteso:
  2026-07-05 02:00:00 - Avvio monitoraggio Supabase
  2026-07-05 02:00:30 - Fine monitoraggio (exit code: 0)


FILE IMPORTANTI:
----------------
  Log:         tail -f /tmp/supabase-monitor.log
  Stato:       cat ~/.supabase_monitor/version_info.json
  Config:      cat /home/docker/github/supabase-monitor/.env.supabase-monitor


TROUBLESHOOTING RAPIDO:
------------------------
  P: Lo script non trova il docker-compose.yml
  R: Verifica COMPOSE_DIR in .env.supabase-monitor

  P: "LITELLM_API_KEY non configurato"
  R: Opzionale, lo script funziona comunque

  P: Cron non esegue
  R: Verifica che il wrapper sia eseguibile e i path assoluti siano corretti
