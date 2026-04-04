# NAutoBuff — Network Management & Automation System

NAutoBuff is an **NMAS (Network Management and Automation System)** that acts as a centralized **Network Source of Truth (NSoT)**. It combines a Flask web UI, Containerlab virtual topology management, AI-assisted operations, gNMI/SNMP telemetry, and a Jenkins CI/CD pipeline into a single platform.

![Architecture](pictures/nhub-arch.jpeg)

---

## What is NSOT?

A Network Source of Truth provides a single, reliable reference for all network data — configurations, states, IPs, and inventory.

NSOT is the core of NAutoBuff. It includes:

| Directory | Purpose |
|---|---|
| `GUI/` | Flask web interface (port 5555) |
| `python-files/` | Config generation, deployment, health checks, SNMP/gNMI |
| `golden_configs/` | Pre-validated configuration snapshots for rollback |
| `IPAM/` | IP Address Management and device inventory (`hosts.csv`) |
| `templates/` | Jinja2 templates for building device configs |
| `logs/` | Application log file (`nautobuff.log`), ngrok tunnel log |

---

## Why CI/CD?

CI/CD pipelines are the foundation for Infrastructure as Code (IaC), enabling automated, consistent, and auditable network deployments.

![cicd](pictures/nautobuff-cicd.png)

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
| **Config Rollback** | Atomic configure-session replace to golden config — credentials auto-synced from inventory |
| **Console Access** | Interactive browser-based SSH terminal for each device (xterm.js + WebSocket) |
| **IPAM** | Live SNMP-polled IP address table; sync IPs from Containerlab with one button |
| **Device Health** | CPU, interface, route, and LLDP neighbor health checks |
| **Telemetry** | gNMI streaming via gnmic → InfluxDB → Grafana dashboard |
| **AI Assistant** | Natural language queries via Ollama/Llama3.1 — translated to show commands |
| **Service Health Check** | Start/stop/restart all project systemd services from the burger menu |
| **External Links** | Grafana, InfluxDB, and Jenkins URLs always accessible from the nav menu |
| **Password Rotation** | Automatic credential rotation on all devices every 30 minutes |
| **App Logging** | Timestamped log at `NSOT/logs/nautobuff.log` — noisy polling filtered out |

---

## How Rollback Works

Rollback uses Arista's **configure session** with `rollback clean-config` — an atomic transaction that replaces the entire running config in one commit (not a line-by-line merge). Before every rollback:

1. The current password is read from `hosts.csv`
2. Any credential lines in the golden config are replaced with the current credentials via a Jinja2 template
3. The patched golden config is written back to disk (so it stays permanently in sync)
4. The patched config is pushed inside a configure session and committed atomically

This means rollback never reverts a password rotation, and the golden config file is always deployable at the current credential state.

> **Important:** Golden configs must include the `Management0` interface to preserve management connectivity after rollback. Always retake golden configs while devices are fully reachable using **Tools → Golden Config Generator**.

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
git clone https://github.com/NetEngBuff/NAutoBuff.git
cd NAutoBuff/pilot-config
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
  - Generates Jenkins API token → writes to `run_nautobuff.sh` automatically
  - Creates the Jenkins `NAutoBuff` pipeline job pointing at this repo

### Step 4 — Start the web UI

```bash
# From NAutoBuff/pilot-config/ (where Steps 1–3 left you):
./run_nautobuff.sh
# Access at http://<your-ip>:5555
```

Default login: `admin / admin`

---

## CI/CD — One Manual Step

Everything in the CI/CD pipeline is automated **except** adding the GitHub webhook (this requires GitHub credentials that only you own).

**Add the webhook once:**

1. Open the NAutoBuff burger menu → **External Links → Jenkins** — the webhook URL is shown there
2. Go to your GitHub repo → **Settings → Webhooks → Add webhook**
3. Paste the URL (format: `https://xxxx.ngrok-free.app/github-webhook/`)
4. Content type: `application/json` → **Add webhook**

After this, every `git push` from the web UI triggers the Jenkins pipeline automatically.

> **Note:** The ngrok URL changes each time `ngrok.service` restarts (free tier). Update the GitHub webhook when that happens — the current URL is always visible in the burger menu.

---

## Monitoring

| Service | URL | Credentials |
|---|---|---|
| NAutoBuff Web UI | `http://<host>:5555` | admin / admin |
| Grafana | `http://<host>:3000` | admin / admin |
| InfluxDB | `http://<host>:8086` | admin / NAutoBuff123! |
| Jenkins | via ngrok URL (burger menu) | admin / admin |

Live telemetry log:
```bash
tail -f ~/projects/NAutoBuff/NSOT/logs/nautobuff.log
```

---

## Systemd Services

All managed from **burger menu → Health Check** in the UI, or via the command line:

| Service | Purpose |
|---|---|
| `ipam.service` | SNMP polling every 10s → `IPAM/hosts.csv` |
| `gnmic_nautobuff.service` | gNMI telemetry → InfluxDB |
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
cd ~/projects/NAutoBuff/pilot-config
python3 -c "from pilot import provision_jenkins_job; provision_jenkins_job()"
```

**Ngrok tunnel not running**
```bash
sudo systemctl restart ngrok.service
# New URL appears in NSOT/logs/ngrok.log and the burger menu within ~10s
```

**Config push fails — wrong management IP**
- Open IPAM page → click **Sync IPs from clab** to pull the correct Containerlab-assigned IPs into `hosts.csv`

**Rollback loses management connectivity (can't SSH via 172.20.20.x after rollback)**
- The golden config didn't include the `Management0` interface when it was saved
- Fix: while devices are reachable, re-run **Golden Config Generator → All devices** to capture a fresh snapshot that includes `Management0`
- Future rollbacks will then restore full management connectivity

**Rollback fails — device unreachable after topology redeploy**
- Containerlab reassigns management IPs on redeploy; `hosts.csv` has stale IPs
- Open IPAM page → click **Sync IPs from clab**, then retry the rollback
