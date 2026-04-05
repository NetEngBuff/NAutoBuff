#!/bin/bash
set -e

# 1. Load the environment (Reuse Python 3.12)
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
pyenv shell 3.12.8

# 2. Activate the existing VENV
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "Error: venv not found! Run requirements.sh first."
    exit 1
fi

# 3. Fix Containerlab permissions
sudo setcap cap_net_admin,cap_net_raw,cap_sys_admin+ep $(which containerlab)

# 4. Jenkins credentials
# Generate your API token: Jenkins → top-right username → Configure → API Token
export JENKINS_USER="${JENKINS_USER:-admin}"
export JENKINS_TOKEN="${JENKINS_TOKEN:-116cebc6637ad2eedcb5740b9f54bd6285}"
export JENKINS_JOB_NAME="${JENKINS_JOB_NAME:-NAutoBuff}"

echo "Launching NAutoBuff Flask App..."
echo "Running on http://0.0.0.0:5555"

# Direct path to your Flask app
"$(dirname "$0")/venv/bin/python" ~/projects/NAutoBuff/NSOT/GUI/flask_app/nautobuff.py
