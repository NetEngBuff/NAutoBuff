# NAutoHUB — Network Management & Automation System

NAutoHUB is an **NMAS (Network Management and Automation System)** that acts as a centralized **Network Source of Truth (NSoT)**. It combines a Flask web UI, Containerlab virtual topology management, AI-assisted operations, gNMI/SNMP telemetry, and a Jenkins CI/CD pipeline into a single platform.

![Architecture](pictures/nhub-arch.jpeg)

---

## What is NSOT?

A Network Source of Truth provides a single, reliable reference for all network data — configurations, states, IPs, and inventory.

NSOT is the core of NAutoHUB. It includes:

| Directory | Purpose |
|---|---|
| `GUI/` | Flask web interface (port 5555) |
| `python-files/` | Config generation, deployment, health checks, SNMP/gNMI |
| `golden_configs/` | Pre-validated configuration snapshots for rollback |
| `IPAM/` | IP Address Management and device inventory (`hosts.csv`) |
| `templates/` | Jinja2 templates for building device configs |
| `logs/` | Application log file (`nahub.log`), ngrok tunnel log |

---

## Why CI/CD?

CI/CD pipelines are the foundation for Infrastructure as Code (IaC), enabling automated, consistent, and auditable network deployments.

![cicd](pictures/nahub-cicd.png)

Every config change pushed through the web UI is:
1. Committed to Git automatically
2. Pushed to GitHub
3. Picked up by Jenkins via GitHub webhook
4. Validated by the pipeline (flake8 lint → YAML lint → unit tests)

---

## Features

| Feature | Description |
|---|---|
| **Topology Builder** | Deploy/destroy Containerlab topologies from the UI |
| **Config Generator** | Build device configs from Jinja2 templates (BGP, OSPF, RIP, VLANs, DHCP) |
| **Config Push** | Push configs to devices via SSH (Netmiko) with CI/CD validation |
| **Config Rollback** | Restore any device to its last golden config in one click |
| **IPAM** | Live SNMP-polled IP address table; sync IPs from Containerlab with one button |
| **Device Health** | CPU, interface, route, and LLDP neighbor health checks |
| **Telemetry** | gNMI streaming via gnmic → InfluxDB → Grafana dashboard |
| **AI Assistant** | Natural language queries via Ollama/Llama3.1 — translated to show commands |
| **Service Health Check** | Start/stop/restart all project systemd services from the burger menu |
| **External Links** | Grafana, InfluxDB, and Jenkins URLs always accessible from the nav menu |
| **Password Rotation** | Automatic credential rotation on all devices every 30 minutes |
| **App Logging** | Timestamped log at `NSOT/logs/nahub.log` — noisy polling filtered out |

---

## Setup

### Prerequisites

- Ubuntu 22.04+ (or WSL2 on Windows)
- Git + Git LFS installed
- Internet connection
- A free [Ngrok account](https://dashboard.ngrok.com) (for CI/CD webhook tunneling)

### Step 1 — Clone

```bash
mkdir -p ~/projects && cd ~/projects
git clone https://github.com/NetEngBuff/NAutoHUB.git
cd NAutoHUB/pilot-config
chmod +x requirements.sh pilot.sh
```

### Step 2 — Install dependencies

```bash
./requirements.sh
```

`requirements.sh` fully automates:
- Docker, Containerlab, InfluxDB, Grafana, Ngrok, Java, Jenkins
- Python 3.12 (via pyenv) + all required packages
- Jenkins first-time setup (wizard bypassed, `admin/admin` created, plugins installed)
- **Prompts once for your Ngrok auth token** → writes `NSOT/misc/ngrok_config.yml`

> After this step completes, **log out and back in** so Docker group changes take effect.

### Step 3 — Bringup

```bash
./pilot.sh
```

`pilot.sh` handles:
- Pulling LFS assets (cEOS image, Docker images)
- Building Docker images
- Applying network interface config (Netplan)
- Running `pilot.py` which:
  - Creates all systemd services (IPAM, health check, gNMI, ngrok, password rotation)
  - Sets up InfluxDB (org, bucket, API token)
  - Creates Grafana datasource and telemetry dashboard
  - Generates Jenkins API token → writes to `run_nahub.sh` automatically
  - Creates the Jenkins `NAutoHUB` pipeline job pointing at this repo

### Step 4 — Start the web UI

```bash
./run_nahub.sh
# Access at http://<your-ip>:5555
```

Default login: `admin / admin`

---

## CI/CD — One Manual Step

Everything in the CI/CD pipeline is automated **except** adding the GitHub webhook (this requires GitHub credentials that only you own).

**Add the webhook once:**

1. Open the NAutoHUB burger menu → **External Links → Jenkins** — the webhook URL is shown there
2. Go to your GitHub repo → **Settings → Webhooks → Add webhook**
3. Paste the URL (format: `https://xxxx.ngrok-free.app/github-webhook/`)
4. Content type: `application/json` → **Add webhook**

After this, every `git push` from the web UI triggers the Jenkins pipeline automatically.

> **Note:** The ngrok URL changes each time `ngrok.service` restarts (free tier). Update the GitHub webhook when that happens — the current URL is always visible in the burger menu.

---

## Monitoring

| Service | URL | Credentials |
|---|---|---|
| NAutoHUB Web UI | `http://<host>:5555` | admin / admin |
| Grafana | `http://<host>:3000` | admin / admin |
| InfluxDB | `http://<host>:8086` | admin / NAutoHUB123! |
| Jenkins | via ngrok URL (burger menu) | admin / admin |

Live telemetry log:
```bash
tail -f ~/projects/NAutoHUB/NSOT/logs/nahub.log
```

---

## Systemd Services

All managed from **burger menu → Health Check** in the UI, or via the command line:

| Service | Purpose |
|---|---|
| `ipam.service` | SNMP polling every 10s → `IPAM/hosts.csv` |
| `gnmic_nautohub.service` | gNMI telemetry → InfluxDB |
| `device_health_check.timer` | Periodic CPU/interface/route checks |
| `password_update.service` | Credential rotation every 30 min |
| `ngrok.service` | Jenkins webhook tunnel |
| `jenkins` | CI/CD pipeline server |
| `influxdb` | Time-series telemetry database |
| `grafana-server` | Telemetry dashboards |

---

## Troubleshooting

**WSL: user not in sudoers**
```powershell
# In PowerShell as Administrator:
wsl -u root
usermod -aG sudo <your_wsl_username>
```

**Docker permission denied after requirements.sh**
```bash
# Log out and back in, or run:
newgrp docker
```

**Jenkins job returns 404**
```bash
# Re-run job provisioning:
cd ~/projects/NAutoHUB/pilot-config
python3 -c "from pilot import provision_jenkins_job; provision_jenkins_job()"
```

**Ngrok tunnel not running**
```bash
sudo systemctl restart ngrok.service
# New URL appears in NSOT/logs/ngrok.log and the burger menu within ~10s
```

**Config push fails — wrong management IP**
- Open IPAM page → click **Sync IPs from clab** to pull the correct Containerlab-assigned IPs into `hosts.csv`
