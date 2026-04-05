"""
Shared helper: look up device credentials from NAutoBuff's hosts.csv inventory.
MCP tools call this instead of accepting username/password as parameters,
keeping credentials out of AI model context entirely.
"""
import csv
import os

_current_dir = os.path.dirname(os.path.abspath(__file__))
HOSTS_CSV = os.path.abspath(os.path.join(_current_dir, "..", "IPAM", "hosts.csv"))


def lookup_device(hostname: str) -> dict | None:
    """
    Return a Netmiko device dict for the given hostname, or None if not found.
    Reads hosts.csv fresh each call so IPAM sync changes are reflected immediately.
    """
    try:
        with open(HOSTS_CSV, newline="") as f:
            for row in csv.DictReader(f):
                if row.get("hostname", "").strip().lower() == hostname.strip().lower():
                    return {
                        "device_type": row.get("device_type", "arista_eos"),
                        "ip": row.get("management_ip", "").strip(),
                        "username": row.get("username", "admin").strip(),
                        "password": row.get("password", "").strip(),
                    }
    except FileNotFoundError:
        pass
    return None


def list_devices() -> list[str]:
    """Return all hostnames from inventory."""
    try:
        with open(HOSTS_CSV, newline="") as f:
            return [row["hostname"].strip() for row in csv.DictReader(f) if row.get("hostname")]
    except FileNotFoundError:
        return []
