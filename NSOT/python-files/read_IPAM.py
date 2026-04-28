import time
import csv
from itertools import groupby


class IPAMReader:
    def __init__(self, file_path, update_interval=10):
        self.file_path = file_path
        self.update_interval = update_interval
        self.ipam_data = []

    def preprocess_data(self, data):
        """Calculate row spans for the Device Name column."""
        data = [
            row for row in data
            if not (
                row.get("Interface Name") == "Management0"
                and str(row.get("IP Address", "")).startswith("172.20.20.")
            )
        ]
        processed_data = []
        for device_name, rows in groupby(data, key=lambda x: x["Device Name"]):
            rows = list(rows)
            rows[0]["rowspan"] = len(rows)  # Add rowspan to the first row
            for row in rows:
                processed_data.append(row)
        return processed_data

    def read_ipam_file(self):
        """Continuously read the IPAM file and update data."""
        while True:
            try:
                with open(self.file_path, "r") as file:
                    reader = csv.DictReader(file)
                    raw_data = list(reader)
                    self.ipam_data = self.preprocess_data(raw_data)  # Preprocess data
            except FileNotFoundError:
                print("IPAM file not found.")
                self.ipam_data = []
            except Exception as e:
                print(f"Error reading IPAM file: {e}")
            time.sleep(self.update_interval)
