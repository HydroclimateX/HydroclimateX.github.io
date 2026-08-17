#!/usr/bin/env bash
# Idempotent bootstrap for the HydroclimateX Hong Kong server (Ubuntu 24.04).
# Run as root through Alibaba Cloud Command Assistant.

set -euo pipefail

APP_ROOT="/opt/hydroclimatex-wasp"
REPO_DIR="$APP_ROOT/repo"
STATE_DIR="$APP_ROOT/state"
DEPLOY_BRANCH="${WASP_DEPLOY_BRANCH:-codex/wasp-hong-kong}"
REPO_URL="${WASP_REPO_URL:-https://github.com/HydroclimateX/HydroclimateX.github.io.git}"

info() { printf '[wasp-bootstrap] %s\n' "$*"; }
fail() { printf '[wasp-bootstrap] error: %s\n' "$*" >&2; exit 1; }

require_supported_root() {
  if [ "$EUID" -ne 0 ]; then
    fail "This bootstrap must be run as root."
  fi
  if [ ! -r /etc/os-release ]; then
    fail "Cannot identify the operating system. Ubuntu 24.04 is required."
  fi
  . /etc/os-release
  if [ "${ID:-}" != "ubuntu" ] || [ "${VERSION_ID:-}" != "24.04" ]; then
    fail "Ubuntu 24.04 is required; found ${PRETTY_NAME:-unknown}."
  fi
}

install_runtime() {
  export DEBIAN_FRONTEND=noninteractive
  info "Installing base packages."
  apt-get update
  apt-get install -y ca-certificates cron curl dnsutils git gnupg openssl

  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc

  cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-noble}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
}

sync_repository() {
  if [ -d "$REPO_DIR/.git" ]; then
    info "Updating the existing deployment checkout."
    (
      cd "$REPO_DIR"
      git fetch --prune origin \
        "+refs/heads/$DEPLOY_BRANCH:refs/remotes/origin/$DEPLOY_BRANCH"
      if git show-ref --verify --quiet "refs/heads/$DEPLOY_BRANCH"; then
        git checkout "$DEPLOY_BRANCH"
      else
        git checkout -b "$DEPLOY_BRANCH" "origin/$DEPLOY_BRANCH"
      fi
      git merge --ff-only "origin/$DEPLOY_BRANCH"
    )
  elif [ -e "$REPO_DIR" ]; then
    fail "$REPO_DIR exists but is not a Git checkout; refusing to overwrite it."
  else
    info "Cloning the WASP deployment branch."
    git clone --branch "$DEPLOY_BRANCH" --single-branch "$REPO_URL" "$REPO_DIR"
  fi
}

main() {
  require_supported_root
  install_runtime
  install -d -m 0755 "$APP_ROOT" "$STATE_DIR/www" "$STATE_DIR/conf"
  sync_repository

  export WASP_STATE_DIR="$STATE_DIR"
  cd "$REPO_DIR"
  info "Running the guarded HTTPS deployment."
  bash ./deploy.sh
  info "Bootstrap and deployment completed."
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
