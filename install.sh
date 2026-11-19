#!/usr/bin/env bash
# Install (or update) zwhoosh: Zwift Ride / Zwift Click controls for MyWhoosh on macOS.
#
#   curl -fsSL https://raw.githubusercontent.com/hermanvest/zwiftControlsOnMyWhoosh/main/install.sh | bash
#
# or, if you have wget (brew install wget):
#
#   wget -qO- https://raw.githubusercontent.com/hermanvest/zwiftControlsOnMyWhoosh/main/install.sh | bash
#
# Installs uv (Astral's Python tool manager) if missing, then installs the
# `zwhoosh` command into ~/.local/bin. Re-run to update.
# Uninstall with: uv tool uninstall zwhoosh
set -euo pipefail

REPO="${ZWHOOSH_REPO:-hermanvest/zwiftControlsOnMyWhoosh}"
REF="${ZWHOOSH_REF:-main}"
SOURCE="${ZWHOOSH_SOURCE:-https://github.com/${REPO}/archive/refs/heads/${REF}.tar.gz}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "zwhoosh only runs on macOS."

export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (https://docs.astral.sh/uv/)…"
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
  else
    wget -qO- https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
  fi
  command -v uv >/dev/null 2>&1 || die "uv installation failed."
fi

say "Installing zwhoosh from ${SOURCE}…"
uv tool install --force --reinstall --python ">=3.11" "zwhoosh @ ${SOURCE}"

# Make sure ~/.local/bin is on PATH for new shells (no-op if it already is).
uv tool update-shell >/dev/null 2>&1 || true

echo
bold "✅ zwhoosh installed."
echo
echo "Start it from any terminal with:"
echo
bold "    zwhoosh"
echo
echo "(Open a new terminal window first if 'zwhoosh' isn't found yet.)"
echo
echo "First run on this Mac — macOS will ask for two permissions for your terminal app:"
echo "  • Bluetooth      — to talk to the Zwift Ride / Click"
echo "  • Accessibility  — to press MyWhoosh's keys (System Settings → Privacy & Security → Accessibility)"
echo "After granting Accessibility, quit and restart zwhoosh."
echo
echo "Update: re-run this installer.   Uninstall: uv tool uninstall zwhoosh"
