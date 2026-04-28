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

    # Build a dict of new rows keyed by hostname (index 0)
    new_by_hostname = {row[0]: row for row in data_rows if row}

    if append and os.path.exists(csv_path):
        # Read existing rows, then upsert: new rows overwrite existing ones with same hostname
        existing_rows = []
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for r in reader:
                hn = r.get("hostname", "").strip()
                if hn not in new_by_hostname:
                    existing_rows.append(r)

        with open(csv_path, "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(headers)
            for r in existing_rows:
                writer.writerow([
                    r.get("hostname", ""),
                    r.get("username", ""),
                    r.get("password", ""),
                    r.get("management_ip", ""),
                    r.get("subnet_cidr", ""),
                    r.get("vendor", ""),
                    r.get("old_password", ""),
                ])
            for row in data_rows:
                writer.writerow(row + [""])
    else:
        with open(csv_path, "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(headers)
            for row in data_rows:
                writer.writerow(row + [""])

    return csv_path
