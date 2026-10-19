#!/usr/bin/env bash
# Runs INSIDE an archlinux container (see .github/workflows/build-iso.yml):
# builds a bootable vinOS ISO from the stock archiso `releng` profile with the
# installer baked in. Expects the repository mounted at /src (also the cwd).
set -euo pipefail

command -v pacman >/dev/null || { echo "this script must run in an Arch container" >&2; exit 1; }

pacman -Syu --noconfirm --needed archiso
cp -r /usr/share/archiso/configs/releng profile

# Bake the installer into the ISO: releng autologins root on tty1, .zprofile
# starts the installer, and install.sh reads the local payload from
# /root/vinos instead of downloading it.
mkdir -p profile/airootfs/root/vinos
cp install.sh vinos-install.sh vinos-installer.py profile/airootfs/root/vinos/

printf '%s\n' '[ -z "$DISPLAY" ] && [ -f /root/vinos/install.sh ] && bash /root/vinos/install.sh' \
  >> profile/airootfs/root/.zprofile

# Ship the installer's live-session dependencies so the GUI starts instantly.
printf '%s\n' \
  python python-pyqt6 xorg-xinit xorg-server mesa libglvnd ttf-dejavu \
  parted gptfdisk dosfstools e2fsprogs btrfs-progs arch-install-scripts \
  >> profile/packages.x86_64

mkarchiso -v -w work -o out profile

# Checksum is written here, as root, on purpose: mkarchiso creates out/ owned by
# root:root (mode 755), so a later step running as the unprivileged CI user
# cannot create files inside it ("vinos-iso.sha256: Permission denied").
cd out
sha256sum -- *.iso > vinos-iso.sha256
cat vinos-iso.sha256
cd ..
ls -l out

# Hand the artifacts back to the user that owns the bind mount (the workflow
# passes the runner's ids as HOST_UID/HOST_GID), so follow-up steps can write
# into out/. If the chown is not possible (e.g. running by hand outside Docker),
# fall back to a world-readable tree with a writable out/ directory.
if [ -n "${HOST_UID:-}" ] && [ -n "${HOST_GID:-}" ] \
   && chown "$HOST_UID:$HOST_GID" out out/* 2>/dev/null; then
  :
else
  chmod -R a+rX out
  chmod a+w out
fi
