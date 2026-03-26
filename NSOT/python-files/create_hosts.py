# create_hosts_csv.py
import csv
import os


def write_hosts_csv(data_rows, append=False):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    ipam_dir = os.path.join(base_dir, "..", "IPAM")
    os.makedirs(ipam_dir, exist_ok=True)
    csv_path = os.path.join(ipam_dir, "hosts.csv")

    headers = [
        "hostname",
        "username",
        "password",
        "management_ip",
        "subnet_cidr",
        "vendor",
        "old_password",
    ]

    mode = "a" if append else "w"
    file_exists = os.path.exists(csv_path)

    with open(csv_path, mode, newline="") as csvfile:
        writer = csv.writer(csvfile)
        if not append or not file_exists:
            writer.writerow(headers)
        for row in data_rows:
            writer.writerow(row + [""])

    return csv_path
