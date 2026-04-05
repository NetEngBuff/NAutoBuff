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
    print(f"Waiting for new Jenkins build (previous was #{pre_build})...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        number = get_latest_build_number()
        if number and (pre_build is None or number > pre_build):
            print(f"New build detected: #{number}")
            return number
    print("Timed out waiting for a new Jenkins build — check GitHub webhook points to current Ngrok URL.")
    return None


def check_build_result(build_number):
    """Check build result via localhost."""
    try:
        url = f"http://localhost:8080/job/{JENKINS_JOB_NAME}/{build_number}/api/json"
        print(f"Checking build result: {url}")
        resp = requests.get(url, auth=_auth(), timeout=15)
        if resp.status_code == 200:
            result = resp.json().get("result")
            print(f"Build #{build_number} result: {result}")
            return result
        print(f"HTTP {resp.status_code} checking build result")
    except Exception as e:
        print(f"Error checking build result: {e}")
    return None


# --- Combined Pipeline Monitor ---


def monitor_jenkins_job():
    pre_build = get_latest_build_number() or 0

    # Ngrok URL is still used to update GitHub webhook — print it for reference
    log_file_path = find_ngrok_log_file()
    if log_file_path:
        ngrok_url = get_latest_ngrok_url(log_file_path)
        if ngrok_url:
            print(f"GitHub webhook should point to: {ngrok_url}/github-webhook/")

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
            print("Build still in progress, checking again in 10 seconds...")
            time.sleep(10)
        else:
            print(f"Unexpected result: {result}")
            return result


# --- Entry Point for Route ---


def push_and_monitor_jenkins():
    if git_push():
        return monitor_jenkins_job()
    return "Git push failed"


# For CLI testing
if __name__ == "__main__":
    result = push_and_monitor_jenkins()
    print("Final result:", result)
