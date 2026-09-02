#!/bin/bash
set -Eeuo pipefail
trap 'echo "Setup failed at line $LINENO" >&2' ERR

export DEBIAN_FRONTEND=noninteractive
export DEBIAN_PRIORITY=critical
export NEEDRESTART_MODE=a

# Enable universe repository first (jq is in universe)
sudo add-apt-repository universe -y

# Update and upgrade
apt-get update && apt-get upgrade -y

# Install jq and other dependencies
apt-get install -y jq ca-certificates curl || { echo "Failed to install dependencies"; exit 1; }
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Add Docker repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update

# Install Docker
apt-get install docker-ce docker-ce-cli containerd.io -y
sudo docker run hello-world || { echo "Docker installation failed"; exit 1; }

# Install GitLab Runner
curl -fsSL https://packages.gitlab.com/install/repositories/runner/gitlab-runner/script.deb.sh | sudo bash

# Import GPG key manually to fix signature verification issue
curl -fsSL https://packages.gitlab.com/runner/gitlab-runner/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/runner_gitlab-runner-archive-keyring.gpg

# Update and install
apt-get update
apt-get install gitlab-runner -y || { echo "GitLab Runner installation failed"; exit 1; }
sudo gpasswd -a gitlab-runner docker
gitlab-runner --version || { echo "GitLab Runner version check failed"; exit 1; }

# Install Docker rollout after the package creates the gitlab-runner user
sudo mkdir -p /home/gitlab-runner/.docker/cli-plugins
sudo curl --fail --location https://raw.githubusercontent.com/wowu/docker-rollout/main/docker-rollout \
  -o /home/gitlab-runner/.docker/cli-plugins/docker-rollout
sudo chmod +x /home/gitlab-runner/.docker/cli-plugins/docker-rollout
sudo chown -R gitlab-runner:gitlab-runner /home/gitlab-runner/.docker

# Install and configure Netbird
curl -fsSL https://pkgs.netbird.io/install.sh | sudo bash
sudo -E netbird up --management-url https://vpn.pyango.ch --allow-server-ssh --setup-key "$NETBIRD_SETUP_KEY" || { echo "Netbird setup failed"; exit 1; }

# Register GitLab Runner
RUNNER_INFO=$(curl --silent --fail --request POST --url "${CI_API_V4_URL}/user/runners" \
  --data "runner_type=project_type" \
  --data "project_id=$CI_PROJECT_ID" \
  --data "tag_list=$CI_MERGE_REQUEST_IID" \
  --data "description=$CI_MERGE_REQUEST_IID" \
  --data "locked=true" \
  --header "PRIVATE-TOKEN: $GITLAB_ACCESS_TOKEN")

if [ $? -ne 0 ]; then
  echo "Failed to get runner token from GitLab API"
  exit 1
fi

RUNNER_TOKEN=$(echo $RUNNER_INFO | jq -r '.token')

if [ -z "$RUNNER_TOKEN" ] || [ "$RUNNER_TOKEN" = "null" ]; then
  echo "Failed to extract runner token from API response"
  echo "API Response: $RUNNER_INFO"
  exit 1
fi

# Register GitLab Runner
gitlab-runner register --non-interactive --name="$CI_ENVIRONMENT_SLUG" --url="${CI_SERVER_URL}" \
  --token="$RUNNER_TOKEN" --request-concurrency="2" --executor="shell" || { echo "Failed to register GitLab Runner"; exit 1; }

mkdir -p ~/.ssh
cat /tmp/ssh-keys >> ~/.ssh/authorized_keys
chmod 644 ~/.ssh/authorized_keys

# Configure SSH to allow root login with key-based authentication only
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#*PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config
systemctl restart sshd || sudo systemctl restart ssh

# Create builds directory for gitlab-runner user with proper permissions
sudo mkdir -p /home/gitlab-runner/builds
sudo chown gitlab-runner:gitlab-runner /home/gitlab-runner/builds

# Ensure gitlab-runner user owns their entire home directory
sudo chown -R gitlab-runner:gitlab-runner /home/gitlab-runner

command -v docker
command -v netbird
command -v gitlab-runner
systemctl is-active --quiet docker
systemctl is-active --quiet netbird
systemctl is-active --quiet gitlab-runner
gitlab-runner verify

echo "Setup completed successfully"
