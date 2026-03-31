import csv
import os
from datetime import datetime
from netmiko import ConnectHandler


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

    # Locate the CSV
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

    # Map vendor to Netmiko device type
    vendor_map = {
        "arista": "arista_eos",
        "cisco": "cisco_ios",
        "juniper": "juniper_junos",
    }

    device_type = vendor_map.get(device_vendor.lower())
    if not device_type:
        return False, f"Unsupported vendor: {device_vendor}", None

    # Create golden_configs directory if it doesn't exist
    golden_config_dir = os.path.join(os.path.dirname(__file__), "..", "golden_configs")
    os.makedirs(golden_config_dir, exist_ok=True)

    # Build Netmiko connection dictionary
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

        # Get running configuration based on vendor
        if device_type == "arista_eos":
            running_config = net_connect.send_command("show running-config")
        elif device_type in ["cisco_ios", "cisco_xe"]:
            running_config = net_connect.send_command("show running-config")
        elif device_type == "juniper_junos":
            running_config = net_connect.send_command("show configuration")
        else:
            running_config = net_connect.send_command("show running-config")

        net_connect.disconnect()

        # Save to golden_configs folder with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"{device_id}_golden.cfg"
        backup_filename_timestamped = f"{device_id}_backup_{timestamp}.cfg"

        golden_config_path = os.path.join(golden_config_dir, backup_filename)
        timestamped_backup_path = os.path.join(golden_config_dir, backup_filename_timestamped)

        # Save as golden config (overwrites previous)
        with open(golden_config_path, "w") as f:
            f.write(running_config)

        # Also save timestamped backup
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
    Rollback device configuration to its golden config.

    Args:
        device_id (str): Device hostname
        device_vendor (str): Device vendor (arista, cisco, juniper)

    Returns:
        tuple: (success: bool, message: str)
    """
    print(f"Starting rollback for device: {device_id}")

    # Check if golden config exists — accept both naming conventions
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

    # Read golden config
    try:
        with open(golden_config_path, "r") as f:
            golden_config = f.read()
    except Exception as e:
        return False, f"Failed to read golden config: {str(e)}"

    # Get device credentials
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

    # Map vendor to Netmiko device type
    vendor_map = {
        "arista": "arista_eos",
        "cisco": "cisco_ios",
        "juniper": "juniper_junos",
    }

    device_type = vendor_map.get(device_vendor.lower())
    if not device_type:
        return False, f"Unsupported vendor: {device_vendor}"

    # Build Netmiko connection dictionary
    device = {
        "device_type": device_type,
        "host": management_ip,
        "username": username,
        "password": password,
    }

    print(f"Connecting to device {management_ip} for rollback...")

    try:
        net_connect = ConnectHandler(**device)
        net_connect.enable()

        # Parse config into commands
        config_commands = [
            line.strip()
            for line in golden_config.split("\n")
            if line.strip() and not line.strip().startswith("!")
        ]

        # Push golden configuration
        output = net_connect.send_config_set(config_commands)
        print("Rollback output:")
        print(output)

        # Save configuration
        if device_type == "arista_eos":
            save_output = net_connect.send_command("write memory")
        elif device_type in ["cisco_ios", "cisco_xe"]:
            save_output = net_connect.send_command("write memory")
        elif device_type == "juniper_junos":
            save_output = net_connect.commit()
        else:
            save_output = ""

        print(f"Save output: {save_output}")

        net_connect.disconnect()
        return True, f"✅ Successfully rolled back {device_id} to golden configuration"

    except Exception as e:
        print(f"❌ Rollback failed: {e}")
        return False, f"Rollback failed: {str(e)}"
