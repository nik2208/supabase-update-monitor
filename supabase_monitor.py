#!/usr/bin/env python3
"""
Supabase Self-Hosted Update Monitor - v4
Confronta docker-compose.yml locali con quelli del repo GitHub ufficiale
Genera una RUNBOOK completa step-by-step per l'aggiornamento manuale
"""

import os
import json
import re
import sys
import difflib
import subprocess
import yaml
from datetime import datetime
from typing import Dict, Optional, List, Tuple
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from pathlib import Path

# === CONFIGURAZIONE ===
# File locali
COMPOSE_DIR = os.getenv("COMPOSE_DIR", "/home/docker/dockerCompose/supabase/supatest")
COMPOSE_FILE = os.path.join(COMPOSE_DIR, "docker-compose.yml")
COMPOSE_S3_FILE = os.path.join(COMPOSE_DIR, "docker-compose.s3.yml")
ENV_FILE = os.path.join(COMPOSE_DIR, ".env")

# GitHub URLs
GITHUB_COMPOSE_URL = "https://raw.githubusercontent.com/supabase/supabase/refs/heads/master/docker/docker-compose.yml"
GITHUB_COMPOSE_S3_URL = "https://raw.githubusercontent.com/supabase/supabase/refs/heads/master/docker/docker-compose.s3.yml"
CHANGELOG_URL = "https://raw.githubusercontent.com/supabase/supabase/refs/heads/master/docker/CHANGELOG.md"
GITHUB_ENV_EXAMPLE_URL = "https://raw.githubusercontent.com/supabase/supabase/refs/heads/master/docker/.env.example"
GITHUB_API_COMMITS = "https://api.github.com/repos/supabase/supabase/commits"
# Opzionale ma consigliato: senza token, GitHub limita le richieste API a
# 60/ora per IP (facile da sforare dato che facciamo più chiamate API per
# esecuzione: commits + compare). Con un Personal Access Token (anche senza
# permessi particolari, basta "public_repo" read) il limite sale a 5000/ora.
# Crealo su https://github.com/settings/tokens
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

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
STATE_DIR = os.getenv("STATE_DIR", os.path.expanduser("~/.supabase_monitor"))
STATE_FILE = os.path.join(STATE_DIR, "version_info.json")
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

        # Confronto strutturato (non soggetto a troncamenti)
        self.service_version_changes: List[str] = []
        self.env_vars_nuove: List[str] = []
        self.env_vars_rimosse: List[str] = []

        # Version tracking
        self.current_version_info = {}
        self.github_commit_sha = ""
        self.github_commit_sha_full = ""
        self.github_latest_tag = ""
        self.relevant_changelog = ""  # commit reali tra versione deployata e attuale

        self._ensure_state_dir()
        self._validate_config()

    def _ensure_state_dir(self):
        """Crea la directory di stato se non esiste"""
        Path(STATE_DIR).mkdir(parents=True, exist_ok=True)
        print(f"  ✓ State directory: {STATE_DIR}")

    def _validate_config(self):
        """Valida la configurazione prima di iniziare"""
        print(f"\n[{datetime.now()}] Validando configurazione...")

        if not os.path.exists(COMPOSE_DIR):
            raise FileNotFoundError(f"Directory non trovata: {COMPOSE_DIR}")

        if not os.path.exists(COMPOSE_FILE):
            raise FileNotFoundError(f"docker-compose.yml non trovato: {COMPOSE_FILE}")

        if not os.path.exists(ENV_FILE):
            print(f"  ⚠️  .env non trovato: {ENV_FILE}")

        if not GITHUB_TOKEN:
            self.errors.append("ℹ️  GITHUB_TOKEN non configurato: le chiamate API GitHub sono limitate a 60/ora (rischio rate-limit rilevato durante i test)")

        if not LITELLM_API_KEY:
            self.errors.append("⚠️  LITELLM_API_KEY non configurato")

        if not SMTP_USER or not SMTP_PASSWORD:
            self.errors.append("⚠️  Credenziali SMTP non configurate")

        if not MAIL_TO:
            self.errors.append("⚠️  MAIL_TO non configurato")

        print(f"  ✓ Configurazione validata")

    def _get_file_hash(self, filepath: str) -> str:
        """Calcola SHA256 di un file"""
        import hashlib
        try:
            with open(filepath, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()[:16]
        except:
            return "unknown"

    def _get_git_commit_sha(self) -> str:
        """Estrae il commit SHA dal repo locale (se esiste)"""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=COMPOSE_DIR,
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()[:16]
        except:
            pass
        return "unknown"

    def _get_git_commit_sha_full(self) -> str:
        """Estrae lo SHA completo (40 char) del repo locale, necessario per
        chiamare la GitHub Compare API (lo SHA troncato a 16 char non basta)."""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=COMPOSE_DIR,
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except:
            pass
        return ""

    def _load_current_version(self):
        """Carica lo stato della versione precedente"""
        print(f"\n[{datetime.now()}] Caricando versione attuale...")

        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    self.current_version_info = json.load(f)
                    print(f"  ✓ Versione precedente trovata: {self.current_version_info.get('last_update', 'sconosciuta')}")
            except:
                print(f"  ⚠️  State file corrotto, creando nuovo")
                self.current_version_info = {}
        else:
            print(f"  ℹ️  Prima volta? State file non trovato")
            self.current_version_info = {}

    def _load_local_compose(self):
        """Carica i docker-compose.yml e .env locali"""
        print(f"\n[{datetime.now()}] Caricando file locali...")

        with open(COMPOSE_FILE) as f:
            self.local_compose = f.read()
        print(f"  ✓ {COMPOSE_FILE}")

        if os.path.exists(COMPOSE_S3_FILE):
            with open(COMPOSE_S3_FILE) as f:
                self.local_compose_s3 = f.read()
            print(f"  ✓ {COMPOSE_S3_FILE}")

        if os.path.exists(ENV_FILE):
            with open(ENV_FILE) as f:
                self.local_env = f.read()
            print(f"  ✓ {ENV_FILE}")
        else:
            print(f"  ⚠️  .env non trovato in {ENV_FILE}")

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

        # Estrai commit SHA dal repo GitHub
        gh_headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
        try:
            resp = requests.get(f"{GITHUB_API_COMMITS}?per_page=1&sha=master", headers=gh_headers, timeout=10)
            resp.raise_for_status()
            commits = resp.json()
            if commits:
                self.github_commit_sha_full = commits[0]['sha']
                self.github_commit_sha = self.github_commit_sha_full[:16]
                print(f"  ✓ GitHub commit: {self.github_commit_sha}")
        except Exception as e:
            print(f"  ⚠️  Errore ricevendo commit SHA: {e}")
            self.github_commit_sha = "unknown"

        return True

    def _generate_diff(self, local: str, github: str, filename: str) -> str:
        """Genera un diff leggibile tra i due file"""
        local_lines = local.splitlines(keepends=True)
        github_lines = github.splitlines(keepends=True)

        diff = difflib.unified_diff(
            local_lines,
            github_lines,
            fromfile=f"locale ({filename})",
            tofile=f"GitHub master ({filename})",
            lineterm=''
        )

        diff_text = '\n'.join(diff)
        return diff_text if diff_text else "Nessuna differenza trovata"

    def _extract_service_versions(self, compose_text: str) -> Dict[str, str]:
        """Estrae {nome_servizio: immagine:tag} da un docker-compose.yml.

        Lavora sulla struttura YAML già interpretata, quindi non ha limiti
        di lunghezza: processa TUTTI i servizi, non solo i primi che
        compaiono nel file.
        """
        if not compose_text:
            return {}
        try:
            data = yaml.safe_load(compose_text)
            services = data.get("services", {}) if data else {}
            return {
                name: svc.get("image", "")
                for name, svc in services.items()
                if isinstance(svc, dict) and svc.get("image")
            }
        except Exception as e:
            print(f"  ⚠️  Errore parsing YAML: {e}")
            return {}

    def _diff_service_versions(self, local_text: str, github_text: str) -> List[str]:
        """Confronta la versione immagine di OGNI servizio, senza troncamenti"""
        local_versions = self._extract_service_versions(local_text)
        github_versions = self._extract_service_versions(github_text)

        changes = []
        all_services = sorted(set(local_versions) | set(github_versions))

        for name in all_services:
            local_img = local_versions.get(name, "‹assente localmente›")
            github_img = github_versions.get(name, "‹rimosso da GitHub›")
            if local_img != github_img:
                changes.append(f"{name}: {local_img} -> {github_img}")

        return changes

    def _diff_env_vars(self, local_env: str, github_env_example: str) -> Tuple[List[str], List[str]]:
        """Confronta le CHIAVI (mai i valori, per non esporre segreti)
        tra il .env locale e il .env.example di GitHub.
        """
        def parse_keys(text: str) -> set:
            keys = set()
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    keys.add(line.split("=", 1)[0].strip())
            return keys

        local_keys = parse_keys(local_env)
        github_keys = parse_keys(github_env_example)

        nuove = sorted(github_keys - local_keys)
        rimosse = sorted(local_keys - github_keys)

        return nuove, rimosse

    def _fetch_commits_between(self, base_sha: str, head_sha: str) -> str:
        """Usa la GitHub Compare API per ottenere l'elenco REALE dei commit
        tra la versione attualmente deployata (base) e quella disponibile
        su master (head). A differenza dell'estratto statico di
        CHANGELOG.md, questo è sempre esattamente delimitato al periodo
        rilevante, indipendentemente da quanto tempo sia passato dall'ultimo
        aggiornamento.

        NOTA: richiede SHA completi (40 caratteri), non troncati.
        Ritorna stringa vuota se non applicabile (es. prima esecuzione,
        SHA mancante, o errore di rete).
        """
        if not base_sha or not head_sha or base_sha == head_sha:
            return ""

        url = f"https://api.github.com/repos/supabase/supabase/compare/{base_sha}...{head_sha}"
        gh_headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
        try:
            resp = requests.get(url, headers=gh_headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  ⚠️  Errore chiamando GitHub Compare API: {e}")
            return ""

        total_commits = data.get("total_commits", 0)
        commits = data.get("commits", [])
        files = data.get("files", [])

        # Filtra solo i file sotto docker/ per capire se il range di commit
        # tocca effettivamente la parte self-hosted che ci interessa
        docker_files = [f["filename"] for f in files if f.get("filename", "").startswith("docker/")]

        lines = [f"Commit reali tra {base_sha[:10]} (deployato) e {head_sha[:10]} (disponibile): {total_commits} totali"]

        if docker_files:
            lines.append(f"File sotto docker/ toccati in questo range: {', '.join(docker_files[:30])}")
        else:
            lines.append("Nessun file sotto docker/ toccato in questo range (probabile falso positivo nel confronto compose)")

        # Messaggi di commit (solo prima riga, per restare compatti)
        for c in commits[:50]:
            msg = c.get("commit", {}).get("message", "").split("\n")[0].strip()
            sha_short = c.get("sha", "")[:10]
            if msg:
                lines.append(f"- [{sha_short}] {msg}")

        if total_commits > 50:
            lines.append(f"... e altri {total_commits - 50} commit non mostrati (troppi per il prompt)")

        result = "\n".join(lines)
        print(f"  ✓ Compare API: {total_commits} commit, {len(docker_files)} file docker/ toccati")
        return result

    def _fetch_changelog(self) -> str:
        """Scarica il changelog"""
        print(f"\n[{datetime.now()}] Scaricando changelog...")

        try:
            resp = requests.get(CHANGELOG_URL, timeout=10)
            resp.raise_for_status()
            content = resp.text
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
        self._load_current_version()
        self._load_local_compose()

        if not self._fetch_github_compose():
            return False

        print(f"\n[{datetime.now()}] Generando diff testuale...")

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

        if self.local_env and self.github_env_example:
            self.env_diff = self._generate_diff(
                self.local_env,
                self.github_env_example,
                ".env"
            )

        print(f"\n[{datetime.now()}] Generando confronto strutturato (versioni servizi + variabili env)...")

        # Confronto strutturato: elabora TUTTI i servizi/variabili, senza
        # alcun limite di dimensione, a differenza del diff testuale grezzo.
        self.service_version_changes = self._diff_service_versions(
            self.local_compose, self.github_compose
        )
        if self.local_compose_s3 and self.github_compose_s3:
            self.service_version_changes += self._diff_service_versions(
                self.local_compose_s3, self.github_compose_s3
            )

        if self.local_env and self.github_env_example:
            self.env_vars_nuove, self.env_vars_rimosse = self._diff_env_vars(
                self.local_env, self.github_env_example
            )
        else:
            self.env_vars_nuove, self.env_vars_rimosse = [], []

        if self.service_version_changes:
            print(f"  ✓ {len(self.service_version_changes)} servizi con versione cambiata:")
            for change in self.service_version_changes:
                print(f"    - {change}")
        if self.env_vars_nuove:
            print(f"  ✓ {len(self.env_vars_nuove)} nuove variabili env richieste")
        if self.env_vars_rimosse:
            print(f"  ✓ {len(self.env_vars_rimosse)} variabili env rimosse/deprecate")

        self.has_changes = (
            "Nessuna differenza" not in self.compose_diff or
            (self.compose_s3_diff and "Nessuna differenza" not in self.compose_s3_diff) or
            (self.env_diff and "Nessuna differenza" not in self.env_diff) or
            bool(self.service_version_changes) or
            bool(self.env_vars_nuove) or
            bool(self.env_vars_rimosse)
        )

        if self.has_changes:
            print(f"  ✓ Differenze trovate")
        else:
            print(f"  ✓ File allineati con GitHub")

        # Recupera il changelog REALE (commit veri) tra la versione deployata
        # nell'esecuzione precedente e quella disponibile ora. Preferito
        # rispetto all'estratto statico di CHANGELOG.md perché è sempre
        # esattamente delimitato al periodo che interessa.
        if self.has_changes:
            print(f"\n[{datetime.now()}] Recuperando commit reali da GitHub Compare API...")
            base_sha_full = self.current_version_info.get("github_commit_full", "")
            head_sha_full = self.github_commit_sha_full
            self.relevant_changelog = self._fetch_commits_between(base_sha_full, head_sha_full)
            if not self.relevant_changelog:
                print(f"  ℹ️  Compare API non disponibile (probabile prima esecuzione), uso estratto statico del CHANGELOG.md")

        return True

    def analyze_with_ai(self) -> Optional[Dict]:
        """Usa LiteLLM per analizzare il confronto strutturato e il changelog"""
        if not LITELLM_API_KEY:
            print(f"\n[{datetime.now()}] ⚠️  Skippando analisi AI (API key non configurata)")
            return None

        print(f"\n[{datetime.now()}] Analizzando con AI...")

        # Preferiamo il changelog REALE (commit veri ottenuti dalla Compare
        # API, delimitato esattamente al range tra versione deployata e
        # disponibile). Ripieghiamo sull'estratto statico di CHANGELOG.md
        # solo se la Compare API non è disponibile (es. prima esecuzione,
        # in cui non abbiamo ancora uno SHA precedente salvato).
        if self.relevant_changelog:
            changelog_limited = self.relevant_changelog[:4000]
            changelog_source_note = "(fonte: commit reali via GitHub Compare API)"
        else:
            changelog_limited = self.changelog_excerpt[:1000] if self.changelog_excerpt else ""
            changelog_source_note = "(fonte: estratto statico CHANGELOG.md, non delimitato alla versione deployata)"

        # Riassunto strutturato: copre TUTTI i servizi e TUTTE le variabili,
        # a differenza del vecchio approccio che troncava il diff testuale
        # grezzo (compose_diff[:2000], compose_s3_diff[:500]) perdendo
        # tutto ciò che seguiva i primi servizi elencati nel file.
        versioni_summary = "\n".join(self.service_version_changes) if self.service_version_changes else "Nessuna modifica versione immagine rilevata"
        env_summary = (
            f"Nuove variabili richieste: {', '.join(self.env_vars_nuove) if self.env_vars_nuove else 'nessuna'}\n"
            f"Variabili rimosse/deprecate: {', '.join(self.env_vars_rimosse) if self.env_vars_rimosse else 'nessuna'}"
        )

        prompt = f"""Analizza questo aggiornamento Supabase e rispondi ESCLUSIVAMENTE con JSON valido.

VERSIONI IMMAGINE CAMBIATE (per ogni servizio, include sia docker-compose.yml che docker-compose.s3.yml):
{versioni_summary}

VARIABILI D'AMBIENTE:
{env_summary}

CHANGELOG {changelog_source_note}:
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
  "stima_downtime_minuti": 5,
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
                        "content": "Sei un esperto DevOps. Analizza i dati forniti e RESTITUISCI ESCLUSIVAMENTE UN JSON VALIDO. Includi stima del downtime previsto."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.2,
                "max_tokens": 4000
            }

            resp = requests.post(
                f"{LITELLM_BASE_URL}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=300
            )

            if resp.status_code == 200:
                result = resp.json()
                message = result.get('choices', [{}])[0].get('message', {})
                content = message.get('content', '').strip()

                if not content:
                    content = message.get('reasoning_content', '').strip()

                try:
                    if not content:
                        raise ValueError("Content is empty")

                    start = content.find('{')
                    end = content.rfind('}') + 1

                    if start != -1 and end > start:
                        json_str = content[start:end]
                        self.ai_analysis = json.loads(json_str)
                        print(f"  ✓ JSON parsato correttamente")
                    else:
                        raise ValueError("No JSON found in response")

                except (json.JSONDecodeError, ValueError) as e:
                    print(f"  ⚠️  Parsing JSON fallito: {e}")

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
                        self.ai_analysis = self._fallback_analysis(str(e))

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

    def _fallback_analysis(self, error_msg: str) -> Dict:
        """Analisi di riserva basata sui dati strutturati, usata se l'AI fallisce.

        Anche in questo caso di errore, la runbook mostrerà comunque
        TUTTI i servizi e le variabili cambiate, perché questi dati
        arrivano dal confronto strutturato e non dall'AI.
        """
        return {
            "versioni_cambiate": list(self.service_version_changes),
            "variabili_env_nuove": list(self.env_vars_nuove),
            "variabili_env_modificate": [],
            "breaking_changes": [],
            "migrazioni_necessarie": [],
            "livello_rischio": "SCONOSCIUTO",
            "raccomandazione": "Analisi AI non disponibile: rivedere manualmente i cambiamenti elencati sopra",
            "stima_downtime_minuti": 0,
            "verdetto_finale": f"Errore AI: {error_msg}"
        }

    def _build_runbook(self) -> str:
        """Genera la RUNBOOK completa per l'aggiornamento manuale"""
        if not self.ai_analysis:
            # Se l'AI non è stata invocata affatto (es. API key mancante),
            # usa comunque il confronto strutturato per non perdere info.
            self.ai_analysis = self._fallback_analysis("Analisi AI non eseguita")

        analysis = self.ai_analysis
        risk_level = analysis.get("livello_rischio", "SCONOSCIUTO").upper()
        downtime_est = analysis.get("stima_downtime_minuti", 5)

        risk_color_map = {
            "ALTO": "#d32f2f",
            "MEDIO": "#f57c00",
            "BASSO": "#388e3c"
        }
        risk_emoji_map = {
            "ALTO": "🔴",
            "MEDIO": "🟠",
            "BASSO": "🟢"
        }

        risk_color = risk_color_map.get(risk_level, "#9c27b0")
        risk_emoji = risk_emoji_map.get(risk_level, "❓")

        current_sha = self.current_version_info.get('docker_compose_sha', 'unknown')
        git_sha = self._get_git_commit_sha()

        html = f"""
<html style="font-family: monospace;">
<body style="font-family: 'Courier New', monospace; line-height: 1.6; color: #333;">

<div style="background-color: #f5f5f5; padding: 20px; border-radius: 8px; margin-bottom: 30px;">
    <h1 style="margin: 0 0 20px 0; color: #000;">SUPABASE SELF-HOSTED UPDATE RUNBOOK</h1>
    <p style="margin: 5px 0;"><strong>Data Report:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    <p style="margin: 5px 0;"><strong>Versione Attuale:</strong> sha={current_sha}</p>
    <p style="margin: 5px 0;"><strong>Versione Disponibile:</strong> sha={self.github_commit_sha}</p>
    <p style="margin: 5px 0;"><strong>Git Commit:</strong> {git_sha}</p>
</div>

<!-- HEADER ALERT -->
<div style="background-color: {risk_color}; color: white; padding: 20px; border-radius: 8px; margin-bottom: 30px;">
    <h2 style="margin: 0 0 10px 0;">{risk_emoji} LIVELLO RISCHIO: {risk_level}</h2>
    <p style="margin: 0 0 5px 0;"><strong>Raccomandazione:</strong> {analysis.get('raccomandazione', 'N/A').upper()}</p>
    <p style="margin: 0;"><strong>Downtime Previsto:</strong> ~{downtime_est} minuti</p>
</div>

<!-- VERSIONI CAMBIATE (dato strutturato: copre TUTTI i servizi) -->
"""

        # Usiamo sempre il confronto strutturato come fonte primaria per le
        # versioni, così la runbook mostra sempre TUTTI i servizi cambiati
        # anche se l'AI ne ha riassunti/omessi alcuni nella sua risposta.
        versioni = self.service_version_changes or analysis.get("versioni_cambiate", [])
        if versioni:
            html += """
<div style="margin-bottom: 30px; background-color: #f9f9f9; padding: 15px; border-left: 4px solid #1976d2; border-radius: 4px;">
    <h3 style="margin: 0 0 10px 0; color: #1976d2;">📦 VERSIONI CAMBIATE (""" + str(len(versioni)) + """ servizi)</h3>
    <pre style="margin: 0; overflow-x: auto; font-size: 12px;">"""
            for v in versioni:
                html += f"{v}\n"
            html += "</pre></div>\n"

        # CHANGELOG REALE (commit via Compare API, fonte primaria e verificabile)
        if self.relevant_changelog:
            html += """
<div style="margin-bottom: 30px; background-color: #f9f9f9; padding: 15px; border-left: 4px solid #455a64; border-radius: 4px;">
    <h3 style="margin: 0 0 10px 0; color: #455a64;">📜 CHANGELOG REALE (commit tra versione deployata e disponibile)</h3>
    <pre style="margin: 0; overflow-x: auto; font-size: 11px; white-space: pre-wrap;">""" + self.relevant_changelog + """</pre>
</div>
"""

        # VARIABILI (dato strutturato come fonte primaria, AI come arricchimento)
        var_modificate = analysis.get("variabili_env_modificate", [])
        var_nuove = self.env_vars_nuove or analysis.get("variabili_env_nuove", [])
        var_rimosse = self.env_vars_rimosse

        if var_modificate or var_nuove or var_rimosse:
            html += """
<div style="margin-bottom: 30px; background-color: #f9f9f9; padding: 15px; border-left: 4px solid #f57c00; border-radius: 4px;">
    <h3 style="margin: 0 0 10px 0; color: #f57c00;">⚙️ VARIABILI D'AMBIENTE</h3>
"""
            if var_nuove:
                html += "<p style='margin: 0 0 5px 0;'><strong>Nuove variabili richieste (" + str(len(var_nuove)) + "):</strong></p><pre style='margin: 0 0 10px 0; font-size: 12px; background-color: #fff3e0; padding: 10px; border-radius: 4px;'>"
                for v in var_nuove:
                    html += f"# Aggiungi al .env:\n{v}=VALORE_QUI\n"
                html += "</pre>"

            if var_rimosse:
                html += "<p style='margin: 10px 0 5px 0;'><strong>Variabili rimosse/deprecate (" + str(len(var_rimosse)) + "):</strong></p><pre style='margin: 0 0 10px 0; font-size: 12px; background-color: #eeeeee; padding: 10px; border-radius: 4px;'>"
                for v in var_rimosse:
                    html += f"{v}\n"
                html += "</pre>"

            if var_modificate:
                html += "<p style='margin: 10px 0 5px 0;'><strong>Variabili modificate (dettaglio AI):</strong></p><pre style='margin: 0; font-size: 12px; background-color: #fff3e0; padding: 10px; border-radius: 4px;'>"
                for v in var_modificate:
                    html += f"{v}\n"
                html += "</pre>"

            html += "</div>\n"

        # BREAKING CHANGES
        breaking = analysis.get("breaking_changes", [])
        if breaking:
            html += """
<div style="margin-bottom: 30px; background-color: #ffebee; padding: 15px; border-left: 4px solid #d32f2f; border-radius: 4px;">
    <h3 style="margin: 0 0 10px 0; color: #d32f2f;">🚨 BREAKING CHANGES - ATTENZIONE!</h3>
    <ul style="margin: 0; padding-left: 20px;">
"""
            for change in breaking:
                html += f"<li style='margin-bottom: 5px;'>{change}</li>\n"
            html += """
    </ul>
</div>
"""

        # MIGRAZIONI NECESSARIE
        migrazioni = analysis.get("migrazioni_necessarie", [])
        if migrazioni:
            html += """
<div style="margin-bottom: 30px; background-color: #f3e5f5; padding: 15px; border-left: 4px solid #6a1b9a; border-radius: 4px;">
    <h3 style="margin: 0 0 10px 0; color: #6a1b9a;">🔧 AZIONI DURANTE L'AGGIORNAMENTO</h3>
    <ol style="margin: 0; padding-left: 20px;">
"""
            for mig in migrazioni:
                html += f"<li style='margin-bottom: 8px;'>{mig}</li>\n"
            html += """
    </ol>
</div>
"""

        # RUNBOOK COMPLETA
        html += """
<div style="margin-bottom: 30px; background-color: #e8f5e9; padding: 15px; border-left: 4px solid #388e3c; border-radius: 4px;">
    <h3 style="margin: 0 0 15px 0; color: #388e3c;">📋 ISTRUZIONI PASSO-PASSO</h3>

    <h4 style="margin: 15px 0 10px 0; color: #1976d2;">FASE 0: PRE-UPDATE CHECKLIST</h4>
    <pre style="background-color: white; padding: 15px; border-radius: 4px; overflow-x: auto; font-size: 12px; border: 1px solid #ddd;">
□ Verificare spazio disco (almeno 10GB liberi)
  $ df -h """ + COMPOSE_DIR + """

□ Backup database PostgreSQL
  $ cd """ + COMPOSE_DIR + """
  $ docker compose exec postgres pg_dump -U postgres -v > backup-$(date +%Y-%m-%d).sql
  $ du -h backup-*.sql  # Verifica peso

□ Backup file configurazione
  $ cp docker-compose.yml docker-compose.yml.backup-$(date +%Y-%m-%d)
  $ cp .env .env.backup-$(date +%Y-%m-%d)
  $ tar -czf backup-$(date +%Y-%m-%d).tar.gz docker-compose.yml.backup-* .env.backup-*

□ Documenta versione attuale
  $ git log --oneline -1
  $ git rev-parse HEAD

□ Annuncia downtime (""" + str(downtime_est) + """ minuti previsti)
    </pre>

    <h4 style="margin: 15px 0 10px 0; color: #1976d2;">FASE 1: UPDATE REPOSITORY</h4>
    <pre style="background-color: white; padding: 15px; border-radius: 4px; overflow-x: auto; font-size: 12px; border: 1px solid #ddd;">
$ cd """ + COMPOSE_DIR + """

# STEP 1: Salva personalizzazioni locali (se esistono)
$ git stash

# STEP 2: Verifica cosa cambia PRIMA di pullare
$ git fetch origin master
$ git diff HEAD origin/master --stat  # Vedi quali file cambiano
$ git diff HEAD origin/master -- docker-compose.yml | head -50  # Vedi le differenze

# ❌ Se vedi cose strane, STOP qui e investigare

# STEP 3: Esegui il pull
$ git pull origin master
ASPETTA il completamento

# Verifica che il pull sia andato bene:
$ git log --oneline -1  # Deve mostrare un commit più recente

# ✅ Conferma: git diff HEAD~1 -- docker-compose.yml deve mostrare cambiamenti
    </pre>

    <h4 style="margin: 15px 0 10px 0; color: #1976d2;">FASE 2: VALIDAZIONE FILE</h4>
    <pre style="background-color: white; padding: 15px; border-radius: 4px; overflow-x: auto; font-size: 12px; border: 1px solid #ddd;">
# STEP 1: Controlla nuove variabili .env necessarie
$ diff -u .env .env.example | grep "^+" | grep -v "^+++" | head -20

# STEP 2: Copia le nuove variabili nel tuo .env
$ cat .env.example | grep -E "^[A-Z_]+" | grep -v "^#" > /tmp/env_new.txt
# Poi MANUALMENTE aggiungi le variabili nuove dal file sopra al tuo .env

# STEP 3: Valida la sintassi YAML
$ docker compose config --quiet
# ✅ Se NON vedi errori, puoi proseguire
# ❌ Se vedi errori, NON proseguire, investigare

# STEP 4: Preview (facoltativo, ma consigliato)
$ docker compose config | head -100
    </pre>

    <h4 style="margin: 15px 0 10px 0; color: #1976d2;">FASE 3: AGGIORNAMENTO CONTAINER</h4>
    <pre style="background-color: white; padding: 15px; border-radius: 4px; overflow-x: auto; font-size: 12px; border: 1px solid #ddd;">
# ⏱️ DA QUI IN POI: ~""" + str(downtime_est) + """ MINUTI DI DOWNTIME

# STEP 1: Scarica le nuove immagini (container ancora UP)
$ docker compose pull
# ⏱️ Questo potrebbe durare 5-10 minuti (dipende dalla connessione)

# STEP 2: Stop graceful - dai tempo ai container di terminare connessioni
$ docker compose down
# ✅ Verifiche:
$ docker ps  # Deve essere VUOTO

# STEP 3: Riavvia
$ docker compose up -d

# ⏱️ ASPETTA 30 SECONDI PER STABILIZZARSI

# Verifica che sia partito:
$ docker compose ps
# ✅ Tutti i container devono essere UP
    </pre>

    <h4 style="margin: 15px 0 10px 0; color: #1976d2;">FASE 4: POST-UPDATE VALIDATION</h4>
    <pre style="background-color: white; padding: 15px; border-radius: 4px; overflow-x: auto; font-size: 12px; border: 1px solid #ddd;">
# STEP 1: Verifica container status
$ docker compose ps
# ✅ Tutti devono essere UP? BENE
# ❌ Qualcuno DOWN? Vedi ROLLBACK

# STEP 2: Controlla log (primissimi 50 righe)
$ docker compose logs --tail=50
# ❌ Errori critici? Vedi ROLLBACK

# STEP 3: Healthcheck di base
$ curl -s http://localhost:9999/auth/v1/health | jq .
# ✅ Deve rispondere con JSON

$ curl -s http://localhost:8000/health | jq .
# ✅ Deve rispondere

$ docker compose exec postgres psql -U postgres -c "SELECT version();"
# ✅ Deve mostrare versione PostgreSQL

# STEP 4: Verifica applicazioni critiche
# - Prova ad accedere a Supabase Studio: http://localhost:3000
# - Prova a fare un login/signup
# - Verifica che i dati siano visibili

# ✅ SE TUTTO OK:
$ echo "Update completed successfully" >> /var/log/supabase-update.log
    </pre>

    <h4 style="margin: 15px 0 10px 0; color: #d32f2f;">FASE 5: ROLLBACK (SE QUALCOSA VA MALE)</h4>
    <pre style="background-color: #ffebee; padding: 15px; border-radius: 4px; overflow-x: auto; font-size: 12px; border: 1px solid #d32f2f;">
# ⚠️ ESEGUI SOLO SE L'UPDATE HA PROBLEMI CRITICI

# STEP 1: Torna al commit precedente
$ git reset --hard HEAD~1

# STEP 2: Riavvia container con codice vecchio
$ docker compose down
$ docker compose up -d

# STEP 3: Verifica che è tornato ok
$ docker compose ps
$ docker compose logs --tail=20

# STEP 4: Ripristina database se necessario
$ docker compose exec postgres psql -U postgres -c "DROP DATABASE postgres;"
$ docker compose exec postgres psql -U postgres -f /path/to/backup.sql

# STEP 5: Documenta il problema e apri issue su GitHub
    </pre>

</div>

<!-- FINE RUNBOOK -->

<hr style="border: 1px solid #ddd; margin: 30px 0;">

<div style="background-color: #f0f0f0; padding: 15px; border-radius: 8px; margin-bottom: 30px;">
    <h3 style="margin: 0 0 10px 0; color: #666;">📞 SUPPORT & RESOURCES</h3>
    <ul style="margin: 0; padding-left: 20px; color: #666;">
        <li><strong>Supabase Docker Docs:</strong> <a href="https://github.com/supabase/supabase/tree/master/docker">github.com/supabase/supabase/tree/master/docker</a></li>
        <li><strong>Changelog:</strong> <a href="https://github.com/supabase/supabase/blob/master/docker/CHANGELOG.md">CHANGELOG.md</a></li>
        <li><strong>Issues:</strong> <a href="https://github.com/supabase/supabase/issues">github.com/supabase/supabase/issues</a></li>
        <li><strong>Community:</strong> <a href="https://discord.supabase.com">discord.supabase.com</a></li>
    </ul>
</div>

<div style="background-color: #fffacd; padding: 15px; border-radius: 8px; border-left: 4px solid #f57c00;">
    <h4 style="margin: 0 0 10px 0; color: #f57c00;">⚠️ CHECKLIST FINALE PRIMA DI INIZIARE</h4>
    <ul style="margin: 0; padding-left: 20px;">
        <li>✓ Backup completo eseguito e testato</li>
        <li>✓ Team notificato del downtime previsto</li>
        <li>✓ Ambiente TEST aggiornato con successo (se disponibile)</li>
        <li>✓ Hai tutto il tempo necessario per il rollback se necessario</li>
        <li>✓ Hai questa runbook stampata o salvata</li>
    </ul>
</div>

</body>
</html>
"""
        return html

    def send_email(self):
        """Invia email con la runbook completa"""
        if not MAIL_TO or not SMTP_USER:
            print(f"⚠️  Email non configurata, skippando invio")
            return

        print(f"\n[{datetime.now()}] Inviando email...")

        try:
            msg = MIMEMultipart("alternative")
            status = "🔴 AGGIORNAMENTO DISPONIBILE" if self.has_changes else "🟢 ALLINEATO"
            msg["Subject"] = f"{status} - Supabase Self-Hosted Monitor"
            msg["From"] = MAIL_FROM
            msg["To"] = ", ".join(MAIL_TO)

            if self.has_changes:
                html_content = self._build_runbook()
            else:
                html_content = f"""
<html>
<body style="font-family: Arial, sans-serif;">
    <h2>✅ SUPABASE ALLINEATO</h2>
    <p><strong>Data:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    <p>La configurazione locale è allineata con il repository GitHub master.</p>
    <p>Nessun aggiornamento necessario al momento.</p>

    <hr>
    <p style="color: #666; font-size: 12px;">
        Versione Attuale: {self.current_version_info.get('docker_compose_sha', 'unknown')}<br>
        Versione GitHub: {self.github_commit_sha}
    </p>
</body>
</html>
"""

            msg.attach(MIMEText(html_content, "html"))

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
        """Salva lo stato della versione corrente"""
        state = {
            "last_update": datetime.now().isoformat(),
            "docker_compose_sha": self._get_file_hash(COMPOSE_FILE),
            "git_commit": self._get_git_commit_sha(),
            "github_commit": self.github_commit_sha,
            "github_commit_full": self.github_commit_sha_full,
            "has_changes": self.has_changes,
            "compose_diff_lines": len(self.compose_diff.split('\n')),
            "service_version_changes": self.service_version_changes,
            "env_vars_nuove": self.env_vars_nuove,
            "env_vars_rimosse": self.env_vars_rimosse,
        }

        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f, indent=2)
            print(f"\n  ✓ Stato salvato: {STATE_FILE}")
        except Exception as e:
            print(f"  ✗ Errore salvando stato: {e}")
            self.errors.append(f"Errore salvando stato: {e}")

    def print_summary(self):
        """Stampa un riassunto con eventuali errori"""
        if self.errors:
            print(f"\n⚠️  AVVISI:")
            for error in self.errors:
                print(f"  {error}")

    def run(self):
        """Esegui il ciclo completo"""
        print("=" * 70)
        print("Supabase Self-Hosted Update Monitor v4")
        print("=" * 70)

        self.changelog_excerpt = self._fetch_changelog()

        if not self.compare():
            print("\n✗ Errore durante il confronto dei file")
            self.print_summary()
            return

        if not self.has_changes:
            print("\n✓ Configurazione allineata con GitHub master")
            self.print_summary()
            if MAIL_TO and SMTP_USER:
                self.send_email()
            self.save_state()
            return

        print("\n🔴 AGGIORNAMENTI DISPONIBILI - Generando runbook...")

        self.analyze_with_ai()

        if MAIL_TO and SMTP_USER:
            self.send_email()

        self.save_state()

        self.print_summary()
        print("\n✓ Monitoraggio completato - Runbook inviata")


if __name__ == "__main__":
    try:
        print("=" * 70)
        print("Supabase Self-Hosted Update Monitor v4")
        print("=" * 70)
        print(f"\nConfigurazione:")
        print(f"  COMPOSE_DIR: {COMPOSE_DIR}")
        print(f"  STATE_DIR: {STATE_DIR}")
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
