#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# vinOS bootstrap
#
# Run on a live Arch ISO as root:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/michaldaniszewski03-hash/vinos/main/install.sh)"
#
# It installs the minimal dependencies the GUI installer needs (Python 3,
# PyQt6, Xorg, parted, ...), downloads the installer payload and launches the
# PyQt6 GUI. Everything is logged to /var/log/vinos-bootstrap.log.
# ---------------------------------------------------------------------------
set -Eeuo pipefail

REPO_RAW="https://raw.githubusercontent.com/michaldaniszewski03-hash/vinos/main"
WORKDIR="/run/vinos"
LOG_FILE="/var/log/vinos-bootstrap.log"

# Dependencies for the live session (NOT the installed system):
#   python / python-pyqt6 : the GUI
#   xorg-* / mesa         : display server for the GUI (stock Arch ISO has none)
#   ttf-dejavu            : a font, otherwise Qt renders tofu
#   parted / gptfdisk / dosfstools / e2fsprogs / btrfs-progs / arch-install-scripts : backend tools
PKGS=(python python-pyqt6 xorg-xinit xorg-server mesa libglvnd ttf-dejavu
      parted gptfdisk dosfstools e2fsprogs btrfs-progs arch-install-scripts)

log()  { printf '[%(%Y-%m-%dT%H:%M:%S)T] %s\n' -1 "$*" | tee -a "$LOG_FILE" >&2; }
die()  { log "ERROR: $*"; log "Bootstrap failed. Log saved to $LOG_FILE"; exit 1; }

trap 'die "bootstrap aborted at line $LINENO (exit $?)"' ERR

# --- preflight checks -------------------------------------------------------
[ "$EUID" -eq 0 ] || die "this installer must run as root (the live ISO logs you in as root)"
[ -f /etc/arch-release ] || die "this does not look like an Arch Linux live environment"

log "vinOS bootstrap starting"

# Network connectivity (the live ISO needs internet for pacman and pacstrap)
log "checking network connectivity"
curl -fsSL --connect-timeout 10 -o /dev/null https://archlinux.org \
  || die "no internet connectivity (or archlinux.org is unreachable)"

# Never run against an already-installed system mounted at /mnt
if findmnt -rn /mnt >/dev/null 2>&1; then
  die "/mnt is already a mountpoint - unmount it before running the installer"
fi

# --- install live-session dependencies --------------------------------------
log "installing dependencies: ${PKGS[*]}"
# -Sy (sync only, no full upgrade) is intentional: refreshing the sync DB is
# required to resolve the packages above, and a full -Syu of the live session
# would download hundreds of MB for no benefit.
pacman -Sy --noconfirm --needed "${PKGS[@]}" \
  || die "pacman failed to install dependencies (is another pacman running?)"

command -v python3 >/dev/null || die "python3 missing after dependency installation"
command -v startx  >/dev/null || die "xorg-xinit missing after dependency installation"

# --- fetch installer payload ------------------------------------------------
# Prefer files baked into a prebuilt vinOS ISO (or a local checkout) over the
# network so the installer works offline and ISOs are self-contained.
LOCAL_PAYLOAD="${VINOS_LOCAL_PAYLOAD:-/root/vinos}"
mkdir -p "$WORKDIR"
fetch() {
  if [ -f "$LOCAL_PAYLOAD/$1" ]; then
    log "using local copy of $1 from $LOCAL_PAYLOAD"
    cp "$LOCAL_PAYLOAD/$1" "$WORKDIR/$1"
  else
    log "downloading $1 from $REPO_RAW"
    curl -fsSL --retry 3 --connect-timeout 10 "$REPO_RAW/$1" -o "$WORKDIR/$1" \
      || die "failed to download $1"
  fi
  chmod +x "$WORKDIR/$1"
}
fetch vinos-install.sh
fetch vinos-installer.py

# --- launch the GUI ---------------------------------------------------------
log "launching vinOS installer GUI"

if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
  # Already inside a graphical session - run the GUI directly.
  exec python3 "$WORKDIR/vinos-installer.py"
else
  # Stock Arch ISO is a TTY: boot a minimal X session that runs the GUI.
  cat > /root/.xinitrc <<'XINITRC'
#!/bin/sh
exec python3 /run/vinos/vinos-installer.py
XINITRC
  chmod +x /root/.xinitrc
  startx
fi

log "installer GUI exited"
