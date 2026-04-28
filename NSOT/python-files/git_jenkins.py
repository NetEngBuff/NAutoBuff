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


def _load_jenkins_creds_file():
    global JENKINS_JOB_NAME, JENKINS_USER, JENKINS_TOKEN
    if JENKINS_TOKEN:
        return
    creds_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "pilot-config", ".jenkins_creds")
    )
    if not os.path.exists(creds_path):
        return
    try:
        with open(creds_path, encoding="utf-8") as f:
            for line in f:
                match = re.match(r"export\s+(JENKINS_(?:JOB_NAME|USER|TOKEN))=(.*)", line.strip())
                if not match:
                    continue
                key, value = match.groups()
                value = value.strip().strip('"').strip("'")
                if key == "JENKINS_JOB_NAME":
                    JENKINS_JOB_NAME = value or JENKINS_JOB_NAME
                elif key == "JENKINS_USER":
                    JENKINS_USER = value or JENKINS_USER
                elif key == "JENKINS_TOKEN":
                    JENKINS_TOKEN = value or JENKINS_TOKEN
    except Exception as e:
        print(f"Could not load Jenkins credentials file: {e}")


_load_jenkins_creds_file()


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


# --- Jenkins API (localhost for monitoring — always available) ---


def _auth():
    return (JENKINS_USER, JENKINS_TOKEN) if JENKINS_TOKEN else None


def get_latest_build_number():
    """Get current latest build number via localhost."""
    try:
        url = f"http://localhost:8080/job/{JENKINS_JOB_NAME}/api/json?tree=lastBuild%5Bnumber%5D"
        resp = requests.get(url, auth=_auth(), timeout=15)
        if resp.status_code == 200:
            return resp.json().get("lastBuild", {}).get("number")
    except Exception as e:
        print(f"Error fetching latest build number: {e}")
    return None


def wait_for_new_build(pre_build, timeout=120):
    """Poll localhost until a build number greater than pre_build appears."""
    print("Waiting for Jenkins build to start...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        number = get_latest_build_number()
        if number and number > pre_build:
            print(f"Jenkins build #{number} started.")
            return number
    print("Timed out waiting for Jenkins — check GitHub webhook points to current Ngrok URL.")
    return None


def check_build_result(build_number):
    """Check build result via localhost."""
    try:
        url = f"http://localhost:8080/job/{JENKINS_JOB_NAME}/{build_number}/api/json"
        resp = requests.get(url, auth=_auth(), timeout=15)
        if resp.status_code == 200:
            return resp.json().get("result")
    except Exception as e:
        print(f"Error checking build result: {e}")
    return None


# --- Combined Pipeline Monitor ---


def monitor_jenkins_job(pre_build=None):
    if pre_build is None:
        pre_build = get_latest_build_number() or 0

    build_number = wait_for_new_build(pre_build)
    if not build_number:
        return "Failed to retrieve the latest build number"

    while True:
        result = check_build_result(build_number)
        if result == "SUCCESS":
            print("Jenkins job completed successfully.")
            return "SUCCESS"
        elif result == "FAILURE":
            print("Jenkins job failed.")
            return "FAILURE"
        elif result is None:
            print("Build in progress, checking again in 10 seconds...")
            time.sleep(10)
        else:
            print(f"Unexpected result: {result}")
            return result


# --- Entry Point for Route ---


def push_and_monitor_jenkins():
    pre_build = get_latest_build_number() or 0
    if git_push():
        return monitor_jenkins_job(pre_build)
    return "Git push failed"


# For CLI testing
if __name__ == "__main__":
    result = push_and_monitor_jenkins()
    print("Final result:", result)
