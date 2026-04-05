# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

NAutoBuff is a Network Management and Automation System (NMAS) that serves as a centralized Network Source of Truth (NSoT). It combines a Flask web UI, Containerlab virtual topology management, AI-assisted operations via Ollama/Llama3.1, gNMI/SNMP telemetry, and Jenkins CI/CD into a unified platform.

## Setup & Running

### Initial Setup
```bash
cd pilot-config
chmod +x requirements.sh pilot.sh
./requirements.sh    # Installs system deps: Docker, Containerlab, InfluxDB, Grafana, Ngrok, Jenkins, Python 3.12
./pilot.sh           # Pulls LFS files, builds Docker images, imports cEOS image, creates systemd services
```

### Running the Web UI
```bash
./pilot-config/run_nautobuff.sh
# Flask app runs on http://0.0.0.0:5555
```

### Linting & Code Quality
```bash
cd NSOT/python-files
flake8 . --max-line-length=150    # Match CI (Jenkinsfile enforces 150)
yamllint .                        # YAML linting
black .                           # Auto-format Python
```

### Tests
```bash
cd NSOT/python-files
python -m unittest discover -s . -p "test_suite.py"
```

## Architecture

### Core Components

**Web UI** (`NSOT/GUI/flask_app/nautobuff.py`): 1700+ line Flask app on port 5555. All routes are defined here — topology management, device configuration, AI chat, health checks, config generation, file uploads. This is the main entry point that imports and calls all backend modules.

**Backend Python modules** (`NSOT/python-files/`): Individual service scripts, each with a focused responsibility:
- `config_Gen.py` / `push_config.py` — Generate configs from Jinja2 templates, deploy via Netmiko SSH
- `goldenConfig.py` / `config_backup.py` — Fetch running configs and enable rollback
- `ipam.py` — Polls devices via SNMP every 10 seconds, writes to `NSOT/IPAM/ipam_output.csv`
- `health_checks.py` — Monitors CPU, interfaces, routes, LLDP neighbors
- `clab_builder.py` / `clab_push.py` — Generates Containerlab YAML, manages Docker images
- `git_jenkins.py` — Commits config changes to Git and monitors Jenkins builds via API
- `update_hosts.py` / `create_hosts.py` — Manage the `hosts.csv` device inventory
- `user_db.py` — Flask-Login user store (SQLite `users.db` in `NSOT/IPAM/`)
- `password_reset.py` — Rotates credentials on all devices and updates `hosts.csv`

**ML Pipeline** (`NSOT/machine_learning/`):
- `run_pipeline.py` — Orchestrates the AI query workflow
- `predict/llm_extract.py` — Ollama/Llama3.1 for intent extraction and CLI output interpretation
- `predict/predict_specific.py` / `predict/predict_genericshow.py` — Template selection and show command generation
- `helper/ollama_utils.py` — Manages local Ollama model lifecycle
- `helper/fetch_show.py` / `helper/generate_config.py` / `helper/generate_show.py` — Netmiko execution helpers

**Configuration Templates** (`NSOT/templates/`): Jinja2 `.j2` templates for BGP, OSPF, interfaces, VLANs, DHCP, and vendor-specific variants (Cisco/Juniper). `devices_config.yml` maps devices to templates.

**Golden Configs** (`NSOT/golden_configs/`): Pre-validated device configurations that serve as the source of truth for rollback.

### Data Flow

**Config generation:** Web form → `config_Gen.py` → Jinja2 templates → config file → `push_config.py` → Netmiko SSH → device

**AI query:** Natural language → Ollama intent extraction → show command generation → Netmiko SSH → CLI output parsing → response

**Monitoring:** Containerlab devices ← SNMP ← `ipam.py` → CSV; devices ← gNMI ← gnmic service → InfluxDB → Grafana

### Key Config Files
| File | Purpose |
|------|---------|
| `NSOT/IPAM/hosts.csv` | Device inventory with Fernet-encrypted credentials (hostname, IP, username, password) |
| `pilot-config/topo.yml` | Containerlab topology (3 nodes: mgmt, R1, R2 as cEOS containers) |
| `gnmic-stream.yaml` | gNMI telemetry subscriptions (interfaces, CPU, device info) |
| `NSOT/templates/devices_config.yml` | Maps devices to their Jinja2 config templates |
| `NSOT/misc/ngrok_config.yml` | Ngrok auth token for Jenkins webhook tunneling |
| `pilot-config/requirements.txt` | Python dependencies (14 packages: flask, netmiko, easysnmp, ollama, scikit-learn, etc.) |

### Systemd Services (created by `pilot.py`)
- `ipam.service` — Continuous SNMP polling
- `device_health_check.timer` — Periodic health monitoring
- `gnmic_nautobuff.service` — gNMI telemetry collection
- `ngrok.service` — Tunnel for Jenkins webhooks
- `password_update.service` — Scheduled credential rotation

### CI/CD
The `Jenkinsfile` defines stages: flake8 linting, yamllint, then unit tests. Ngrok provides a public webhook URL pointing to the local Jenkins instance. GitHub Actions (`.github/workflows/pylint.yml`) also runs pylint on PRs.

### Rollback Mechanism
Rollback uses Arista `configure session` with `rollback clean-config` — an atomic full-config replace (not line-by-line merge). Before each rollback, `goldenConfig.py` reads the current password from `hosts.csv`, patches credential lines in the golden config via Jinja2, writes it back to disk, then pushes it inside the configure session. This keeps the golden config permanently in sync with rotated credentials. Golden configs **must** include the `Management0` interface or management connectivity will be lost after rollback.

### Containerlab Notes
Containerlab commands in `nautobuff.py` require `sudo`. The `requirements.sh` script sets `cap_net_admin` capabilities but the codebase explicitly uses `sudo clab ...` for topology deploy/destroy operations.
