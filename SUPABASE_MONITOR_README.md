# Supabase Self-Hosted Update Monitor

Sistema automatico di monitoraggio degli aggiornamenti per Supabase self-hosted. Confronta i `docker-compose.yml` locali con il repo GitHub ufficiale, analizza i cambiamenti via AI e invia notifiche email con runbook dettagliata.

## Requisiti

- Python 3.7+
- `requests`, `pyyaml` (vedi `requirements.txt`)
- (Opzionale) LiteLLM API per analisi AI
- (Opzionale) Account SMTP per notifiche email

## File Installati

- `/home/docker/github/supabase-monitor/supabase_monitor.py` - Script principale Python
- `/home/docker/github/supabase-monitor/supabase-monitor.sh` - Script wrapper bash per cron
- `/home/docker/github/supabase-monitor/.env.supabase-monitor` - File di configurazione

## Installazione e Configurazione

### 1. Configurare il file `.env.supabase-monitor`

```bash
nano /home/docker/github/supabase-monitor/.env.supabase-monitor
```

#### LITELLM (Opzionale - per analisi AI):
```bash
export LITELLM_BASE_URL="http://localhost:4000"
export LITELLM_API_KEY="sk-..."  # Sostituisci con la tua API key
export LITELLM_MODEL="groq/llama-3.3-70b-versatile"
```

#### SMTP (Opzionale - per notifiche email):
```bash
export SMTP_SERVER="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USER="tua-email@gmail.com"
export SMTP_PASSWORD="app-password-generato"
export MAIL_FROM="tua-email@gmail.com"
export MAIL_TO="destinatario@example.com,altro@example.com"
```

### 2. Test manuale

```bash
cd /home/docker/github/supabase-monitor
source .env.supabase-monitor
/usr/bin/python3 supabase_monitor.py
```

### 3. Configurare Cron

```bash
crontab -e
```

Esempi di schedule:

```bash
# Ogni giorno alle 2:00 AM
0 2 * * * /home/docker/github/supabase-monitor/supabase-monitor.sh

# Ogni lunedì alle 8:00 AM
0 8 * * 1 /home/docker/github/supabase-monitor/supabase-monitor.sh
```

## Output e Log

### File di Log:
```bash
tail -f /tmp/supabase-monitor.log
```

## Come Funziona

1. **Lettura file locali**: Carica `docker-compose.yml`, `docker-compose.s3.yml` e `.env`
2. **Download da GitHub**: Scarica gli stessi file dal repo ufficiale supabase/supabase (master)
3. **Confronto strutturato**: Genera diff testuali e confronta versione per versione ogni servizio
4. **Git Compare API**: Recupera i commit reali tra la versione deployata e quella disponibile
5. **Analisi AI** (opzionale): Invia i cambiamenti a LiteLLM per valutare rischio e generare raccomandazioni
6. **Generazione runbook**: Crea una guida passo-passo HTML per l'aggiornamento manuale
7. **Notifica email** (opzionale): Invia la runbook via email
8. **Salva stato**: Memorizza gli SHA dei file per il confronto successivo

## Cosa Viene Monitorato

- `docker-compose.yml` - Versioni immagini di tutti i servizi Supabase
- `docker-compose.s3.yml` - Versioni immagini servizi S3 (se presente)
- `.env` vs `.env.example` - Nuove variabili d'ambiente richieste o rimosse
- Commit reali tra versione deployata e master via GitHub Compare API
- Changelog ufficiale da GitHub

## Sicurezza

- Le credenziali sono nel file `.env.supabase-monitor`, NON nel codice Python
- Usa permission restrittive: `chmod 600 /home/docker/github/supabase-monitor/.env.supabase-monitor`

---

**Ultima modifica**: 2026-07-05
