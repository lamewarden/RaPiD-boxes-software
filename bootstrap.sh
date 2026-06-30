#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Please run as root (recommended: curl ... | sudo bash)"
  exit 1
fi

REPO_URL="${RAPIDBOXES_REPO_URL:-https://github.com/lamewarden/RaPiD-boxes-software.git}"
BRANCH="${RAPIDBOXES_BRANCH:-main}"
APP_DIR="${RAPIDBOXES_APP_DIR:-/opt/rapidboxes}"
APP_USER="${RAPIDBOXES_APP_USER:-${SUDO_USER:-pi}}"

if [[ -d "${APP_DIR}/.git" ]]; then
  git -C "${APP_DIR}" fetch origin
  git -C "${APP_DIR}" checkout "${BRANCH}"
  git -C "${APP_DIR}" pull --ff-only origin "${BRANCH}"
else
  rm -rf "${APP_DIR}"
  git clone --depth 1 --branch "${BRANCH}" "${REPO_URL}" "${APP_DIR}"
fi

export APP_DIR APP_USER
exec "${APP_DIR}/deploy/install.sh"
