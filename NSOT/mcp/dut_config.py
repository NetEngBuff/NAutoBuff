"""
NAutoBuff MCP Server — Device Configuration
Exposes config push, rollback, and backup as MCP tools.
Credentials are read from hosts.csv — never passed as parameters.

Transports:
  stdio (default) — used by Claude Code / .mcp.json
  HTTP            — used by Claude Desktop or remote clients
                    start with: python dut_config.py --http [--port 8002]
"""
import sys
from datetime import datetime
from netmiko import ConnectHandler
from fastmcp import FastMCP
from _device_lookup import lookup_device, list_devices

mcp = FastMCP("nautobuff-config")


# ── MCP tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def configure_dut(
    device: str,
    config_commands: list[str],
    save_config: bool = True,
) -> str:
    """Push configuration commands to a network device.

    Credentials are read automatically from NAutoBuff inventory.

    Args:
        device:          Hostname from NAutoBuff inventory (e.g. "R1", "R2")
        config_commands: List of EOS config commands to apply
        save_config:     Write memory after applying (default True)
    """
    creds = lookup_device(device)
    if not creds:
        devices = list_devices()
        hint = f"Available devices: {', '.join(devices)}" if devices else "hosts.csv appears empty."
        return f"Device '{device}' not found in NAutoBuff inventory. {hint}"

    out = [
        f"Configuring {device}",
        f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 60, "",
    ]
    try:
        connection = ConnectHandler(**creds)
        connection.enable()
        out.append("✓ Connected\n")

        out.append("Commands to apply:")
        out.append("-" * 60)
        out.extend(f"  {cmd}" for cmd in config_commands)
        out.append("")

        connection.send_command_timing("configure terminal", delay_factor=2)
        output_lines = []
        for cmd in config_commands:
            output_lines.append(connection.send_command_timing(cmd, delay_factor=2))
        connection.send_command_timing("end", delay_factor=2)
        output = "\n".join(output_lines)
        out += ["✓ Configuration applied\n", "Output:", "-" * 60, output, ""]

        if save_config:
            save_out = connection.send_command_timing("write memory", delay_factor=2)
            out += ["✓ Configuration saved", save_out, ""]

        connection.disconnect()
        out += ["=" * 60, "✓ Configuration completed successfully"]

    except Exception as e:
        out.append(f"✗ Error: {e}")

    return "\n".join(out)


@mcp.tool()
def rollback_dut(
    device: str,
    checkpoint: str = "startup-config",
) -> str:
    """Roll back a device configuration to a saved checkpoint.

    Credentials are read automatically from NAutoBuff inventory.

    Args:
        device:     Hostname from NAutoBuff inventory (e.g. "R1", "R2")
        checkpoint: Config checkpoint to restore (default: startup-config)
    """
    creds = lookup_device(device)
    if not creds:
        devices = list_devices()
        hint = f"Available devices: {', '.join(devices)}" if devices else "hosts.csv appears empty."
        return f"Device '{device}' not found in NAutoBuff inventory. {hint}"

    out = [
        f"Rolling back {device} to: {checkpoint}",
        f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 60, "",
    ]
    try:
        connection = ConnectHandler(**creds)
        out.append("✓ Connected\n")

        output = connection.send_command(
            f"configure replace {checkpoint}", read_timeout=60
        )
        out += [output, "", "✓ Rollback completed\n"]

        save_out = connection.send_command("write memory")
        out += ["✓ Configuration saved", save_out, ""]

        connection.disconnect()
        out += ["=" * 60, "✓ Rollback completed successfully"]

    except Exception as e:
        out.append(f"✗ Error: {e}")

    return "\n".join(out)


@mcp.tool()
def backup_dut(device: str) -> str:
    """Retrieve and return the full running configuration of a device.

    Credentials are read automatically from NAutoBuff inventory.

    Args:
        device: Hostname from NAutoBuff inventory (e.g. "R1", "R2")
    """
    creds = lookup_device(device)
    if not creds:
        devices = list_devices()
        hint = f"Available devices: {', '.join(devices)}" if devices else "hosts.csv appears empty."
        return f"Device '{device}' not found in NAutoBuff inventory. {hint}"

    out = [
        f"Backing up {device}",
        f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 60, "",
    ]
    try:
        connection = ConnectHandler(**creds)
        out.append("✓ Connected\n")

        config = connection.send_command("show running-config", read_timeout=60)
        out += ["=" * 60, "RUNNING CONFIGURATION", "=" * 60, "", config, ""]

        connection.disconnect()
        out += ["=" * 60, "✓ Backup completed successfully"]

    except Exception as e:
        out.append(f"✗ Error: {e}")

    return "\n".join(out)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--http" in sys.argv:
        port = 8002
        for i, arg in enumerate(sys.argv):
            if arg == "--port" and i + 1 < len(sys.argv):
                port = int(sys.argv[i + 1])
        mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
    else:
        mcp.run()
