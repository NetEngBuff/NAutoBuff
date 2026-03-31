import csv
import os
import re
from datetime import datetime

from jinja2 import Template
from netmiko import ConnectHandler


# ---------------------------------------------------------------------------
# Credential line templates — injected with current hosts.csv creds before
# every rollback so the golden config never carries a stale password.
# ---------------------------------------------------------------------------
_CRED_TEMPLATES = {
    "arista_eos": (
        "username {{ username }} privilege 15 role network-admin secret 0 {{ password }}"
    ),
    "cisco_ios": (
        "username {{ username }} privilege 15 secret 0 {{ password }}"
    ),
}

# Pattern that matches credential lines to strip from the stored golden config.
# Covers:
#   username admin secret <hash>
#   username admin privilege 15 role network-admin secret <hash>   (Arista)
#   enable secret / enable password
#   aaa root secret
_CRED_RE = re.compile(
    r"^(username\s+\S+.*\s(secret|password)\s"
    r"|enable\s+(secret|password)\s"
    r"|aaa\s+root\s+secret\s)",
    re.IGNORECASE,
)


def _strip_cred_lines(config_text):
    """Return config text with all credential lines removed."""
    return "\n".join(
        line for line in config_text.splitlines()
        if not _CRED_RE.match(line.strip())
    )


def _inject_creds(config_text, device_type, username, password):
    """
    Prepend a freshly-rendered credential line (using current hosts.csv
    credentials) to the config so the rollback never resets the password.
    """
    tmpl_str = _CRED_TEMPLATES.get(device_type)
    if not tmpl_str:
        return config_text  # Juniper or unknown — leave as-is
    cred_line = Template(tmpl_str).render(username=username, password=password)
    return cred_line + "\n" + config_text


def backup_running_config(device_id, device_vendor):
    """
    Backup the current running configuration of a device to golden_configs folder.

    Args:
        device_id (str): Device hostname
        device_vendor (str): Device vendor (arista, cisco, juniper)

    Returns:
        tuple: (success: bool, message: str, backup_path: str)
    """
    print(f"Starting config backup for device: {device_id}")

    csv_path = os.path.join(os.path.dirname(__file__), "..", "IPAM", "hosts.csv")
    management_ip, username, password = None, None, None

    try:
        with open(csv_path, mode="r", encoding="utf-8-sig") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row["hostname"].strip() == device_id.strip():
                    management_ip = row["management_ip"].strip()
                    username = row["username"].strip()
                    password = row["password"].strip()
                    break
    except FileNotFoundError:
        return False, "Host CSV file not found.", None

    if not management_ip or not username or not password:
        return False, "Device credentials or management IP missing.", None

    vendor_map = {
        "arista": "arista_eos",
        "cisco": "cisco_ios",
        "juniper": "juniper_junos",
    }
    device_type = vendor_map.get(device_vendor.lower())
    if not device_type:
        return False, f"Unsupported vendor: {device_vendor}", None

    golden_config_dir = os.path.join(os.path.dirname(__file__), "..", "golden_configs")
    os.makedirs(golden_config_dir, exist_ok=True)

    device = {
        "device_type": device_type,
        "host": management_ip,
        "username": username,
        "password": password,
    }

    print(f"Connecting to device {management_ip} to retrieve running config...")

    try:
        net_connect = ConnectHandler(**device)
        net_connect.enable()

        if device_type == "juniper_junos":
            running_config = net_connect.send_command("show configuration")
        else:
            running_config = net_connect.send_command("show running-config")

        net_connect.disconnect()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"{device_id}_golden.cfg"
        backup_filename_timestamped = f"{device_id}_backup_{timestamp}.cfg"

        golden_config_path = os.path.join(golden_config_dir, backup_filename)
        timestamped_backup_path = os.path.join(golden_config_dir, backup_filename_timestamped)

        with open(golden_config_path, "w") as f:
            f.write(running_config)
        with open(timestamped_backup_path, "w") as f:
            f.write(running_config)

        print(f"✅ Backup saved to: {golden_config_path}")
        print(f"✅ Timestamped backup: {timestamped_backup_path}")

        return True, f"Running config backed up successfully to {backup_filename}", golden_config_path

    except Exception as e:
        print(f"❌ Failed to backup config: {e}")
        return False, f"Failed to backup config: {str(e)}", None


def rollback_to_golden_config(device_id, device_vendor):
    """
    Rollback device configuration to its golden config using configure replace
    (atomic swap — not line-by-line merge).  Before pushing, the golden config
    is patched with the current credentials from hosts.csv so password rotation
    is never undone by a rollback.

    Args:
        device_id (str): Device hostname
        device_vendor (str): Device vendor (arista, cisco, juniper)

    Returns:
        tuple: (success: bool, message: str)
    """
    print(f"Starting rollback for device: {device_id}")

    # ------------------------------------------------------------------
    # 1. Locate golden config
    # ------------------------------------------------------------------
    golden_config_dir = os.path.join(os.path.dirname(__file__), "..", "golden_configs")
    golden_config_path = None
    for candidate in [
        os.path.join(golden_config_dir, f"{device_id}_golden.cfg"),
        os.path.join(golden_config_dir, f"goldenconfigs_{device_id}.cfg"),
    ]:
        if os.path.exists(candidate):
            golden_config_path = candidate
            break

    if not golden_config_path:
        return False, f"No golden config found for {device_id}. Please create a backup first."

    try:
        with open(golden_config_path, "r") as f:
            golden_config = f.read()
    except Exception as e:
        return False, f"Failed to read golden config: {str(e)}"

    # ------------------------------------------------------------------
    # 2. Get current credentials from hosts.csv
    # ------------------------------------------------------------------
    csv_path = os.path.join(os.path.dirname(__file__), "..", "IPAM", "hosts.csv")
    management_ip, username, password = None, None, None

    try:
        with open(csv_path, mode="r", encoding="utf-8-sig") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row["hostname"].strip() == device_id.strip():
                    management_ip = row["management_ip"].strip()
                    username = row["username"].strip()
                    password = row["password"].strip()
                    break
    except FileNotFoundError:
        return False, "Host CSV file not found."

    if not management_ip or not username or not password:
        return False, "Device credentials or management IP missing."

    vendor_map = {
        "arista": "arista_eos",
        "cisco": "cisco_ios",
        "juniper": "juniper_junos",
    }
    device_type = vendor_map.get(device_vendor.lower())
    if not device_type:
        return False, f"Unsupported vendor: {device_vendor}"

    # ------------------------------------------------------------------
    # 3. Patch golden config: strip old creds, inject current creds
    # ------------------------------------------------------------------
    patched_config = _strip_cred_lines(golden_config)
    patched_config = _inject_creds(patched_config, device_type, username, password)

    device = {
        "device_type": device_type,
        "host": management_ip,
        "username": username,
        "password": password,
    }

    print(f"Connecting to device {management_ip} for rollback (configure replace)...")

    try:
        net_connect = ConnectHandler(**device)
        net_connect.enable()

        config_lines = [
            line for line in patched_config.splitlines()
            if line.strip() and not line.strip().startswith("!")
        ]

        # ------------------------------------------------------------------
        # 5a. Arista EOS — configure session (atomic, no SCP needed)
        #     'rollback clean-config' wipes the candidate first so this is
        #     a true replace, not a merge.
        #     All commands are sent in one send_config_set call with
        #     enter/exit disabled so Netmiko never sends 'configure
        #     terminal' or 'end' — only our own session open/commit.
        # ------------------------------------------------------------------
        if device_type == "arista_eos":
            session_name = "nahub_rollback"
            all_session_cmds = (
                [f"configure session {session_name}", "rollback clean-config"]
                + config_lines
                + ["commit"]
            )
            output = net_connect.send_config_set(
                all_session_cmds,
                enter_config_mode=False,
                exit_config_mode=False,
                cmd_verify=False,
                read_timeout=120,
            )
            print(f"Configure session output:\n{output}")

        # ------------------------------------------------------------------
        # 5b. Juniper — load override (native atomic replace)
        # ------------------------------------------------------------------
        elif device_type == "juniper_junos":
            net_connect.send_config_set(
                ["load override terminal"] + config_lines + [""]
            )
            net_connect.commit()

        # ------------------------------------------------------------------
        # 5c. Cisco IOS/XE — line-by-line (configure replace needs SCP;
        #     fallback to merge push for now)
        # ------------------------------------------------------------------
        else:
            output = net_connect.send_config_set(config_lines)
            print(f"Config push output:\n{output}")

        net_connect.send_command_timing("write memory", delay_factor=3)
        net_connect.disconnect()
        return True, f"✅ Successfully rolled back {device_id} to golden configuration"

    except Exception as e:
        print(f"❌ Rollback failed: {e}")
        return False, f"Rollback failed: {str(e)}"
