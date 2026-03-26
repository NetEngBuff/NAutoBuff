import subprocess
from netmiko import (
    ConnectHandler,
    NetmikoTimeoutException,
    NetmikoAuthenticationException,
)


def ping_local(destination):
    """Ping a destination locally and return success status and output."""
    try:
        print(f"Starting local ping to {destination}")
        # Perform a local ping, with 3 packets
        output = subprocess.check_output(
            ["ping", "-c", "3", destination],
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )
        print("Local ping successful")
        return True, output
    except subprocess.CalledProcessError as e:
        print("Local ping failed")
        return False, e.output


def ping_remote(source, destination, username, password, device_type="arista_eos"):
    """Ping a destination from a remote source via SSH using Netmiko."""
    device = {
        "device_type": device_type,
        "ip": source,
        "username": username,
        "password": password,
    }

    try:
        print(f"Attempting SSH connection to {source} with username: {username}")
        # Establish SSH connection using Netmiko
        ssh_conn = ConnectHandler(**device)
        print(f"SSH login successful to {source}")

        # Switch to enable mode if necessary
        print(f"Switching to enable mode on {source}")
        ssh_conn.enable()

        # Execute the ping command remotely
        print(f"Starting remote ping test from {source} to {destination}")
        command = f"ping {destination}"
        output = ssh_conn.send_command(command)

        print("Ping test completed, disconnecting SSH")
        ssh_conn.disconnect()

        # Check if ping was successful based on packet loss in output
        if "0% packet loss" in output:
            print(f"Ping successful from {source} to {destination}")
            return True, output
        else:
            print(f"Ping failed from {source} to {destination}")
            return False, output
    except (
        NetmikoTimeoutException,
        NetmikoAuthenticationException,
    ) as e:
        print(f"SSH connection failed to {source}. Error: {str(e)}")
        return False, str(e)
    except Exception as e:
        print(f"An unexpected error occurred with {source}: {str(e)}")
        return False, str(e)
