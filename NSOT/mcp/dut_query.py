"""
NAutoBuff MCP Server — Device Query
Exposes show command traversal as an MCP tool.
Credentials are read from hosts.csv — never passed as parameters.

Transports:
  stdio (default) — used by Claude Code / .mcp.json
  HTTP            — used by Claude Desktop or remote clients
                    start with: python dut_query.py --http [--port 8001]
"""
import sys
import time
from netmiko import ConnectHandler
from fastmcp import FastMCP
from fastmcp.prompts import Message
from _device_lookup import lookup_device, list_devices

mcp = FastMCP("nautobuff-query")


# ── CLI traversal helpers ─────────────────────────────────────────────────────

def _parse_cli_output(output: str) -> dict:
    skip_words = {"?", "<cr>", "show", "%"}
    commands = {}
    for line in output.split("\n"):
        parts = line.split()
        if not parts:
            continue
        cmd = parts[0]
        if cmd not in skip_words and "#" not in cmd:
            commands[cmd] = " ".join(parts[1:])
    return commands


def _is_config_query(keyword: str) -> bool:
    config_words = ["running-config", "running", "configured", "configuration", "config"]
    return any(w in keyword.lower() for w in config_words)


def _get_config(connection, keyword: str) -> tuple[str, dict]:
    search_term = keyword.lower()
    for word in ["running-config", "running", "configured", "configuration", "config"]:
        search_term = search_term.replace(word, "").strip()

    if search_term:
        cmd = f"show running-config | grep {search_term.split()[0]}"
        log = f"Searching config for: {search_term}\n"
    else:
        cmd = "show running-config"
        log = "Getting full running configuration...\n"

    try:
        output = connection.send_command(cmd, read_timeout=30)
        if output.strip():
            return log + "Configuration retrieved\n\n", {cmd: output}
        return log + "No configuration found\n\n", {}
    except Exception as e:
        return log + f"Failed: {e}\n\n", {}


def _find_and_run(connection, path: str, keywords: list) -> dict:
    try:
        time.sleep(0.1)
        output = connection.send_command(f"{path} ?", read_timeout=3)

        if "invalid" in output.lower() or "unrecognized" in output.lower():
            return {"log": f"  {path}\n", "output": None}

        available = _parse_cli_output(output)
        can_execute = "<cr>" in output

        if not keywords and can_execute:
            try:
                result = connection.send_command(path, read_timeout=30)
                return {"log": f"  Found: {path}\n", "output": {path: result}}
            except Exception as e:
                return {"log": f"  Failed: {e}\n", "output": None}

        if keywords:
            next_kw = keywords[0]
            match = next(
                (cmd for cmd in available
                 if cmd.lower() == next_kw or cmd.lower().startswith(next_kw[:3])),
                None,
            )
            if match:
                result = _find_and_run(connection, f"{path} {match}", keywords[1:])
                return {"log": f"  → {path} {match}\n" + result["log"], "output": result["output"]}
            else:
                available_list = list(available.keys())[:10]
                return {
                    "log": f"  ✗ '{next_kw}' not found. Available: {', '.join(available_list)}\n",
                    "output": None,
                }

        return {"log": "", "output": None}

    except Exception as e:
        return {"log": f"  Error: {e}\n", "output": None}


def _query_device(connection, keyword: str) -> tuple[str, dict]:
    if _is_config_query(keyword):
        return _get_config(connection, keyword)

    keywords = keyword.lower().split()
    log = f"Searching for: {keyword}\n"
    for base in ["show", "show ip"]:
        result = _find_and_run(connection, base, keywords)
        log += result["log"]
        if result["output"]:
            return log, result["output"]
    return log, {}


# ── MCP tools ─────────────────────────────────────────────────────────────────

@mcp.prompt()
def query_help() -> list[Message]:
    """Instructions for using dut_query effectively."""
    return [Message("""When querying devices with the nautobuff-query tools:

- **device**: use the hostname from NAutoBuff inventory (e.g. "R1", "R2")
- **keyword**: space-separated CLI keywords. The tool auto-prepends "show".
  Examples: "ip bgp summary", "interfaces counters", "running-config"
- If the first attempt fails, check the "Available:" hints in the output and adjust.
- For config queries use words like "running-config", "configuration", or "config <section>".
- list_devices() shows all devices in inventory.
""", role="user")]


@mcp.tool()
def list_inventory() -> str:
    """List all devices available in the NAutoBuff inventory."""
    devices = list_devices()
    if not devices:
        return "No devices found in inventory. Check NSOT/IPAM/hosts.csv."
    return "Devices in NAutoBuff inventory:\n" + "\n".join(f"  - {d}" for d in devices)


@mcp.tool()
def query_dut(device: str, keyword: str) -> str:
    """Query a network device for information using show commands.

    Credentials are read automatically from NAutoBuff inventory.

    Args:
        device:  Hostname from NAutoBuff inventory (e.g. "R1", "R2")
        keyword: Space-separated CLI keywords — tool auto-prepends "show".
                 Examples: "ip bgp summary", "interfaces Ethernet1 counters errors",
                           "running-config", "lldp neighbors"
    """
    creds = lookup_device(device)
    if not creds:
        devices = list_devices()
        hint = f"Available devices: {', '.join(devices)}" if devices else "hosts.csv appears empty."
        return f"Device '{device}' not found in NAutoBuff inventory. {hint}"

    out = [f"Querying {device} for: {keyword}", "=" * 60, ""]
    try:
        connection = ConnectHandler(**creds)
        log, results = _query_device(connection, keyword)
        out.append(log)
        if results:
            out += ["=" * 60, "RESULTS", "=" * 60, ""]
            for cmd, output in results.items():
                out += [f"Command: {cmd}", "-" * 60, output, "", "=" * 60, ""]
        else:
            out.append(f"✗ No output found for '{keyword}'")
        connection.disconnect()
    except Exception as e:
        out.append(f"✗ Connection error: {e}")

    return "\n".join(out)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--http" in sys.argv:
        port = 8001
        for i, arg in enumerate(sys.argv):
            if arg == "--port" and i + 1 < len(sys.argv):
                port = int(sys.argv[i + 1])
        mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
    else:
        mcp.run()
