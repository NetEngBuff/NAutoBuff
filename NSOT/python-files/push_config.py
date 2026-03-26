import csv
import os
from netmiko import ConnectHandler


def push_configuration(device_id):
    print(f"Starting configuration push for device_id: {device_id}")

    # Locate the CSV
    csv_path = os.path.join(os.path.dirname(__file__), "..", "IPAM", "hosts.csv")
    print(f"Looking for CSV file at path: {csv_path}")

    management_ip, username, password = None, None, None

    try:
        with open(csv_path, mode="r", encoding="utf-8-sig") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                print(f"Checking row: {row['hostname'].strip()} vs {device_id.strip()}")
                if row["hostname"].strip() == device_id.strip():
                    management_ip = row["management_ip"].strip()
                    username = row["username"].strip()
                    password = row["password"].strip()
                    print(f"Found device with IP: {management_ip}, user: {username}")
                    break
    except FileNotFoundError:
        print("Host CSV file not found.")
        return "Host CSV file not found."

    if not management_ip or not username or not password:
        print("Device credentials or management IP missing.")
        return "Device credentials or management IP missing."

    # Locate the config file
    config_path = os.path.join(
        os.path.dirname(__file__), "..", "configs", f"{device_id}.cfg"
    )
    print(f"Looking for config file at path: {config_path}")

    if not os.path.isfile(config_path):
        print("Config file not found for device.")
        return "Config file not found for device."

    # Map vendor to Netmiko device type
    vendor_map = {
        "arista": "arista_eos",
        "cisco": "cisco_ios",
        "juniper": "juniper_junos",
    }
    vendor = ""
    try:
        with open(csv_path, mode="r", encoding="utf-8-sig") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row["hostname"].strip() == device_id.strip():
                    vendor = row.get("vendor", "").strip().lower()
                    break
    except Exception:
        pass
    device_type = vendor_map.get(vendor, "arista_eos")

    # Build Netmiko connection dictionary
    device = {
        "device_type": device_type,
        "host": management_ip,
        "username": username,
        "password": password,
    }

    print(f"Attempting to connect to device {management_ip}...")

    try:
        net_connect = ConnectHandler(**device)
        print("Connected successfully.")
        net_connect.enable()

        output = net_connect.send_config_from_file(config_path)
        print("Configuration push output:")
        print(output)

        net_connect.disconnect()
        print("Disconnected from device.")
        return "Configuration pushed successfully."
    except Exception as e:
        print(f"Failed to push configuration: {e}")
        return f"Failed to push configuration: {e}"
