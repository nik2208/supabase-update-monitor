#!/usr/bin/env python3
"""
Supabase Self-Hosted Update Monitor - VERSIONE MIGLIORATA
Monitors Docker image versions, checks changelogs, uses LiteLLM for AI analysis,
and sends email notifications.
"""

import os
import json
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from pathlib import Path

# Configurazione
COMPOSE_FILE = os.getenv("COMPOSE_FILE", "/home/docker/dockerCompose/supabase/supatest/docker-compose.yml")
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "")
LITELLM_MODEL = os.getenv("LITELLM_MODEL", "gpt-3.5-turbo")

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER)
# FIX 1: Parsing corretto di MAIL_TO
MAIL_TO = [x.strip() for x in os.getenv("MAIL_TO", "").split(",") if x.strip()]

STATE_FILE = "/tmp/supabase_versions.json"
LOG_FILE = os.getenv("LOG_FILE", "/var/log/supabase-monitor.log")


class SupabaseMonitor:
    def __init__(self):
        self.current_versions = {}
        self.new_versions = {}
        self.changelogs = {}
        self.ai_analysis = None
        self.errors = []
        
        # Validazione configurazione
        self._validate_config()
        self._load_current_versions()

    def _validate_config(self):
        """Valida la configurazione prima di iniziare"""
        print(f"\n[{datetime.now()}] Validando configurazione...")
        
        if not os.path.exists(COMPOSE_FILE):
            raise FileNotFoundError(f"docker-compose.yml non trovato: {COMPOSE_FILE}")
        
        if not LITELLM_API_KEY:
            self.errors.append("⚠️  LITELLM_API_KEY non configurato - analisi AI non sarà disponibile")
        
        if not SMTP_USER or not SMTP_PASSWORD:
            self.errors.append("⚠️  Credenziali SMTP non configurate - email non sarà inviata")
        
        if not MAIL_TO:
            self.errors.append("⚠️  MAIL_TO non configurato - email non sarà inviata")
        
        print(f"  ✓ Configurazione validata")

    def _load_current_versions(self) -> Dict[str, str]:
        """Legge le versioni attuali da docker-compose.yml"""
        print(f"[{datetime.now()}] Caricando versioni attuali...")
        
        with open(COMPOSE_FILE) as f:
            content = f.read()

        # Pattern per immagini Docker (es: postgres:15.1, supabase/postgres:15.1)
        pattern = r'image:\s*([^:]+):([^\s\n]+)'
        matches = re.findall(pattern, content)

        if not matches:
            raise ValueError(f"Nessuna immagine Docker trovata in {COMPOSE_FILE}")

        for service, version in matches:
            service_name = service.split('/')[-1]
            self.current_versions[service_name] = version
            print(f"  • {service_name}: {version}")

        return self.current_versions

    def _get_docker_hub_url(self, service: str) -> str:
        """Costruisce l'URL corretto per Docker Hub API"""
        # Se il servizio non ha '/', è un'immagine ufficiale
        if '/' not in service:
            return f"https://hub.docker.com/v2/repositories/library/{service}/tags"
        else:
            return f"https://hub.docker.com/v2/repositories/{service}/tags"

    def check_new_versions(self) -> bool:
        """Controlla le nuove versioni disponibili su Docker Hub"""
        print(f"\n[{datetime.now()}] Controllando nuove versioni...")

        has_updates = False
        for service, current_version in self.current_versions.items():
            try:
                # FIX 2: URL corretto per Docker Hub API
                url = self._get_docker_hub_url(service)
                resp = requests.get(url, timeout=10)
                
                if resp.status_code == 200:
                    tags = resp.json().get('results', [])
                    if tags:
                        latest_tag = tags[0]['name']
                        if latest_tag != current_version:
                            self.new_versions[service] = {
                                'current': current_version,
                                'new': latest_tag
                            }
                            has_updates = True
                            print(f"  ✓ {service}: {current_version} → {latest_tag}")
                elif resp.status_code == 404:
                    print(f"  ⚠️  {service}: non trovato su Docker Hub")
                else:
                    print(f"  ✗ {service}: errore HTTP {resp.status_code}")
            except Exception as e:
                msg = f"Errore controllando {service}: {e}"
                print(f"  ✗ {msg}")
                self.errors.append(msg)

        return has_updates

    def fetch_changelog(self, service: str, version: str) -> str:
        """Scarica il changelog per un servizio"""
        try:
            # Prova a scaricare dal repository GitHub ufficiale
            url = f"https://raw.githubusercontent.com/supabase/supabase/v{version}/CHANGELOG.md"
            resp = requests.get(url, timeout=10)

            if resp.status_code == 200:
                return resp.text[:2000]

            # Alternativa: usa Docker Hub API per la descrizione
            url = f"https://hub.docker.com/v2/repositories/{service}/tags/{version}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                return json.dumps(resp.json(), indent=2)[:1000]

        except Exception as e:
            print(f"Errore scaricando changelog per {service}:{version}: {e}")

        return f"Changelog non disponibile per {service}:{version}"

    def analyze_with_ai(self) -> Optional[Dict]:
        """Usa LiteLLM per analizzare i changelog"""
        if not LITELLM_API_KEY:
            print(f"\n[{datetime.now()}] ⚠️  Skippando analisi AI (API key non configurata)")
            return None

        print(f"\n[{datetime.now()}] Analizzando con AI...")

        changelog_text = "\n\n".join([
            f"=== {service} ({info['current']} → {info['new']}) ===\n{self.changelogs.get(service, 'N/A')}"
            for service, info in self.new_versions.items()
        ])

        prompt = f"""Analizza questi changelog di Supabase per gli aggiornamenti disponibili.
Fornisci un'analisi concisa (max 500 caratteri) che includa:
1. Breaking changes identificati
2. Migrazioni database necessarie
3. Livello di rischio (BASSO/MEDIO/ALTO)
4. Raccomandazione (procedere subito / testare prima / attendere)

Changelogs:
{changelog_text}

Rispondi SOLO in formato JSON valido."""

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
                        "content": "Sei un esperto DevOps. Analizza changelog con attenzione ai breaking changes e rischi di produzione."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.7,
                "max_tokens": 500
            }

            resp = requests.post(
                f"{LITELLM_BASE_URL}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=30
            )

            if resp.status_code == 200:
                result = resp.json()
                content = result['choices'][0]['message']['content']
                
                # FIX 3: Parsing JSON più robusto
                try:
                    # Prova a estrarre JSON dalla risposta
                    json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content, re.DOTALL)
                    if json_match:
                        self.ai_analysis = json.loads(json_match.group())
                    else:
                        self.ai_analysis = {"analysis": content}
                except json.JSONDecodeError:
                    self.ai_analysis = {"analysis": content}
                
                print(f"  ✓ Analisi completata")
                return self.ai_analysis
            else:
                msg = f"Errore LiteLLM: {resp.status_code}"
                print(f"  ✗ {msg}")
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
            msg["Subject"] = f"🔔 Supabase Updates Available ({len(self.new_versions)} servizi)"
            msg["From"] = MAIL_FROM
            msg["To"] = ", ".join(MAIL_TO)

            # Costruisci il corpo HTML
            html_content = f"""
            <html>
              <body style="font-family: Arial, sans-serif;">
                <h2>Supabase Self-Hosted - Aggiornamenti Disponibili</h2>
                <p><strong>Data:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

                <h3>Servizi da aggiornare:</h3>
                <table border="1" cellpadding="10" cellspacing="0" style="border-collapse: collapse;">
                  <tr style="background-color: #f0f0f0;">
                    <th>Servizio</th>
                    <th>Versione Attuale</th>
                    <th>Nuova Versione</th>
                  </tr>
            """

            for service, versions in self.new_versions.items():
                html_content += f"""
                  <tr>
                    <td><strong>{service}</strong></td>
                    <td>{versions['current']}</td>
                    <td style="color: #0066cc;"><strong>{versions['new']}</strong></td>
                  </tr>
                """

            html_content += """
                </table>

                <h3>Analisi AI:</h3>
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
                  ✓ Verifica i changelog in https://github.com/supabase/supabase/releases<br>
                  ✓ Fai un backup prima di aggiornare<br>
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
        with open(STATE_FILE, 'w') as f:
            json.dump(self.new_versions, f)

    def print_summary(self):
        """Stampa un riassunto con eventuali errori"""
        if self.errors:
            print(f"\n⚠️  AVVISI:")
            for error in self.errors:
                print(f"  {error}")

    def run(self):
        """Esegui il ciclo completo"""
        print("=" * 60)
        print("Supabase Self-Hosted Update Monitor")
        print("=" * 60)

        print(f"\nVersioni attuali ({len(self.current_versions)} servizi):")
        for service, version in self.current_versions.items():
            print(f"  • {service}: {version}")

        if not self.check_new_versions():
            print("\n✓ Nessun aggiornamento disponibile")
            self.print_summary()
            return

        print(f"\n✓ Trovati {len(self.new_versions)} aggiornamenti")

        # Scarica i changelog
        print(f"\n[{datetime.now()}] Scaricando changelog...")
        for service in self.new_versions:
            new_version = self.new_versions[service]['new']
            self.changelogs[service] = self.fetch_changelog(service, new_version)

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
        monitor = SupabaseMonitor()
        monitor.run()
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ ERRORE CRITICO: {e}", file=sys.stderr)
        sys.exit(1)
