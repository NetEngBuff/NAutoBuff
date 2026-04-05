# NAutoBuff MCP Servers — Setup Guide

NAutoBuff exposes two MCP servers that let AI assistants query and configure your network devices directly:

| Server | Default Port | Tools |
|--------|-------------|-------|
| `nautobuff-query` | 8001 | `query_dut`, `list_inventory` |
| `nautobuff-config` | 8002 | `configure_dut`, `rollback_dut`, `backup_dut` |

Credentials are read automatically from `NSOT/IPAM/hosts.csv` — never passed as parameters.

---

## Prerequisites

`fastmcp` and `netmiko` are installed automatically by `requirements.sh`.

The **HTTP services** (ports 8001/8002) are **opt-in** — you are asked during
`requirements.sh` whether to enable them. They are only needed for Options 2 and 3
below (Claude Desktop / remote clients). Claude Code over SSH works without them.

To check if HTTP services are running:
```bash
sudo systemctl status nautobuff_mcp_query.service
sudo systemctl status nautobuff_mcp_config.service
```

To enable them after the fact:
```bash
touch ~/projects/NAutoBuff/NSOT/misc/.mcp_http_enabled
cd ~/projects/NAutoBuff/pilot-config && python pilot.py
```

---

## Option 1 — Claude Code (Recommended)

Claude Code is a CLI tool that runs directly on your Ubuntu server over SSH.
This is the simplest setup — no tunnels, no remote ports needed.

### Install Claude Code on the server

```bash
# Requires Node.js 18+
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash
sudo apt-get install -y nodejs
npm install -g @anthropic-ai/claude-code
```

### Authenticate

```bash
claude login
```

### Use the MCP servers

The `.mcp.json` file in the project root registers both servers automatically.
Just open Claude Code from the project directory:

```bash
cd ~/projects/NAutoBuff
claude
```

Claude Code will detect `.mcp.json` and connect to both MCP servers on startup.
You can then ask things like:

```
> Show me the BGP neighbors on R1
> What are the interface counters on Ethernet1 of R2?
> Configure OSPF on R1 with router-id 1.1.1.1
> Back up the running config from R2
```

---

## Option 2 — Claude Desktop (via SSH tunnel)

Claude Desktop runs on your laptop (Mac/Windows). To reach the MCP servers
on a remote Ubuntu server, forward the ports over SSH.

### Step 1 — Open SSH tunnels

Run this on your **laptop** (keep the terminal open):

```bash
ssh -N -L 8001:localhost:8001 -L 8002:localhost:8002 user@<server-ip>
```

Or add to `~/.ssh/config` for a permanent alias:

```
Host nautobuff
    HostName <server-ip>
    User <your-user>
    LocalForward 8001 localhost:8001
    LocalForward 8002 localhost:8002
```

Then: `ssh -N nautobuff`

### Step 2 — Add to Claude Desktop config

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (Mac)
or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "nautobuff-query": {
      "url": "http://localhost:8001/mcp"
    },
    "nautobuff-config": {
      "url": "http://localhost:8002/mcp"
    }
  }
}
```

Restart Claude Desktop. The tools will appear in the MCP tools panel.

---

## Option 3 — Claude Code on your laptop (remote HTTP)

If you prefer to run Claude Code locally but connect to a remote NAutoBuff server:

```json
// Add to ~/.claude/mcp.json or project .mcp.json on your laptop
{
  "mcpServers": {
    "nautobuff-query": {
      "url": "http://<server-ip>:8001/mcp"
    },
    "nautobuff-config": {
      "url": "http://<server-ip>:8002/mcp"
    }
  }
}
```

> **Note:** Only use direct IP access on a trusted network. For public-facing
> servers, use the SSH tunnel approach above.

---

## ChatGPT

ChatGPT does not support MCP natively. To use NAutoBuff tools with ChatGPT
you would need to wrap the MCP servers in an OpenAPI spec and register them
as a Custom GPT Action — this is a separate integration not covered here.

## Gemini

Google has announced MCP support in Gemini but availability varies by product
and region. When Gemini MCP support stabilises, the HTTP URLs from Option 2/3
above should work directly.

---

## Managing MCP servers from the NAutoBuff UI

The MCP servers appear in the **Service Health** panel (burger menu → Health Check)
alongside Jenkins, InfluxDB, and Grafana. You can start, stop, and restart them
from there without touching the terminal.

---

## Manual start (without systemd)

**stdio mode** (for Claude Code / `.mcp.json`):
```bash
cd ~/projects/NAutoBuff
PYTHONPATH=NSOT/mcp pilot-config/venv/bin/python NSOT/mcp/dut_query.py
PYTHONPATH=NSOT/mcp pilot-config/venv/bin/python NSOT/mcp/dut_config.py
```

**HTTP mode** (for Claude Desktop / remote clients):
```bash
PYTHONPATH=NSOT/mcp pilot-config/venv/bin/python NSOT/mcp/dut_query.py --http --port 8001
PYTHONPATH=NSOT/mcp pilot-config/venv/bin/python NSOT/mcp/dut_config.py --http --port 8002
```
