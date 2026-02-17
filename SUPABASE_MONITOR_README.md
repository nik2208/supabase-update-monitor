# Supabase Self-Hosted Update Monitor

Sistema automatico di monitoraggio degli aggiornamenti per Supabase self-hosted con analisi AI e notifiche email.

## 📋 Requisiti

- Python 3.7+
- `requests` library (già installata)
- Accesso a Docker Hub (pubblico, nessun auth richiesto)
- (Opzionale) LiteLLM API per analisi AI
- (Opzionale) Account Gmail con app password per notifiche email

## 📁 File Installati

- `/home/docker/supabase_monitor.py` - Script principale Python
- `/home/docker/supabase-monitor.sh` - Script wrapper bash per cron
- `/home/docker/.env.supabase-monitor` - File di configurazione

## 🚀 Installazione e Configurazione

### 1. Configurare il file `.env.supabase-monitor`

```bash
nano /home/docker/.env.supabase-monitor
```

Compila i seguenti campi:

#### LITELLM (Opzionale - per analisi AI):
```bash
export LITELLM_BASE_URL="http://localhost:4000"
export LITELLM_API_KEY="sk-..."  # Sostituisci con la tua API key
export LITELLM_MODEL="gpt-3.5-turbo"  # O il modello che preferisci
```

#### SMTP (Opzionale - per notifiche email):
```bash
export SMTP_SERVER="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USER="tua-email@gmail.com"
export SMTP_PASSWORD="app-password-generato"  # NOT password di Gmail!
export MAIL_FROM="tua-email@gmail.com"
export MAIL_TO="destinatario@example.com,altro@example.com"
```

**Per Gmail App Password:**
1. Vai su https://myaccount.google.com/apppasswords
2. Seleziona "Mail" e "Windows Computer" (o il tuo dispositivo)
3. Copia la password generata nel campo `SMTP_PASSWORD`

### 2. Test manuale

```bash
source /home/docker/.env.supabase-monitor
/usr/bin/python3 /home/docker/supabase_monitor.py
```

Output atteso:
```
============================================================
Supabase Self-Hosted Update Monitor
============================================================

Versioni attuali (13 servizi):
  • studio: 2025.12.17-sha-43f4f7f
  • kong: 2.8.1
  ...
```

### 3. Configurare Cron

Aggiungi il cron job:

```bash
crontab -e
```

Esempi di schedule:

```bash
# Ogni giorno alle 2:00 AM
0 2 * * * /home/docker/supabase-monitor.sh

# Due volte al giorno (2 AM e 2 PM)
0 2,14 * * * /home/docker/supabase-monitor.sh

# Ogni lunedì alle 8:00 AM
0 8 * * 1 /home/docker/supabase-monitor.sh

# Ogni 6 ore
0 */6 * * * /home/docker/supabase-monitor.sh
```

## 📊 Output e Log

### Console Output (durante esecuzione manuale):
```
============================================================
Supabase Self-Hosted Update Monitor
============================================================

Versioni attuali (13 servizi):
  • studio: 2025.12.17-sha-43f4f7f
  ...

[2026-02-17 14:34:40] Controllando nuove versioni...
  ✓ kong: 2.8.1 → latest
  ✓ postgres: 15.8.1.085 → 14.21-trixie

 Trovati 2 aggiornamenti

[2026-02-17 14:34:45] Scaricando changelog...
[2026-02-17 14:34:45] Analizzando con AI...
[2026-02-17 14:34:50] Inviando email...

 Email inviata a destinatario@example.com
 Monitoraggio completato
```

### File di Log:
```bash
tail -f /tmp/supabase-monitor.log  # o /var/log/supabase-monitor.log se configurato

# Output nel log:
2026-02-17 14:34:40 - Avvio monitoraggio Supabase
2026-02-17 14:34:50 - Fine monitoraggio (exit code: 0)
```

## 🔧 Troubleshooting

### "docker-compose.yml non trovato"
```bash
# Verifica il path
ls /home/docker/dockerCompose/supabase/supatest/docker-compose.yml
```

### "LITELLM_API_KEY non configurato"
Non è critico - il monitor funziona comunque senza analisi AI, mostra solo gli avvisi

### "Email non inviata"
Controlla:
```bash
# 1. Le credenziali SMTP sono valide?
grep SMTP /home/docker/.env.supabase-monitor

# 2. Se usi Gmail, hai generato un'app password?
# 3. Il server SMTP è raggiungibile?
```

### Test connessione SMTP:
```bash
python3 << 'EOF'
import smtplib
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "tua-email@gmail.com"
SMTP_PASSWORD = "app-password"

try:
    server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
    server.starttls()
    server.login(SMTP_USER, SMTP_PASSWORD)
    print("✓ Connessione SMTP OK")
    server.quit()
except Exception as e:
    print(f"✗ Errore: {e}")
EOF
```

## 📝 Come Funziona

1. **Lettura versioni**: Parsa il `docker-compose.yml` per estrarre le versioni attuali
2. **Controllo aggiornamenti**: Interroga Docker Hub API per cercare nuove versioni
3. **Scarica changelog**: Se disponibile, scarica il changelog dal repo GitHub ufficiale
4. **Analisi AI** (opzionale): Invia i changelog a LiteLLM per:
   - Identificare breaking changes
   - Valutare il livello di rischio (BASSO/MEDIO/ALTO)
   - Fornire raccomandazioni (procedere / testare prima / attendere)
5. **Notifica email** (opzionale): Invia un'email HTML con il report completo
6. **Salva stato**: Memorizza le versioni trovate per future referenze

## 🎯 Case di Utilizzo

### Monitoraggio Passivo
Solo controllo, nessuna email - perfetto per ambienti non critici:
```bash
# Lascia vuoti SMTP_USER, SMTP_PASSWORD, MAIL_TO nel .env
```

### Monitoraggio Attivo con Avvisi
Email solo se ci sono aggiornamenti:
```bash
# Configura SMTP e MAIL_TO
# Email inviata solo se trovati nuovi aggiornamenti
```

### Monitoraggio Intelligente
Con analisi AI dei rischi:
```bash
# Configura sia LITELLM che SMTP
# Ricevi email con analisi automatica del rischio per ogni aggiornamento
```

## 📜 Miglioramenti Implementati

- ✅ **FIX 1**: Parsing corretto di MAIL_TO (supporta più email con spazi)
- ✅ **FIX 2**: URL Docker Hub API corretto per immagini ufficiali (`library/postgres` instead of `postgres`)
- ✅ **FIX 3**: Parsing JSON robusto da risposta LiteLLM
- ✅ **Validazione configurazione**: Verifica all'avvio che il docker-compose esista
- ✅ **Gestione errori**: Errori non bloccanti, con avvisi riepilogativi
- ✅ **Logging**: Traccia tutti gli eventi nel file di log
- ✅ **Exit codes**: Ritorna 0 se successo, 1 se errori critici

## 🔐 Sicurezza

- Le credenziali SMTP sono nel file `.env`, NON nel codice Python
- File `.env` è leggibile solo dall'utente docker: `chmod 600 /home/docker/.env.supabase-monitor`
- API keys non vengono loggare in chiaro
- Usa sempre HTTPS per LiteLLM

```bash
# Proteggi il file di configurazione
chmod 600 /home/docker/.env.supabase-monitor
```

## 📧 Email Generata

L'email contiene una tabella con:
- Servizio
- Versione attuale
- Nuova versione disponibile

Seguita da:
- Analisi AI (se disponibile)
- Link ai changelog ufficiali
- Reminder per fare backup e testare prima di aggiornare

---

**Creato e mantenuto da**: Warp Agent  
**Ultima modifica**: 2026-02-17
