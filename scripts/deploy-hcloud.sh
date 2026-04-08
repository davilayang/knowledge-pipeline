#!/usr/bin/env bash
# Deploy knowledge-pipeline to a Hetzner VPS via Docker Compose.
#
# Usage:
#   ./scripts/deploy-hcloud.sh setup                # One-time project setup
#   ./scripts/deploy-hcloud.sh deploy               # Pull latest, rebuild & restart
#   ./scripts/deploy-hcloud.sh deploy --no-build    # Restart only (skip image build)
#
# Config is loaded from .env.deploy (create from .env.deploy.example).
# Env vars can also be set inline: DEPLOY_TARGET=... ./scripts/deploy-hcloud.sh deploy
#
# Variables:
#   IDENTITY_FILE     Path to SSH private key (required)
#   HETZNER_SERVER or DEPLOY_TARGET    SSH target (required)
#   DEPLOY_USER       Non-root user (default: deploy)

set -euo pipefail

# --- Config ------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Source deploy config if it exists
if [ -f "${PROJECT_DIR}/.env.deploy" ]; then
    set -a
    # shellcheck source=/dev/null
    source "${PROJECT_DIR}/.env.deploy"
    set +a
fi

: "${IDENTITY_FILE:=}"
: "${DEPLOY_USER:=deploy}"
REMOTE_DIR="knowledge-pipeline"

# --- Helpers -----------------------------------------------------------------

info()  { printf "\033[1;34m==> %s\033[0m\n" "$*"; }
warn()  { printf "\033[1;33mWARN: %s\033[0m\n" "$*"; }
error() { printf "\033[1;31mERROR: %s\033[0m\n" "$*" >&2; exit 1; }

usage() {
    sed -n '2,/^$/s/^# //p' "$0"
    exit 1
}

server_ip() { echo "${HETZNER_SERVER#*@}"; }

deploy_target() { echo "${DEPLOY_TARGET:-${DEPLOY_USER}@$(server_ip)}"; }

# --- Pre-flight checks -------------------------------------------------------

[ -n "${HETZNER_SERVER:-}" ] || [ -n "${DEPLOY_TARGET:-}" ] \
    || error "Set HETZNER_SERVER or DEPLOY_TARGET in .env.deploy"
[ -n "${IDENTITY_FILE}" ] || error "Set IDENTITY_FILE in .env.deploy (e.g. ~/.ssh/id_ed25519)"
[ -f "${IDENTITY_FILE}" ] || error "Identity file not found: ${IDENTITY_FILE}"

cd "$PROJECT_DIR"

# --- Remote helpers -----------------------------------------------------------

ssh_opts() { echo "-o ConnectTimeout=10 -i ${IDENTITY_FILE} -o IdentitiesOnly=yes"; }

run_deploy() {
    ssh $(ssh_opts) "$(deploy_target)" "$@"
}

compose_cmd() {
    echo "docker compose"
}

# ==============================================================================
# SETUP — one-time project setup
# ==============================================================================

do_setup() {
    for f in docker-compose.yml .env; do
        [ -f "$f" ] || error "Missing $f — run from project root"
    done

    local target
    target="$(deploy_target)"

    info "Setting up project on ${target}..."
    run_deploy true 2>/dev/null \
        || error "Cannot SSH to ${target}"

    # Clone repo
    if run_deploy "[ -d ~/${REMOTE_DIR}/.git ]" 2>/dev/null; then
        info "Repo already cloned, pulling latest..."
        run_deploy "cd ~/${REMOTE_DIR} && git pull origin main"
    else
        info "Cloning repo..."
        run_deploy "git clone ${REPO_URL} ~/${REMOTE_DIR}"
    fi

    # Copy .env and configs
    info "Copying .env and configs/..."
    rsync -azv -e "ssh $(ssh_opts)" .env "${target}:~/${REMOTE_DIR}/"
    rsync -azv -e "ssh $(ssh_opts)" configs/ "${target}:~/${REMOTE_DIR}/configs/"

    # Create data directories
    run_deploy "mkdir -p ~/${REMOTE_DIR}/data ~/${REMOTE_DIR}/datasets"

    echo ""
    echo "========================================="
    echo " Setup complete!"
    echo "========================================="
    echo ""
    echo " SSH:     ssh ${target}"
    echo " Project: ~/${REMOTE_DIR}"
    echo ""
    echo " Next steps:"
    echo "   1. Copy datasets to the server: rsync -azv datasets/ ${target}:~/${REMOTE_DIR}/datasets/"
    echo "   2. ./scripts/deploy-hcloud.sh deploy"
    echo ""
}

# ==============================================================================
# DEPLOY — pull latest, rebuild, restart
# ==============================================================================

do_deploy() {
    local target
    target="$(deploy_target)"

    info "Deploying to ${target}..."
    run_deploy true 2>/dev/null \
        || error "Cannot SSH to ${target}"

    # Verify .env exists on server
    run_deploy "[ -f ~/${REMOTE_DIR}/.env ]" \
        || error ".env not found on server — run 'setup' first"

    # Pull latest code
    info "Pulling latest from main..."
    run_deploy "cd ~/${REMOTE_DIR} && git fetch origin && git reset --hard origin/main"

    # Sync configs
    rsync -azv -e "ssh $(ssh_opts)" configs/ "${target}:~/${REMOTE_DIR}/configs/"

    local compose
    compose="$(compose_cmd)"

    # Build and restart
    if [ "${1:-}" != "--no-build" ]; then
        info "Building images on server..."
        run_deploy "cd ~/${REMOTE_DIR} && ${compose} build"
    else
        info "Skipping image build (--no-build)"
    fi

    info "Restarting services..."
    run_deploy "cd ~/${REMOTE_DIR} && ${compose} up -d --force-recreate"

    # Prune old images to save disk space
    run_deploy "docker image prune -f" || true

    info "Verifying..."
    run_deploy "cd ~/${REMOTE_DIR} && ${compose} ps"

    echo ""
    info "Deploy complete!"
}

# ==============================================================================
# Main
# ==============================================================================

case "${1:-}" in
    setup)  do_setup ;;
    deploy) do_deploy "${2:-}" ;;
    *)      usage ;;
esac
