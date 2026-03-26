import csv
import os

# Define the path to the hosts.csv file
current_dir = os.path.dirname(
    os.path.abspath(__file__)
)  # Directory of the current script
csv_relative_path = os.path.join(
    current_dir, "..", "IPAM", "hosts.csv"
)  # Relative path to the CSV file
CSV_FILE_PATH = os.path.abspath(csv_relative_path)  # Get absolute path


def regenerate_hosts_csv(devices):
    current_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.abspath(os.path.join(current_dir, "..", "IPAM", "hosts.csv"))

    fieldnames = [
        "hostname",
        "username",
        "password",
        "management_ip",
        "subnet_cidr",
        "vendor",
        "old_password",
    ]

    with open(csv_path, mode="w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for dev in devices:
            writer.writerow(
                {
                    "hostname": dev["name"],
                    "username": dev.get("username", ""),
                    "password": dev.get("password", ""),
                    "management_ip": dev.get("ip_address", ""),
                    "subnet_cidr": dev.get("subnet_cidr", ""),
                    "vendor": dev.get("vendor", ""),
                    "old_password": "",
                }
            )

    print(f"[✔] Generated hosts.csv with {len(devices)} device(s).")


def update_hosts_csv(device_name, ip_address, username="admin", password="admin"):
    rows = []
    device_found = False

    if os.path.exists(CSV_FILE_PATH):
        with open(CSV_FILE_PATH, mode="r") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row["hostname"] == device_name:
                    row["management_ip"] = ip_address
                    row["username"] = username
                    row["password"] = password
                    device_found = True
                rows.append(row)

    if not device_found:
        rows.append(
            {
                "hostname": device_name,
                "username": username,
                "password": password,
                "management_ip": ip_address,
                "subnet_cidr": "",
                "vendor": "",
                "old_password": "",
            }
        )

    with open(CSV_FILE_PATH, mode="w", newline="") as csvfile:
        fieldnames = [
            "hostname",
            "username",
            "password",
            "management_ip",
            "subnet_cidr",
            "vendor",
            "old_password",
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"{device_name} updated successfully in {CSV_FILE_PATH}")
