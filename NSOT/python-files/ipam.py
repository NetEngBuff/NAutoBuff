import os
import csv
import time
from datetime import datetime
from easysnmp import Session

# Define the base directory paths
script_dir = os.path.dirname(os.path.abspath(__file__))
ipam_dir = os.path.join(script_dir, "..", "IPAM")
hosts_csv = os.path.join(ipam_dir, "hosts.csv")
output_csv = os.path.join(ipam_dir, "ipam_output.csv")
check_interval = 10  # seconds

# SNMP OIDs
OID_IP = "1.3.6.1.2.1.4.20.1.1"
OID_SUBNET = "1.3.6.1.2.1.4.20.1.3."
OID_IFINDEX = "1.3.6.1.2.1.4.34.1.3.1.4."
OID_IFNAME_BASE = "1.3.6.1.2.1.2.2.1.2."


def collect_device_info(device_name, management_ip):
    """Collects IP, subnet mask, and interface name from a device via SNMP."""
    community = "public"
    device_info = []

    try:
        session = Session(hostname=management_ip, community=community, version=2)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for ip_entry in session.walk(OID_IP):
            ip = ip_entry.value
            try:
                subnet_entry = session.get(OID_SUBNET + ip)
                subnet = subnet_entry.value

                ifindex_entry = session.get(OID_IFINDEX + ip)
                ifindex = ifindex_entry.value

                iface_entry = session.get(OID_IFNAME_BASE + str(ifindex))
                iface_name = iface_entry.value

            except Exception as e:
                subnet = "N/A"
                iface_name = "N/A"
                print(f"⚠️  Could not get full info for {ip} on {device_name}: {e}")

            if iface_name == "Management0" and str(ip).startswith("172.20.20."):
                continue

            device_info.append(
                {
                    "Timestamp": timestamp,
                    "Device Name": device_name,
                    "Interface Name": iface_name,
                    "IP Address": ip,
                    "Subnet Mask": subnet,
                }
            )

    except Exception as e:
        print(f"❌ Error fetching data from {device_name} ({management_ip}): {e}")

    return device_info


def main():
    """Main function to read hosts.csv, collect SNMP data, and write to output CSV."""
    while True:
        with open(hosts_csv, mode="r") as infile, open(
            output_csv, mode="w", newline=""
        ) as outfile:
            reader = csv.DictReader(infile)
            fieldnames = [
                "Timestamp",
                "Device Name",
                "Interface Name",
                "IP Address",
                "Subnet Mask",
            ]
            writer = csv.DictWriter(outfile, fieldnames=fieldnames)
            writer.writeheader()

            for row in reader:
                device_name = row.get("hostname")
                management_ip = row.get("management_ip")

                if not device_name or not management_ip:
                    print(f"⚠️  Skipping row with missing data: {row}")
                    continue

                print(f"\n🔄 Collecting data from {device_name} ({management_ip})...")
                data = collect_device_info(device_name, management_ip)

                if data:
                    for entry in data:
                        print(
                            f"{entry['Device Name']} | {entry['Interface Name']} | {entry['IP Address']} | {entry['Subnet Mask']}"
                        )
                    writer.writerows(data)

        print(
            f"\n✅ IPAM CSV updated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        time.sleep(check_interval)


if __name__ == "__main__":
    main()
