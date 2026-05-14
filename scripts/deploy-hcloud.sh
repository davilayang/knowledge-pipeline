#!/usr/bin/env bash
# Deploy knowledge-pipeline to a Hetzner VPS via Docker Compose.
#
# Usage:
#   ./scripts/deploy-hcloud.sh setup                            # One-time project setup
#   ./scripts/deploy-hcloud.sh deploy                           # Pull main, rebuild & restart
#   ./scripts/deploy-hcloud.sh deploy --no-build                # Restart only (skip image build)
#   ./scripts/deploy-hcloud.sh deploy --branch fix/foo          # Deploy a feature branch (testing)
#   ./scripts/deploy-hcloud.sh deploy --branch fix/foo --no-build
#   ./scripts/deploy-hcloud.sh push-creds                       # Sync rclone.conf to server
#
# Config is loaded from .env.deploy (create from .env.deploy.example).
# Env vars can also be set inline: DEPLOY_TARGET=... ./scripts/deploy-hcloud.sh deploy
#
# Variables:
#   IDENTITY_FILE     Path to SSH private key (required)
#   HETZNER_SERVER or DEPLOY_TARGET    SSH target (required)
#   DEPLOY_USER       Non-root user (default: deploy)
#   DEPLOY_PASSWORD   Sudo password for DEPLOY_USER (required for setup/push-creds
#                     since both chown bind-mount dirs over a non-TTY SSH session)

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

# Run a remote command that needs sudo. Sends DEPLOY_PASSWORD via SSH stdin
# (not via the command string) so it doesn't appear in `ps` output and
# special chars don't need escaping.
run_deploy_sudo() {
    [ -n "${DEPLOY_PASSWORD:-}" ] \
        || error "DEPLOY_PASSWORD must be set in .env.deploy for sudo commands"
    ssh $(ssh_opts) "$(deploy_target)" "sudo -S -p '' $*" <<<"${DEPLOY_PASSWORD}"
}

compose_cmd() {
    # `--profile app` activates the dagster services; postgres + chroma get
    # pulled in automatically via depends_on. Without a profile, nothing runs
    # since every service in compose is profile-gated.
    echo "docker compose --profile app"
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

    # Create runtime directories. They're owned by the deploy user — and since
    # APP_UID/APP_GID in .env make the container's dagster user match the host
    # deploy user, the bind-mounted dirs are writable by both without chown.
    run_deploy "mkdir -p ~/${REMOTE_DIR}/data ~/${REMOTE_DIR}/datasets ~/${REMOTE_DIR}/logs ~/${REMOTE_DIR}/backups"

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
    local target branch="main" no_build=0
    target="$(deploy_target)"

    # Parse flags. Order-independent; supports --branch X, --branch=X, --no-build.
    while [ $# -gt 0 ]; do
        case "$1" in
            --no-build)  no_build=1; shift ;;
            --branch)    [ -n "${2:-}" ] || error "--branch requires a name"
                         branch="$2"; shift 2 ;;
            --branch=*)  branch="${1#*=}"; shift ;;
            "") shift ;;  # tolerate empty positional from old single-arg call sites
            *) error "Unknown deploy flag: $1 (see usage at top of script)" ;;
        esac
    done

    info "Deploying to ${target}..."
    [ "${branch}" != "main" ] && warn "Deploying branch '${branch}', not main — testing build, do not leave running"
    run_deploy true 2>/dev/null \
        || error "Cannot SSH to ${target}"

    # Verify .env exists on server
    run_deploy "[ -f ~/${REMOTE_DIR}/.env ]" \
        || error ".env not found on server — run 'setup' first"

    # Pull requested branch
    info "Pulling latest from ${branch}..."
    run_deploy "cd ~/${REMOTE_DIR} && git fetch origin && git reset --hard origin/${branch}"

    # Sync configs
    rsync -azv -e "ssh $(ssh_opts)" configs/ "${target}:~/${REMOTE_DIR}/configs/"

    local compose
    compose="$(compose_cmd)"

    # Build and restart
    if [ "${no_build}" -eq 0 ]; then
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
# PUSH-CREDS — sync local secrets to the server
# ==============================================================================
#
# Currently handles the rclone Drive credential. Run this once after editing
# `~/.config/rclone/rclone.conf` locally; the file lands at `<project>/.rclone/`
# on the server and is mounted read-only into the dagster-code container at
# /root/.config/rclone (rclone's default search path).

do_push_creds() {
    local target
    target="$(deploy_target)"

    info "Pushing rclone.conf to ${target}..."
    run_deploy true 2>/dev/null \
        || error "Cannot SSH to ${target}"

    local local_rclone="${HOME}/.config/rclone/rclone.conf"
    [ -f "${local_rclone}" ] \
        || error "rclone.conf not found at ${local_rclone} — run 'rclone config' first"

    # sudo chown covers the case where Docker auto-created .rclone/ as root
    # before push-creds ran (compose creates missing bind-mount sources as root).
    run_deploy "mkdir -p ~/${REMOTE_DIR}/.rclone"
    run_deploy_sudo "chown ${DEPLOY_USER}:${DEPLOY_USER} ~/${REMOTE_DIR}/.rclone"
    # rclone.conf is 600 locally (rclone enforces it); -a preserves perms. The
    # container's dagster user has the same uid as the deploy user (via APP_UID
    # in .env), so the file is readable without any extra chown.
    rsync -az -e "ssh $(ssh_opts)" \
        "${local_rclone}" "${target}:~/${REMOTE_DIR}/.rclone/rclone.conf"

    echo ""
    info "rclone.conf pushed."
    echo "    Restart the code container to pick up changes:"
    echo "      ./scripts/deploy-hcloud.sh deploy --no-build"
}

# ==============================================================================
# Main
# ==============================================================================

case "${1:-}" in
    setup)      do_setup ;;
    deploy)     shift; do_deploy "$@" ;;
    push-creds) do_push_creds ;;
    *)          usage ;;
esac
