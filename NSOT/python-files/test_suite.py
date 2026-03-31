import os
import unittest
from validate_ping import ping_ip

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "IPAM", "hosts.csv")
_CSV_EXISTS = os.path.exists(CSV_PATH)


class TestNetworkAutomation(unittest.TestCase):

    @unittest.skipUnless(_CSV_EXISTS, "hosts.csv not present — skipping (CI environment)")
    def test_file_exists(self):
        print("\n" + "-" * 50)
        print("Checking Required File Existence")
        print("-" * 50)
        self.assertTrue(os.path.exists(CSV_PATH), f"File not found: {CSV_PATH}")
        print(f"Found CSV file at {CSV_PATH}\n")

    @unittest.skipUnless(_CSV_EXISTS, "hosts.csv not present — skipping (CI environment)")
    def test_ping_ip_from_csv(self):
        print("\n" + "-" * 50)
        print("Testing Ping Responses from IPs in CSV")
        print("-" * 50)

        ips = []
        with open(CSV_PATH, mode="r") as file:
            for index, line in enumerate(file):
                if index == 0:
                    continue  # Skip header row
                ip = line.strip().split(",")[3]
                ips.append(ip)

        for ip in ips:
            if ip == "management_ip":
                continue
            result = ping_ip(ip)
            print(f"Ping test for IP {ip}: {'Success' if result else 'Failure'}")
            self.assertTrue(result, f"Ping failed for IP {ip}")

        print("Ping tests from CSV completed.\n")


if __name__ == "__main__":
    unittest.main()
