# NAutoBuff — Network Management & Automation System

NAutoBuff is an **NMAS (Network Management and Automation System)** that acts as a centralized **Network Source of Truth (NSoT)**. It combines a Flask web UI, Containerlab virtual topology management, AI-assisted network operations, gNMI/SNMP telemetry, and a Jenkins CI/CD pipeline into a single open-source platform.

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
| `machine_learning/` | AI pipeline — intent extraction, CLI interpretation, config generation |
| `mcp/` | MCP servers for Claude/ChatGPT Desktop integration |
| `logs/` | Application log file (`nautobuff.log`), ngrok tunnel log |

---

## Why CI/CD?

CI/CD pipelines are the foundation for Infrastructure as Code (IaC), enabling automated, consistent, and auditable network deployments.

![cicd](pictures/nautobuff-cicd.png)

Every config change pushed through the web UI or AI chatbot is:
1. Rendered from a Jinja2 template
2. Committed to Git automatically
3. Pushed to GitHub
4. Picked up by Jenkins via GitHub webhook
5. Validated by the pipeline (flake8 lint → YAML lint → unit tests)
6. Deployed to the device via Netmiko SSH

---

## Features

| Feature | Description |
|---|---|
| **Topology Builder** | Deploy/destroy Containerlab topologies from the UI |
| **Config Generator** | Build device configs from Jinja2 templates (BGP, OSPF, RIP, VLANs, DHCP, Subinterfaces) |
| **Config Push** | Push configs to devices via SSH (Netmiko) with CI/CD validation |
| **Config Rollback** | Atomic configure-session replace to golden config — credentials auto-synced from inventory |
| **Console Access** | Interactive browser-based SSH terminal for each device (xterm.js + WebSocket) |
| **IPAM** | Live SNMP-polled IP address table; sync IPs from Containerlab with one button |
| **Device Health** | CPU, interface, route, and LLDP neighbor health checks |
| **Telemetry** | gNMI streaming via gnmic → InfluxDB → Grafana dashboard |
| **AI Assistant (NBot)** | Natural language queries AND configuration via Ollama LLM — full CI/CD pipeline triggered on configure |
| **Model Selector** | Users can choose any installed Ollama model from the chat UI |
| **MCP Servers** | Claude/ChatGPT Desktop can query and configure devices via MCP tools |
| **Service Health Check** | Start/stop/restart all project systemd services from the burger menu |
| **External Links** | Grafana, InfluxDB, and Jenkins URLs always accessible from the nav menu |
| **Password Rotation** | Automatic credential rotation on all devices every 30 minutes |
| **App Logging** | Timestamped log at `NSOT/logs/nautobuff.log` — noisy polling filtered out |

---

## AI Assistant — NBot

NBot is a local AI assistant powered by **Ollama** (no cloud, no API keys, fully free). It understands natural language and handles both read and write operations:

**Monitor (read-only):**
```
"Show BGP neighbors on R1"
"What's the MAC address of R2?"
"Get interface errors on Ethernet1 on R1"
```

**Configure (full CI/CD pipeline):**
```
"Configure VLAN 111 named test on R1"
"Set up OSPF process 1 on R2 with network 10.0.0.0/24 area 0"
"Add BGP neighbor 10.0.0.2 remote-as 65002 on R1"
```

When a configure request is made:
1. LLM extracts intent and parameters
2. Parameters are rendered through the appropriate **Jinja2 template**
3. Config is committed to **Git** and pushed to **GitHub**
4. **Jenkins** runs lint + unit tests
5. On success, config is deployed to the device via **Netmiko SSH**

The model dropdown in the chat lets users switch between any locally installed Ollama model.

### MCP Integration

`NSOT/mcp/` contains two FastMCP servers for use with Claude Desktop or ChatGPT Desktop:
- `dut_query.py` — query devices (list inventory, run show commands)
- `dut_config.py` — configure devices (apply templates, rollback, backup)

Credentials are read from `hosts.csv` — no credentials are passed as parameters.

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

- **Linux** — Ubuntu 22.04+ (native)
- **macOS** — [OrbStack](https://orbstack.dev) (lightweight Linux VM with full systemd + Docker support)
- **Windows** — WSL2 with Ubuntu
- Git + Git LFS installed
- Internet connection
- A free [Ngrok account](https://dashboard.ngrok.com) (for CI/CD webhook tunneling)

> Containerlab requires a Linux kernel — OrbStack and WSL2 both provide this transparently.

### Step 1 — Fork this repo, then clone your fork

Click **Fork** at the top-right of this page to create your own copy of NAutoBuff under your GitHub account. The CI/CD pipeline watches *your* repo — Jenkins receives webhook notifications from GitHub when you push config changes, so it must point to your fork, not the original.

```bash
mkdir -p ~/projects && cd ~/projects
git clone https://github.com/<your-username>/NAutoBuff.git
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
- Jenkins API token auto-generated → saved to `pilot-config/.jenkins_creds` (gitignored)
- **Prompts for Ngrok auth token** → writes `NSOT/misc/ngrok_config.yml`
- **Optional: AI chatbot** → installs Ollama + pulls `llama3.1:8b` (~5GB)
- **Optional: MCP HTTP servers** → enables Claude/ChatGPT Desktop integration

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
  - Creates all systemd services (IPAM, health check, gNMI, ngrok, password rotation, Ollama)
  - Sets up InfluxDB (org, bucket, API token)
  - Creates Grafana datasource and telemetry dashboard
  - Creates the Jenkins `NAutoBuff` pipeline job pointing at this repo (repo URL auto-detected from git remote)

### Step 4 — Start the web UI

```bash
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

After this, every config change from the web UI or AI chatbot triggers the full Jenkins pipeline automatically.

> **Note:** The Ngrok URL changes each time `ngrok.service` restarts (free tier). Update the GitHub webhook when that happens — the current URL is always visible in the burger menu.

---

## Monitoring

| Service | URL | Credentials |
|---|---|---|
| NAutoBuff Web UI | `http://<host>:5555` | admin / admin |
| Grafana | `http://<host>:3000` | admin / admin |
| InfluxDB | `http://<host>:8086` | admin / NAutoBuff123! |
| Jenkins | via Ngrok URL (burger menu) | admin / admin |

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
| `ollama.service` | Local LLM runtime for NBot AI assistant |
| `jenkins` | CI/CD pipeline server |
| `influxdb` | Time-series telemetry database |
| `grafana-server` | Telemetry dashboards |

---

## Troubleshooting

**Windows/WSL: user not in sudoers**
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

**Jenkins job returns 404 or doesn't trigger**
```bash
# Re-run job provisioning:
cd ~/projects/NAutoBuff
python3 -c "
import sys; sys.path.insert(0, 'pilot-config')
from pilot import provision_jenkins_job; provision_jenkins_job()
"
```

**Ngrok tunnel not running**
```bash
sudo systemctl restart ngrok.service
# New URL appears in NSOT/logs/ngrok.log and the burger menu within ~10s
```

**Jenkins API token expired or invalid**
```bash
# Regenerate token — stored in pilot-config/.jenkins_creds (never committed to git)
# Jenkins → top-right username → Configure → API Token → Add new token
# Then update pilot-config/.jenkins_creds:
#   export JENKINS_TOKEN=<new_token>
```

**AI chatbot not responding**
```bash
sudo systemctl status ollama.service
sudo systemctl start ollama.service
# Check installed models:
ollama list
```

**Config push fails — wrong management IP**
- Open IPAM page → click **Sync IPs from clab** to pull the correct Containerlab-assigned IPs into `hosts.csv`

**Rollback loses management connectivity**
- The golden config didn't include the `Management0` interface when it was saved
- Fix: while devices are reachable, re-run **Golden Config Generator → All devices**

**Rollback fails — device unreachable after topology redeploy**
- Containerlab reassigns management IPs on redeploy; `hosts.csv` has stale IPs
- Open IPAM page → click **Sync IPs from clab**, then retry the rollback
