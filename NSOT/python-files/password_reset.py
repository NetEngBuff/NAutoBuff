import csv
import os
import subprocess
import time
import string
import secrets
from jinja2 import Environment, FileSystemLoader
from netmiko import ConnectHandler
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gnmi_hosts import update_gnmic_yaml_from_hosts  # noqa: E402

# File paths
script_dir = os.path.dirname(os.path.abspath(__file__))
ipam_dir = os.path.join(script_dir, "..", "IPAM")
templates_dir = os.path.abspath(os.path.join(script_dir, "..", "templates"))
hosts_csv = os.path.abspath(os.path.join(ipam_dir, "hosts.csv"))

check_interval = 1800  # 30 minutes

# Vendor → template file mapping
VENDOR_TEMPLATE_MAP = {
    "arista": "password_change_arista.j2",
}

# Vendor → Netmiko device_type mapping
VENDOR_NETMIKO_MAP = {
    "arista": "arista_eos",
    "cisco": "cisco_ios",
    "juniper": "juniper_junos",
}

jinja_env = Environment(loader=FileSystemLoader(templates_dir))


def generate_password(length=16):
    """Generate a secure alphanumeric password (no special chars to avoid shell/CLI issues)."""
    characters = string.ascii_letters + string.digits
    return "".join(secrets.choice(characters) for _ in range(length))


def push_password_to_device(host_row, new_password):
    """
    Connect to a device and push the new password via vendor-specific Jinja2 template.
    Returns (success: bool, message: str).
    """
    hostname = host_row.get("hostname", "").strip()
    mgmt_ip = host_row.get("management_ip", "").strip()
    username = host_row.get("username", "").strip()
    current_password = host_row.get("password", "").strip()
    vendor = host_row.get("vendor", "").strip().lower()

    if not mgmt_ip or not username or not current_password:
        return False, f"{hostname}: missing credentials or management IP"

    template_file = VENDOR_TEMPLATE_MAP.get(vendor)
    if not template_file:
        return False, f"{hostname}: no password change template for vendor '{vendor}'"

    device_type = VENDOR_NETMIKO_MAP.get(vendor)
    if not device_type:
        return False, f"{hostname}: unknown Netmiko device_type for vendor '{vendor}'"

    # Render the Jinja2 template
    try:
        template = jinja_env.get_template(template_file)
        config = template.render(username=username, new_password=new_password)
    except Exception as e:
        return False, f"{hostname}: template render failed — {e}"

    # Parse rendered config into individual commands
    commands = [line.strip() for line in config.splitlines() if line.strip()]

    # Connect and push
    try:
        conn = ConnectHandler(
            device_type=device_type,
            host=mgmt_ip,
            username=username,
            password=current_password,
        )
        conn.enable()
        conn.send_config_set(commands)
        if device_type == "arista_eos":
            conn.send_command("write memory")
        elif device_type in ("cisco_ios", "cisco_xe"):
            conn.send_command("write memory")
        elif device_type == "juniper_junos":
            conn.commit()
        conn.disconnect()
        return True, f"{hostname}: password updated successfully"
    except Exception as e:
        return False, f"{hostname}: SSH push failed — {e}"


def rotate_passwords():
    """Read hosts.csv, push new password to each device, update CSV on success."""
    if not os.path.exists(hosts_csv):
        print("[✘] hosts.csv not found — skipping rotation")
        return

    rows = []
    fieldnames = []

    with open(hosts_csv, mode="r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for col in ["subnet_cidr", "vendor", "old_password"]:
            if col not in fieldnames:
                fieldnames.append(col)
        rows = list(reader)

    if not rows:
        print("[✘] hosts.csv is empty — skipping rotation")
        return

    for row in rows:
        vendor = row.get("vendor", "").strip().lower()
        if not vendor:
            print(f"[⚠] {row.get('hostname')}: no vendor set, skipping")
            continue

        new_password = generate_password()
        success, message = push_password_to_device(row, new_password)
        print(f"[{'✔' if success else '✘'}] {message}")

        if success:
            row["old_password"] = row.get("password", "")
            row["password"] = new_password

    with open(hosts_csv, mode="w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"[✔] Password rotation complete. Next rotation in {check_interval // 60} minutes.")

    # Keep gnmic credentials in sync with the new passwords
    update_gnmic_yaml_from_hosts()
    try:
        subprocess.run(["sudo", "systemctl", "restart", "gnmic_nautobuff.service"], check=True)
        print("[✔] gnmic_nautobuff.service restarted with updated credentials")
    except subprocess.CalledProcessError:
        print("[⚠] Could not restart gnmic_nautobuff.service — restart it manually")


def main():
    while True:
        rotate_passwords()
        time.sleep(check_interval)


if __name__ == "__main__":
    main()
