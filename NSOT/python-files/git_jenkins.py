import re
import subprocess
import time
import requests
import os

JENKINS_JOB_NAME = os.environ.get("JENKINS_JOB_NAME", "NAutoBuff")
JENKINS_USER = os.environ.get("JENKINS_USER", "admin")
JENKINS_TOKEN = os.environ.get("JENKINS_TOKEN", "")

# Headers required for ngrok free-tier tunnels: without this, ngrok
# intercepts the request with an HTML browser-warning page instead of
# forwarding it to Jenkins, causing JSON parse failures.
NGROK_HEADERS = {"ngrok-skip-browser-warning": "true"}

# --- Git Push Functions ---


def has_changes_to_commit():
    result = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True
    )
    return bool(result.stdout.strip())


def git_push():
    if not has_changes_to_commit():
        print("No changes to commit.")
        return False
    try:
        # subprocess.run(["git", "add", f"configs/{device_id}.cfg"], check=True)  # Optional: limit to one file
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "commit", "-m", "Auto-config push"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("Changes pushed to Git successfully.")
        time.sleep(10)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Git push failed: {e}")
        return False


# --- Ngrok URL Fetcher ---


def find_ngrok_log_file(
    search_root=os.path.expanduser("~/projects/NAutoBuff/NSOT/logs"),
):
    for root, dirs, files in os.walk(search_root):
        for file in files:
            if file == "ngrok.log":
                return os.path.join(root, file)
    return None


def get_latest_ngrok_url(log_file_path):
    try:
        with open(log_file_path, "r") as file:
            lines = file.readlines()
        for line in reversed(lines):
            match = re.search(r"url=(https://[a-zA-Z0-9\-]+\.ngrok-free\.app)", line)
            if match:
                ngrok_url = match.group(1)
                print("Ngrok URL found:", ngrok_url)
                return ngrok_url
        print("No ngrok URL found in the log.")
        return None
    except FileNotFoundError:
        print(f"Log file not found: {log_file_path}")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None


# --- Jenkins Build Checks ---


def get_latest_build_number(jenkins_base_url, user, token):
    try:
        time.sleep(7)
        url = (
            f"{jenkins_base_url}/job/{JENKINS_JOB_NAME}/api/json?tree=lastBuild[number]"
        )
        print("Fetching latest build number from URL:", url)
        auth = (user, token) if token else None
        response = requests.get(url, auth=auth, headers=NGROK_HEADERS, timeout=15)
        if response.status_code != 200:
            print(f"Jenkins API returned HTTP {response.status_code}. "
                  f"Response: {response.text[:300]}")
            return None
        response_data = response.json()
        latest_build_number = response_data.get("lastBuild", {}).get("number")
        if latest_build_number:
            print("Latest Build Number:", latest_build_number)
            return latest_build_number
        else:
            print("Failed to retrieve the latest build number from response.")
            return None
    except Exception as e:
        print(f"Error fetching latest build number: {e}")
        return None


def check_build_result(jenkins_base_url, latest_build_number, user, token):
    build_url = (
        f"{jenkins_base_url}/job/{JENKINS_JOB_NAME}/{latest_build_number}/api/json"
    )
    print("Checking build result from URL:", build_url)
    auth = (user, token) if token else None
    response = requests.get(build_url, auth=auth, headers=NGROK_HEADERS, timeout=15)
    if response.status_code == 200:
        build_info = response.json()
        result = build_info.get("result")
        print(f"Build result for build {latest_build_number}: {result}")
        return result
    else:
        print(f"Failed to retrieve build status for build {latest_build_number}. "
              f"HTTP {response.status_code}: {response.text[:200]}")
        return None


# --- Combined Pipeline Monitor ---


def monitor_jenkins_job():
    log_file_path = find_ngrok_log_file()
    if not log_file_path:
        print("Unable to find ngrok.log file.")
        return "Failed"

    jenkins_base_url = get_latest_ngrok_url(log_file_path)
    if not jenkins_base_url:
        print("Unable to retrieve ngrok URL for Jenkins.")
        return "Failed"

    latest_build_number = get_latest_build_number(
        jenkins_base_url, JENKINS_USER, JENKINS_TOKEN
    )
    if not latest_build_number:
        return "Failed to retrieve the latest build number"

    while True:
        build_result = check_build_result(
            jenkins_base_url, latest_build_number, JENKINS_USER, JENKINS_TOKEN
        )
        if build_result == "SUCCESS":
            print("Jenkins job completed successfully.")
            return "SUCCESS"
        elif build_result == "FAILURE":
            print("Jenkins job failed.")
            return "FAILURE"
        elif build_result is None:
            print("Jenkins job is still in progress. Checking again in 10 seconds...")
            time.sleep(10)
        else:
            print(f"Unexpected result: {build_result}")
            return build_result


# --- Entry Point for Route ---


def push_and_monitor_jenkins():
    if git_push():
        return monitor_jenkins_job()
    return "Git push failed"


# For CLI testing
if __name__ == "__main__":
    result = push_and_monitor_jenkins()
    print("Final result:", result)
