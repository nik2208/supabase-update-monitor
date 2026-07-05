#!/usr/bin/env python3
"""
Supabase Self-Hosted Update Monitor - v2
Confronta docker-compose.yml locali con quelli del repo GitHub ufficiale
Scarica il changelog e invia tutto ad AI per analisi
"""

import os
import json
import re
import sys
import difflib
from datetime import datetime
from typing import Dict, Optional, Tuple
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from pathlib import Path

# === CONFIGURAZIONE ===
# File locali
COMPOSE_FILE = os.getenv("COMPOSE_FILE", "/home/docker/dockerCompose/supabase/supatest/docker-compose.yml")
COMPOSE_S3_FILE = os.getenv("COMPOSE_S3_FILE", "/home/docker/dockerCompose/supabase/supatest/docker-compose.s3.yml")
ENV_FILE = os.getenv("ENV_FILE", "/home/docker/dockerCompose/supabase/supatest/.env")

# GitHub URLs
GITHUB_COMPOSE_URL = "https://raw.githubusercontent.com/supabase/supabase/refs/heads/master/docker/docker-compose.yml"
GITHUB_COMPOSE_S3_URL = "https://raw.githubusercontent.com/supabase/supabase/refs/heads/master/docker/docker-compose.s3.yml"
CHANGELOG_URL = "https://raw.githubusercontent.com/supabase/supabase/refs/heads/master/docker/CHANGELOG.md"
GITHUB_ENV_EXAMPLE_URL = "https://raw.githubusercontent.com/supabase/supabase/refs/heads/master/docker/.env.example"

# LiteLLM
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "")
LITELLM_MODEL = os.getenv("LITELLM_MODEL", "groq/llama-3.3-70b-versatile")

# SMTP
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER)
MAIL_TO = [x.strip() for x in os.getenv("MAIL_TO", "").split(",") if x.strip()]

# State e log
STATE_FILE = "/tmp/supabase_comparison.json"
LOG_FILE = os.getenv("LOG_FILE", "/var/log/supabase-monitor.log")
CHANGELOG_EXTRACT_SIZE = 100


class SupabaseMonitor:
    def __init__(self):
        self.local_compose = ""
        self.local_compose_s3 = ""
        self.github_compose = ""
        self.github_compose_s3 = ""
        self.local_env = ""
        self.github_env_example = ""
        self.compose_diff = ""
        self.compose_s3_diff = ""
        self.env_diff = ""
        self.changelog_excerpt = ""
        self.ai_analysis = None
        self.errors = []
        self.has_changes = False

        self._validate_config()

    def _validate_config(self):
        """Valida la configurazione prima di iniziare"""
        print(f"\n[{datetime.now()}] Validando configurazione...")

        if not os.path.exists(COMPOSE_FILE):
            raise FileNotFoundError(f"docker-compose.yml non trovato: {COMPOSE_FILE}")

        if COMPOSE_S3_FILE and not os.path.exists(COMPOSE_S3_FILE):
            print(f"  ⚠️  docker-compose.s3.yml non trovato: {COMPOSE_S3_FILE}")

        if not LITELLM_API_KEY:
            self.errors.append("⚠️  LITELLM_API_KEY non configurato")

        if not SMTP_USER or not SMTP_PASSWORD:
            self.errors.append("⚠️  Credenziali SMTP non configurate")

        if not MAIL_TO:
            self.errors.append("⚠️  MAIL_TO non configurato")

        print(f"  ✓ Configurazione validata")

    def _load_local_compose(self):
        """Carica i docker-compose.yml e .env locali"""
        print(f"\n[{datetime.now()}] Caricando file locali...")

        with open(COMPOSE_FILE) as f:
            self.local_compose = f.read()
        print(f"  ✓ {COMPOSE_FILE}")

        if COMPOSE_S3_FILE and os.path.exists(COMPOSE_S3_FILE):
            with open(COMPOSE_S3_FILE) as f:
                self.local_compose_s3 = f.read()
            print(f"  ✓ {COMPOSE_S3_FILE}")

        # Carica il file .env locale
        if os.path.exists(ENV_FILE):
            with open(ENV_FILE) as f:
                self.local_env = f.read()
            print(f"  ✓ {ENV_FILE}")
        else:
            print(f"  ⚠️  .env locale non trovato in {ENV_FILE}")

    def _fetch_github_compose(self):
        """Scarica i docker-compose.yml e .env.example dal repo GitHub"""
        print(f"\n[{datetime.now()}] Scaricando file da GitHub...")

        try:
            resp = requests.get(GITHUB_COMPOSE_URL, timeout=10)
            resp.raise_for_status()
            self.github_compose = resp.text
            print(f"  ✓ docker-compose.yml")
        except Exception as e:
            msg = f"Errore scaricando docker-compose.yml: {e}"
            print(f"  ✗ {msg}")
            self.errors.append(msg)
            return False

        try:
            resp = requests.get(GITHUB_COMPOSE_S3_URL, timeout=10)
            resp.raise_for_status()
            self.github_compose_s3 = resp.text
            print(f"  ✓ docker-compose.s3.yml")
        except Exception as e:
            print(f"  ⚠️  Errore scaricando docker-compose.s3.yml: {e}")

        try:
            resp = requests.get(GITHUB_ENV_EXAMPLE_URL, timeout=10)
            resp.raise_for_status()
            self.github_env_example = resp.text
            print(f"  ✓ .env.example")
        except Exception as e:
            print(f"  ⚠️  Errore scaricando .env.example: {e}")

        return True

    def _generate_diff(self, local: str, github: str, filename: str) -> str:
        """Genera un diff leggibile tra i due file"""
        local_lines = local.splitlines(keepends=True)
        github_lines = github.splitlines(keepends=True)

        # Usa unified diff
        diff = difflib.unified_diff(
            local_lines,
            github_lines,
            fromfile=f"locale ({filename})",
            tofile=f"GitHub master ({filename})",
            lineterm=''
        )

        diff_text = '\n'.join(diff)
        return diff_text if diff_text else "Nessuna differenza trovata"

    def _fetch_changelog(self) -> str:
        """Scarica il changelog da supabase.com"""
        print(f"\n[{datetime.now()}] Scaricando changelog...")

        try:
            resp = requests.get(CHANGELOG_URL, timeout=10)
            resp.raise_for_status()

            # Estrai solo i contenuti testuali rilevanti
            # Cerca la sezione con i changelog
            content = resp.text

            # Estrai i primi N caratteri
            excerpt = content[:CHANGELOG_EXTRACT_SIZE * 1024]

            print(f"  ✓ Estratti {len(excerpt) // 1024}KB dal changelog")
            return excerpt

        except Exception as e:
            msg = f"Errore scaricando changelog: {e}"
            print(f"  ✗ {msg}")
            self.errors.append(msg)
            return ""

    def compare(self) -> bool:
        """Esegui il confronto tra i file"""
        self._load_local_compose()

        if not self._fetch_github_compose():
            return False

        print(f"\n[{datetime.now()}] Generando diff...")

        self.compose_diff = self._generate_diff(
            self.local_compose,
            self.github_compose,
            "docker-compose.yml"
        )

        if self.local_compose_s3:
            self.compose_s3_diff = self._generate_diff(
                self.local_compose_s3,
                self.github_compose_s3,
                "docker-compose.s3.yml"
            )

        # Confronta i file .env
        if self.local_env and self.github_env_example:
            self.env_diff = self._generate_diff(
                self.local_env,
                self.github_env_example,
                ".env"
            )

        # Verifica se ci sono effettivamente differenze
        self.has_changes = (
            "Nessuna differenza" not in self.compose_diff or
            (self.compose_s3_diff and "Nessuna differenza" not in self.compose_s3_diff) or
            (self.env_diff and "Nessuna differenza" not in self.env_diff)
        )

        if self.has_changes:
            print(f"  ✓ Differenze trovate")
            print(f"\nAnteprima diff (prime 20 linee):")
            diff_lines = self.compose_diff.split('\n')[:20]
            for line in diff_lines:
                print(f"    {line}")
        else:
            print(f"  ✓ File allineati con GitHub")

        return True

    def analyze_with_ai(self) -> Optional[Dict]:
        """Usa LiteLLM per analizzare diff e changelog"""
        if not LITELLM_API_KEY:
            print(f"\n[{datetime.now()}] ⚠️  Skippando analisi AI (API key non configurata)")
            return None

        print(f"\n[{datetime.now()}] Analizzando con AI...")

        # Limita il testo dei diff per evitare token troppi
        compose_diff_limited = self.compose_diff[:2000] if self.compose_diff else ""
        changelog_limited = self.changelog_excerpt[:1000] if self.changelog_excerpt else ""

        prompt = f"""Analizza questo diff e rispondi ESCLUSIVAMENTE con JSON valido.

DIFF docker-compose.yml:
{compose_diff_limited}

DIFF docker-compose.s3.yml:
{self.compose_s3_diff[:500] if self.compose_s3_diff else "Identico"}

CHANGELOG:
{changelog_limited}

Rispondi con esattamente questo JSON:
{{
  "versioni_cambiate": ["servizio: old -> new"],
  "variabili_env_nuove": [],
  "variabili_env_modificate": [],
  "breaking_changes": [],
  "migrazioni_necessarie": [],
  "livello_rischio": "BASSO",
  "raccomandazione": "procedere",
  "verdetto_finale": "analisi completata"
}}"""

        try:
            headers = {
                "Authorization": f"Bearer {LITELLM_API_KEY}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": LITELLM_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": "Sei un esperto DevOps. Analizza i dati forniti e RESTITUISCI ESCLUSIVAMENTE UN JSON VALIDO nel tuo messaggio. Il JSON deve essere completo e valido. Non aggiungere testo prima o dopo il JSON."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.2,
                "max_tokens": 4000  # Aumentato significativamente
            }

            print(f"  [DEBUG] Modello: {LITELLM_MODEL}")
            print(f"  [DEBUG] max_tokens: 4000")

            resp = requests.post(
                f"{LITELLM_BASE_URL}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=300
            )

            print(f"  [DEBUG] Status code: {resp.status_code}")

            if resp.status_code == 200:
                result = resp.json()
                
                # Prova a estrarre il content dalla struttura della risposta
                message = result.get('choices', [{}])[0].get('message', {})
                content = message.get('content', '').strip()
                
                # Se content è vuoto, prova reasoning_content (per modelli con reasoning)
                if not content:
                    print(f"  [DEBUG] Content vuoto, tentando reasoning_content...")
                    content = message.get('reasoning_content', '').strip()
                
                print(f"  [DEBUG] Lunghezza content: {len(content)}")
                if content:
                    print(f"  [DEBUG] Primi 300 caratteri: {content[:300]}")

                # Parsing JSON - estrai il primo { e ultimo }
                try:
                    if not content:
                        raise ValueError("Content is empty")
                    
                    # Trova la prima { e l'ultima }
                    start = content.find('{')
                    end = content.rfind('}') + 1
                    
                    if start != -1 and end > start:
                        json_str = content[start:end]
                        print(f"  [DEBUG] JSON estratto ({len(json_str)} char)")
                        self.ai_analysis = json.loads(json_str)
                        print(f"  ✓ JSON parsato correttamente")
                    else:
                        print(f"  [DEBUG] Nessun JSON trovato in: {content[:200]}")
                        raise ValueError("No JSON found in response")
                        
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"  ⚠️  Parsing JSON fallito: {e}")
                    print(f"  [DEBUG] Tentativo di estrazione avanzata...")
                    
                    # Prova regex per estrarre JSON da testo
                    json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
                    matches = re.findall(json_pattern, content, re.DOTALL)
                    
                    parsed = False
                    for match in matches:
                        try:
                            self.ai_analysis = json.loads(match)
                            print(f"  ✓ JSON parsato da regex")
                            parsed = True
                            break
                        except json.JSONDecodeError:
                            continue
                    
                    if not parsed:
                        print(f"  [DEBUG] Estrazione regex fallita, uso fallback")
                        # Fallback: crea un'analisi vuota
                        self.ai_analysis = {
                            "versioni_cambiate": [],
                            "variabili_env_nuove": [],
                            "variabili_env_modificate": [],
                            "breaking_changes": [],
                            "migrazioni_necessarie": [],
                            "livello_rischio": "SCONOSCIUTO",
                            "raccomandazione": "Impossibile analizzare con AI",
                            "verdetto_finale": f"Errore: {str(e)}"
                        }

                print(f"  ✓ Analisi completata")
                return self.ai_analysis
            else:
                msg = f"Errore LiteLLM: {resp.status_code}"
                print(f"  ✗ {msg}")
                print(f"  [DEBUG] Response: {resp.text[:300]}")
                self.errors.append(msg)
                return None

        except Exception as e:
            msg = f"Errore durante analisi AI: {e}"
            print(f"  ✗ {msg}")
            self.errors.append(msg)
            return None

    def send_email(self):
        """Invia email con i risultati"""
        if not MAIL_TO or not SMTP_USER:
            print(f"⚠️  Email non configurata, skippando invio")
            return

        print(f"\n[{datetime.now()}] Inviando email...")

        try:
            msg = MIMEMultipart("alternative")
            status = "🔴 AGGIORNAMENTI" if self.has_changes else "🟢 ALLINEATO"
            msg["Subject"] = f"{status} - Supabase Self-Hosted Monitor"
            msg["From"] = MAIL_FROM
            msg["To"] = ", ".join(MAIL_TO)

            html_content = f"""
            <html>
              <body style="font-family: Arial, sans-serif;">
                <h2>{status} - Supabase Self-Hosted</h2>
                <p><strong>Data:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

                <h3>📊 Stato Confronto</h3>
                <p>
                    {'✅ Repository GitHub è allineato con la configurazione locale' if not self.has_changes else '⚠️ Sono presenti differenze tra la configurazione locale e GitHub master'}
                </p>

                <h3>📝 Diff docker-compose.yml</h3>
                <pre style="background-color: #f5f5f5; padding: 10px; border-radius: 5px; overflow-x: auto; font-size: 12px;">
{self.compose_diff}
                </pre>
            """

            if self.compose_s3_diff:
                html_content += f"""
                <h3>📝 Diff docker-compose.s3.yml</h3>
                <pre style="background-color: #f5f5f5; padding: 10px; border-radius: 5px; overflow-x: auto; font-size: 12px;">
{self.compose_s3_diff}
                </pre>
                """

            html_content += """
                <h3>🔐 Diff .env (Locale vs .env.example)</h3>
            """

            if self.env_diff:
                html_content += f"""
                <pre style="background-color: #f5f5f5; padding: 10px; border-radius: 5px; overflow-x: auto; font-size: 12px;">
{self.env_diff}
                </pre>
                """
            else:
                html_content += "<p>✅ File .env allineato con .env.example</p>"

            html_content += """
                <h3>🤖 Analisi AI:</h3>
            """

            if self.ai_analysis:
                html_content += f"""
                <pre style="background-color: #f0f0f0; padding: 10px; border-radius: 5px; overflow-x: auto;">
{json.dumps(self.ai_analysis, indent=2, ensure_ascii=False)}
                </pre>
                """
            else:
                html_content += "<p>⚠️ Analisi AI non disponibile</p>"

            html_content += """
                <hr>
                <p style="color: #666; font-size: 12px;">
                  📚 Repo: <a href="https://github.com/supabase/supabase">supabase/supabase</a><br>
                  📋 Changelog: <a href="https://supabase.com/changelog">supabase.com/changelog</a><br>
                  ✓ Fai sempre un backup prima di aggiornare<br>
                  ✓ Testa in staging environment
                </p>
              </body>
            </html>
            """

            msg.attach(MIMEText(html_content, "html"))

            # Invia email
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(MAIL_FROM, MAIL_TO, msg.as_string())

            print(f"  ✓ Email inviata a {', '.join(MAIL_TO)}")

        except Exception as e:
            msg = f"Errore invio email: {e}"
            print(f"  ✗ {msg}")
            self.errors.append(msg)

    def save_state(self):
        """Salva lo stato per il prossimo controllo"""
        state = {
            "timestamp": datetime.now().isoformat(),
            "has_changes": self.has_changes,
            "compose_diff_lines": len(self.compose_diff.split('\n')),
            "errors": self.errors
        }
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)

    def print_summary(self):
        """Stampa un riassunto con eventuali errori"""
        if self.errors:
            print(f"\n⚠️  AVVISI:")
            for error in self.errors:
                print(f"  {error}")

    def run(self):
        """Esegui il ciclo completo"""
        print("=" * 70)
        print("Supabase Self-Hosted Update Monitor v2")
        print("=" * 70)

        # Scarica il changelog
        self.changelog_excerpt = self._fetch_changelog()

        # Confronta i file
        if not self.compare():
            print("\n✗ Errore durante il confronto dei file")
            self.print_summary()
            return

        # Se non ci sono cambipamenti, comunica e esci
        if not self.has_changes:
            print("\n✓ Configurazione allineata con GitHub master")
            self.print_summary()
            if MAIL_TO and SMTP_USER:
                self.send_email()
            self.save_state()
            return

        # Analizza con AI
        self.analyze_with_ai()

        # Invia email
        if MAIL_TO and SMTP_USER:
            self.send_email()

        # Salva lo stato
        self.save_state()

        self.print_summary()
        print("\n✓ Monitoraggio completato")


if __name__ == "__main__":
    try:
        print("=" * 70)
        print("Supabase Self-Hosted Update Monitor v2")
        print("=" * 70)
        print(f"\nVariabili di configurazione:")
        print(f"  COMPOSE_FILE: {COMPOSE_FILE}")
        print(f"  ENV_FILE: {ENV_FILE}")
        print(f"  LITELLM_BASE_URL: {LITELLM_BASE_URL}")
        print(f"  LITELLM_MODEL: {LITELLM_MODEL}")
        print(f"  MAIL_TO: {MAIL_TO}")
        
        monitor = SupabaseMonitor()
        monitor.run()
        sys.exit(0)
    except FileNotFoundError as e:
        print(f"\n✗ ERRORE FILE NOT FOUND: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        import traceback
        print(f"\n✗ ERRORE CRITICO: {e}", file=sys.stderr)
        print("\nTraceback completo:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
