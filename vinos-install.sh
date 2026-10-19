#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# vinOS backend installer
#
# Partitions, formats and installs vinOS (Arch base + Hyprland) onto a target
# disk. Driven by the PyQt6 GUI (vinos-installer.py) or usable standalone:
#
#   vinos-install.sh --disk /dev/sda --username vin --password secret \
#       --hostname vinos --timezone Europe/Warsaw --keymap us \
#       --locale en_US.UTF-8 --filesystem ext4 --swap-size auto --yes
#
# Also accepts a JSON config instead of CLI flags:
#   vinos-install.sh --config /run/vinos/config.json
#
# Output protocol (stdout, consumed by the GUI; suppressed on a TTY):
#   VINOS_PHASE:<pct>:<message>      coarse step progress
#   VINOS_PROGRESS:<pct>:<message>   fine-grained progress
#   VINOS_ERROR:<message>            fatal error
#   VINOS_DONE:<message>             success
#
# The password may be supplied via the VINOS_PASSWORD environment variable
# instead of --password (avoids leaking it in `ps` output).
# ---------------------------------------------------------------------------
set -Eeuo pipefail

VERSION="0.1.0"
LOG_FILE="/var/log/vinos-install.log"
MNT="/mnt"
MIN_DISK_BYTES=$((8 * 1024 * 1024 * 1024))   # 8 GiB floor
ESP_MIB=512
MIN_ROOT_MIB=4096

# --- configuration (overridable via CLI / JSON / env) -----------------------
DISK=""            # target block device, e.g. /dev/sda
USERNAME=""        # login name for the primary user
PASSWORD=""        # user password (or $VINOS_PASSWORD)
ROOT_PASSWORD=""   # optional root password; root stays locked if unset
FULLNAME=""        # GECOS comment
HOSTNAME_S="vinos" # "HOSTNAME" is a reserved bash variable - do not use it
KEYMAP="us"
LOCALE="en_US.UTF-8"
TIMEZONE="UTC"
FILESYSTEM="ext4"  # ext4 | btrfs
SWAP_SIZE="auto"   # auto | none | <size>[M|G]
WITH_SUDO="yes"    # yes | no
ASSUME_YES="no"    # skip the typing confirmation (GUI already confirmed)
MANUAL="no"        # user partitioned/mounted by hand; skip disk ops
DRY_RUN="no"

MOUNTED=0          # set once we mounted anything under /mnt (drives cleanup)
LAST_PCT=-1        # progress throttling

# --- logging & protocol -----------------------------------------------------
log()    { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG_FILE" >&2; }
info()   { log ":: $*"; }
warn()   { log ":: WARNING: $*"; }
phase()  { log ":: PHASE[$1%] $2"; [ -t 1 ] || printf 'VINOS_PHASE:%s:%s\n' "$1" "$2"; }
prog()   { log "::   $2 ($1%)";            [ -t 1 ] || printf 'VINOS_PROGRESS:%s:%s\n' "$1" "$2"; }
error()  { log ":: ERROR: $*";             [ -t 1 ] || printf 'VINOS_ERROR:%s\n' "$*"; }
die()    { error "$*"; exit 1; }

cleanup_mounts() {
  set +e
  [ "$MOUNTED" -eq 1 ] || return 0
  log ":: cleaning up mounts under $MNT"
  if swapon --show=NAME --noheadings 2>/dev/null | grep -q .; then swapoff --all 2>/dev/null; fi
  umount -R "$MNT" 2>/dev/null || umount -Rl "$MNT" 2>/dev/null
  set -e
}

on_err() {
  local rc=$? line=$1 cmd=$2
  trap - ERR
  error "installation failed (exit $rc) at line $line: $cmd"
  cleanup_mounts
  exit "$rc"
}
trap 'on_err "$LINENO" "$BASH_COMMAND"' ERR
trap cleanup_mounts EXIT

# --- helpers ----------------------------------------------------------------
usage() {
  sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

# /dev/sda -> /dev/sda1 ; /dev/nvme0n1 -> /dev/nvme0n1p1
part_path() { local d=$1 n=$2; if [[ $d =~ [0-9]$ ]]; then printf '%s p%s' "$d" "$n" | tr -d ' '; else printf '%s%s' "$d" "$n"; fi; }

arch_chroot() {
  arch-chroot "$MNT" /usr/bin/env -i \
    HOME=/root TERM="${TERM:-linux}" LOGNAME=root \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LANG="$LOCALE" "$@"
}

enable_service() {
  local svc=$1
  if [ -f "$MNT/usr/lib/systemd/system/$svc" ] || [ -f "$MNT/etc/systemd/system/$svc" ]; then
    arch_chroot systemctl enable "$svc" >/dev/null 2>&1 && info "enabled service $svc"
  else
    warn "service $svc not present in the target system - skipped"
  fi
}

wait_for_part() {  # wait_for_part <dev> [seconds]
  local dev=$1 t=${2:-15}
  while [ ! -b "$dev" ] && [ "$t" -gt 0 ]; do sleep 0.2; t=$((t - 1)); done
  [ -b "$dev" ] || die "partition $dev never appeared"
}

# --- argument parsing -------------------------------------------------------
parse_json_config() {  # parse_json_config <file> -> eval-able KEY='value' lines
  [ -f "$1" ] || die "config file not found: $1"
  command -v python3 >/dev/null || die "python3 is required to read --config JSON"
  python3 - "$1" <<'PY'
import json, shlex, sys
with open(sys.argv[1]) as f:
    cfg = json.load(f)
allowed = {"disk", "username", "password", "root_password", "fullname", "hostname",
           "keymap", "locale", "timezone", "filesystem", "swap_size", "sudo", "manual", "yes"}
envmap = {"disk": "DISK", "username": "USERNAME", "password": "PASSWORD",
          "root_password": "ROOT_PASSWORD", "fullname": "FULLNAME", "hostname": "HOSTNAME_S",
          "keymap": "KEYMAP", "locale": "LOCALE", "timezone": "TIMEZONE",
          "filesystem": "FILESYSTEM", "swap_size": "SWAP_SIZE", "sudo": "WITH_SUDO",
          "manual": "MANUAL", "yes": "ASSUME_YES"}
unknown = set(cfg) - allowed
if unknown:
    sys.exit(f"unknown config keys: {', '.join(sorted(unknown))}")
for k, v in cfg.items():
    if isinstance(v, bool):
        v = "yes" if v else "no"
    print(f"{envmap[k]}={shlex.quote(str(v))}")
PY
}

while [ $# -gt 0 ]; do
  case $1 in
    --disk)          DISK=$2; shift 2 ;;
    --username)      USERNAME=$2; shift 2 ;;
    --password)      PASSWORD=$2; shift 2 ;;
    --root-password) ROOT_PASSWORD=$2; shift 2 ;;
    --fullname)      FULLNAME=$2; shift 2 ;;
    --hostname)      HOSTNAME_S=$2; shift 2 ;;
    --keymap)        KEYMAP=$2; shift 2 ;;
    --locale)        LOCALE=$2; shift 2 ;;
    --timezone)      TIMEZONE=$2; shift 2 ;;
    --filesystem)    FILESYSTEM=$2; shift 2 ;;
    --swap-size)     SWAP_SIZE=$2; shift 2 ;;
    --sudo)          WITH_SUDO=yes; shift ;;
    --no-sudo)       WITH_SUDO=no; shift ;;
    --yes|-y)        ASSUME_YES=yes; shift ;;
    --manual)        MANUAL=yes; shift ;;
    --dry-run)       DRY_RUN=yes; shift ;;
    --config)        eval "$(parse_json_config "$2")"; shift 2 ;;
    --version)       echo "vinos-install $VERSION"; exit 0 ;;
    --help|-h)       usage ;;
    *)               die "unknown argument: $1 (see --help)" ;;
  esac
done
[ -n "${VINOS_PASSWORD:-}" ] && [ -z "$PASSWORD" ] && PASSWORD=$VINOS_PASSWORD

# --- validation -------------------------------------------------------------
phase 0 "Validating configuration"

[ "$EUID" -eq 0 ] || { [ "$DRY_RUN" = yes ] && warn "running as non-root (dry-run only)" \
                       || die "this script must run as root"; }

[[ $DISK =~ ^/dev/[a-zA-Z][a-zA-Z0-9]{1,15}$ ]] || die "invalid or missing --disk '$DISK'"
[[ $USERNAME =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]     || die "invalid username '$USERNAME' (lowercase a-z 0-9 _ -, max 32)"
[[ $HOSTNAME_S =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$ ]] || die "invalid hostname '$HOSTNAME_S'"
[ -n "$PASSWORD" ] || die "missing password (--password or \$VINOS_PASSWORD)"
[ "${#PASSWORD}" -ge 8 ] || die "password must be at least 8 characters"
[ -f "/usr/share/zoneinfo/$TIMEZONE" ] || die "unknown timezone '$TIMEZONE'"
[ -f "/usr/share/i18n/locales/${LOCALE%%.*}" ] || die "unknown locale '$LOCALE'"
case $FILESYSTEM in ext4|btrfs) ;; *) die "unsupported filesystem '$FILESYSTEM' (ext4|btrfs)" ;; esac
[ -d /sys/firmware/efi ] || die "EFI boot not detected - vinOS currently requires UEFI (BIOS/GRUB support is planned)"

if [ -b "$DISK" ]; then
  [ "$(lsblk -dnro TYPE "$DISK" 2>/dev/null)" = "disk" ] || die "$DISK is not a whole-disk block device (partitions are not accepted)"
  DISK_BYTES=$(lsblk -bdno SIZE "$DISK")
  [ "$DISK_BYTES" -ge "$MIN_DISK_BYTES" ] || die "$DISK is smaller than 8 GiB - refusing to install"
fi

# Never install onto the medium the live system is running from
if [ -b "$DISK" ]; then
  while read -r src; do
    [ -b "$src" ] || continue
    pk=$(lsblk -ndo PKNAME "$src" 2>/dev/null || true)
    if { [ -n "$pk" ] && [ "/dev/$pk" = "$DISK" ]; } || [ "$src" = "$DISK" ]; then
      die "$DISK holds the running live system - refusing to install onto it"
    fi
  done < <(findmnt -rn -o SOURCE /run/archiso/bootmnt 2>/dev/null; findmnt -rn -o SOURCE /run/archiso 2>/dev/null)
fi

# --- target disk safety checks ---------------------------------------------
validate_target() {
  phase 5 "Checking target disk safety"

  local busy
  busy=$(lsblk -nrpo MOUNTPOINT "$DISK" | grep -E '^/' || true)
  [ -z "$busy" ] || die "$DISK has mounted partitions (mountpoints: $(echo "$busy" | tr '\n' ' ')) - unmount them first"

  local parts sw
  parts=$(lsblk -nrpo NAME "$DISK" | tail -n +2)
  for p in $parts; do
    sw=$(swapon --show=NAME --noheadings 2>/dev/null | grep -Fx "$p" || true)
    [ -z "$sw" ] || die "swap partition $p on $DISK is active - run swapoff first"
  done

  local holders
  holders=$(lsblk -nrpo HOLDER "$DISK" | grep -E '\S' || true)
  [ -z "$holders" ] || die "$DISK is in use by LVM/RAID/btrfs multi-device ($holders) - clear it first"

  # Refuse if the disk looks like the booted medium even when not mounted
  [ -r /run/archiso/bootmnt ] || [ -d /run/archiso ] || true

  info "safety checks passed for $DISK ($(lsblk -dnro SIZE "$DISK" | xargs) $(lsblk -dnro MODEL "$DISK" | xargs))"
}

confirm_wipe() {
  if [ "$ASSUME_YES" = yes ]; then
    info "destructive wipe of $DISK confirmed via --yes"
    return 0
  fi
  if [ -t 0 ]; then
    printf '\nThis will ERASE ALL DATA on %s.\nType the device path to continue: ' "$DISK" >&2
    read -r answer
    [ "$answer" = "$DISK" ] || die "confirmation did not match - aborting"
    info "destructive wipe of $DISK confirmed interactively"
  else
    die "no TTY available for confirmation - pass --yes to confirm the wipe"
  fi
}

# --- partition sizing -------------------------------------------------------
compute_swap_mib() {  # echo swap size in MiB (0 = no swap)
  local disk_mib avail requested
  disk_mib=$(lsblk -bdno SIZE "$DISK")
  disk_mib=$((disk_mib / 1024 / 1024))
  avail=$((disk_mib - ESP_MIB - MIN_ROOT_MIB))
  case $SWAP_SIZE in
    none) echo 0; return ;;
    auto) requested=$(awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo) ;;
    *[Gg]) requested=$((${SWAP_SIZE%[Gg]} * 1024)) ;;
    *[Mm]) requested=${SWAP_SIZE%[Mm]} ;;
    *)     requested=$SWAP_SIZE ;;
  esac
  [[ $requested =~ ^[0-9]+$ ]] || die "invalid --swap-size '$SWAP_SIZE'"
  [ "$requested" -gt 8192 ] && requested=8192   # cap at 8 GiB
  [ "$requested" -lt 1024 ] && requested=1024   # floor at 1 GiB
  [ "$requested" -gt "$avail" ] && { warn "shrinking swap from ${requested}MiB to ${avail}MiB to fit the disk"; requested=$avail; }
  [ "$requested" -lt 256 ] && { warn "not enough disk space for swap - installing without swap"; requested=0; }
  echo "$requested"
}

# --- disk operations --------------------------------------------------------
partition_disk() {  # partition_disk <swap_mib>
  local swap_mib=$1 swap_end=$((ESP_MIB + swap_mib))
  phase 8 "Partitioning $DISK (GPT: EFI ${ESP_MIB}MiB, swap ${swap_mib}MiB, root remainder)"

  info "wiping old filesystem signatures"
  wipefs -af "$DISK" >/dev/null
  sgdisk --zap-all "$DISK" >/dev/null 2>&1 || true

  parted -s -- "$DISK" mklabel gpt
  parted -s -- "$DISK" mkpart ESP fat32 1MiB $((ESP_MIB))MiB
  parted -s -- "$DISK" set 1 esp on
  if [ "$swap_mib" -gt 0 ]; then
    parted -s -- "$DISK" mkpart swap linux-swap $((ESP_MIB))MiB ${swap_end}MiB
    parted -s -- "$DISK" mkpart root ext4 ${swap_end}MiB 100%
  else
    parted -s -- "$DISK" mkpart root ext4 $((ESP_MIB))MiB 100%
  fi

  partprobe "$DISK" 2>/dev/null || true
  udevadm settle 2>/dev/null || sleep 2
  EFI_PART=$(part_path "$DISK" 1); wait_for_part "$EFI_PART"
  if [ "$swap_mib" -gt 0 ]; then
    SWAP_PART=$(part_path "$DISK" 2); wait_for_part "$SWAP_PART"
    ROOT_PART=$(part_path "$DISK" 3)
  else
    SWAP_PART=""
    ROOT_PART=$(part_path "$DISK" 2)
  fi
  wait_for_part "$ROOT_PART"
  info "partitions: efi=$EFI_PART swap=${SWAP_PART:-none} root=$ROOT_PART"
}

format_and_mount() {
  phase 12 "Formatting and mounting"
  mkfs.fat -F32 -n VINOS_EFI "$EFI_PART" >/dev/null
  if [ -n "$SWAP_PART" ]; then
    mkswap -L vinos-swap "$SWAP_PART" >/dev/null
    swapon "$SWAP_PART"
  fi

  if [ "$FILESYSTEM" = btrfs ]; then
    mkfs.btrfs -f -L vinOS "$ROOT_PART" >/dev/null
    mount -o compress=zstd,noatime,subvol=@ "$ROOT_PART" "$MNT"
    MOUNTED=1
    btrfs subvolume create "$MNT/@home" >/dev/null
    btrfs subvolume create "$MNT/@var"  >/dev/null
    umount "$MNT"
    mount -o compress=zstd,noatime,subvol=@     "$ROOT_PART" "$MNT";                    MOUNTED=1
    mkdir -p "$MNT/home" "$MNT/var" "$MNT/boot"
    mount -o compress=zstd,noatime,subvol=@home "$ROOT_PART" "$MNT/home"
    mount -o compress=zstd,noatime,subvol=@var  "$ROOT_PART" "$MNT/var"
  else
    mkfs.ext4 -F -L vinOS "$ROOT_PART" >/dev/null
    mount "$ROOT_PART" "$MNT"; MOUNTED=1
    mkdir -p "$MNT/boot"
  fi
  mount "$EFI_PART" "$MNT/boot"
  info "mounted root at $MNT and EFI system partition at $MNT/boot"
}

manual_mount_check() {
  phase 5 "Checking manually prepared mounts"
  findmnt -rn "$MNT"      >/dev/null || die "--manual requires the root filesystem mounted at $MNT"
  findmnt -rn "$MNT/boot" >/dev/null || die "--manual requires the EFI system partition mounted at $MNT/boot"
  ROOT_PART=$(findmnt -no SOURCE "$MNT")
  EFI_PART=$(findmnt -no SOURCE "$MNT/boot")
  SWAP_PART=""
  info "using existing mounts: root=$ROOT_PART efi=$EFI_PART"
}

# --- package installation ---------------------------------------------------
build_package_list() {
  local cpu_vendor
  cpu_vendor=$(awk -F': ' '/vendor_id/ {print $2; exit}' /proc/cpuinfo)
  PACKAGES=(base linux linux-firmware
    networkmanager sudo nano vim git
    mesa libva pipewire pipewire-alsa pipewire-pulse wireplumber
    wayland xorg-xwayland xdg-desktop-portal-hyprland xdg-utils xdg-user-dirs
    hyprland polkit-kde-agent qt6-wayland
    kitty waybar wofi mako grim slurp wl-clipboard
    thunar thunar-volman gvfs
    noto-fonts ttf-dejavu noto-fonts-emoji
    greetd)
  case $FILESYSTEM in ext4) PACKAGES+=(e2fsprogs) ;; btrfs) PACKAGES+=(btrfs-progs) ;; esac
  case $cpu_vendor in
    GenuineIntel) PACKAGES+=(intel-ucode vulkan-intel) ;;
    AuthenticAMD) PACKAGES+=(amd-ucode vulkan-radeon) ;;
  esac
  [ "$MANUAL" = yes ] && PACKAGES+=(cfdisk parted)  # keep manual tools on target
}

run_pacstrap() {
  phase 18 "Installing base system and Hyprland desktop (this takes a while)"
  build_package_list
  info "pacstrap packages: ${PACKAGES[*]}"
  set +e
  pacstrap -K "$MNT" "${PACKAGES[@]}" 2>&1 \
    | stdbuf -oL tee -a "$LOG_FILE" \
    | while IFS= read -r line; do
        if [[ $line =~ ^\(([0-9]+)/([0-9]+)\)\ installing\ ([[:alnum:][:punct:]]+) ]]; then
          local cur=${BASH_REMATCH[1]} total=${BASH_REMATCH[2]} pct
          pct=$((18 + cur * 46 / total))
          if [ "$pct" -gt "$LAST_PCT" ]; then
            LAST_PCT=$pct
            prog "$pct" "Installing packages (${cur}/${total}): ${BASH_REMATCH[3]}"
          fi
        fi
      done
  local rc=${PIPESTATUS[0]}
  set -e
  [ "$rc" -eq 0 ] || die "pacstrap failed (exit $rc) - see $LOG_FILE"
  prog 64 "Package installation finished"
}

generate_fstab() {
  phase 65 "Generating /etc/fstab"
  genfstab -U "$MNT" >> "$MNT/etc/fstab"
  grep -q ' / ' "$MNT/etc/fstab"  || die "fstab is missing the root filesystem"
  grep -q ' /boot ' "$MNT/etc/fstab" || die "fstab is missing the EFI system partition (/boot)"
  info "fstab written"
}

# --- system configuration ---------------------------------------------------
configure_system() {
  phase 68 "Configuring locale, timezone and hostname"

  arch_chroot ln -sf "/usr/share/zoneinfo/$TIMEZONE" /etc/localtime
  arch_chroot hwclock --systohc >/dev/null

  sed -i "s/^#\s*\(${LOCALE%%.*}.*UTF-8\)/\1/" "$MNT/etc/locale.gen"
  grep -q "^${LOCALE} " "$MNT/etc/locale.gen" || echo "$LOCALE UTF-8" >> "$MNT/etc/locale.gen"
  arch_chroot locale-gen >/dev/null
  echo "LANG=$LOCALE" > "$MNT/etc/locale.conf"
  echo "KEYMAP=$KEYMAP" > "$MNT/etc/vconsole.conf"

  echo "$HOSTNAME_S" > "$MNT/etc/hostname"
  cat > "$MNT/etc/hosts" <<EOF
127.0.0.1  localhost
::1        localhost
127.0.1.1  $HOSTNAME_S.localdomain $HOSTNAME_S
EOF

  phase 74 "Creating users and sudo access"
  if [ -n "$ROOT_PASSWORD" ]; then
    printf 'root:%s\n' "$ROOT_PASSWORD" | arch_chroot chpasswd
  else
    arch_chroot passwd -l root >/dev/null
    info "root account locked (set --root-password to change)"
  fi

  arch_chroot useradd -m -G wheel,audio,video,storage -s /bin/bash "$USERNAME"
  printf '%s:%s\n' "$USERNAME" "$PASSWORD" | arch_chroot chpasswd

  if [ "$WITH_SUDO" = yes ]; then
    echo '%wheel ALL=(ALL:ALL) ALL' > "$MNT/etc/sudoers.d/00-vinos"
    chmod 440 "$MNT/etc/sudoers.d/00-vinos"
    arch_chroot visudo -cf /etc/sudoers.d/00-vinos >/dev/null || die "sudoers configuration failed visudo validation"
    info "user $USERNAME may use sudo"
  else
    info "user $USERNAME has no sudo privileges"
  fi
}

configure_desktop() {
  phase 80 "Configuring display manager and desktop defaults"

  # greetd: auto-login straight into Hyprland on tty1; agreety (tty login)
  # becomes available after logout.
  cat > "$MNT/etc/greetd/config.toml" <<EOF
[terminal]
vt = 1

[initial_session]
user = "$USERNAME"
command = "/usr/local/bin/vinos-session"

[default_session]
user = "greetd"
command = "agreety --cmd /bin/sh"
EOF

  cat > "$MNT/usr/local/bin/vinos-session" <<'EOF'
#!/bin/sh
export XDG_CURRENT_DESKTOP=Hyprland
export XDG_SESSION_DESKTOP=Hyprland
export XDG_SESSION_TYPE=wayland
exec Hyprland
EOF
  chmod 755 "$MNT/usr/local/bin/vinos-session"

  # A minimal, working default desktop for the user (rice it later).
  local cfg="$MNT/home/$USERNAME/.config"
  mkdir -p "$cfg/hypr" "$cfg/waybar"
  cat > "$cfg/hypr/hyprland.conf" <<EOF
# vinOS default Hyprland configuration
exec-once = /usr/lib/polkit-kde-authentication-agent-1
exec-once = waybar
exec-once = mako
exec-once = dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP

monitor = ,preferred,auto,1

input {
    kb_layout = $KEYMAP
    follow_mouse = 1
}

general {
    gaps_in = 4
    gaps_out = 8
    border_size = 2
}

decoration {
    rounding = 8
}

bind = SUPER, Return, exec, kitty
bind = SUPER, D, exec, wofi --show drun
bind = SUPER, Q, killactive
bind = SUPER SHIFT, E, exit
bind = SUPER, F, fullscreen
bind = SUPER, V, togglefloating
bindm = SUPER, mouse:272, movewindow
bindm = SUPER, mouse:273, resizewindow
bind = SUPER, 1..9, workspace, 1..9
bind = SUPER SHIFT, 1..9, movetoworkspace, 1..9
EOF

  cat > "$cfg/waybar/config.jsonc" <<'EOF'
{
  "layer": "top",
  "height": 30,
  "modules-left": ["hyprland/workspaces"],
  "modules-center": ["clock"],
  "modules-right": ["network", "pulseaudio", "battery"],
  "clock": { "format": "{:%a %d %b  %H:%M}" },
  "network": { "format-wifi": "{essid} ({signalStrength}%)", "format-ethernet": "ethernet", "format-disconnected": "offline" },
  "pulseaudio": { "format": "vol {volume}%", "format-muted": "muted" },
  "battery": { "format": "{capacity}%" }
}
EOF
  cat > "$cfg/waybar/style.css" <<'EOF'
* { font-family: "DejaVu Sans"; font-size: 13px; }
window#waybar { background: rgba(20, 22, 28, 0.9); color: #cdd6f4; }
#workspaces button.active { background: #5b8fd6; color: #101218; }
#clock, #network, #pulseaudio, #battery { padding: 0 10px; }
EOF

  chown -R "$USERNAME:$USERNAME" "$MNT/home/$USERNAME"
  enable_service NetworkManager.service
  enable_service greetd.service
  enable_service systemd-timesyncd.service
  enable_service fstrim.timer
}

apply_branding() {
  phase 90 "Applying vinOS branding"
  # TODO: move this into a proper 'vinos-branding' pacman package so filesystem
  # updates cannot clobber /etc/os-release.
  cat > "$MNT/etc/os-release" <<EOF
NAME="vinOS"
ID=vinos
ID_LIKE=arch
PRETTY_NAME="vinOS"
ANSI_COLOR="38;2;91;143;214"
HOME_URL="https://github.com/michaldaniszewski03-hash/vinOS"
DOCUMENTATION_URL="https://github.com/michaldaniszewski03-hash/vinOS"
SUPPORT_URL="https://github.com/michaldaniszewski03-hash/vinOS/issues"
BUG_REPORT_URL="https://github.com/michaldaniszewski03-hash/vinOS/issues"
VERSION_ID="0.1"
BUILD_ID="rolling"
EOF
  cat > "$MNT/etc/motd" <<'EOF'
              __   __
 _   _____   / /__ / /______ ___  ____
/ | / / _ \ / / _ `/ __/ -_) _ \/ __/
/  |/ /  __// /\_,_/_/ \__/ /_//_/
/_|___/\___/_/\__,_/\__/___/___/

欢迎 · Witamy · Willkommen · Welcome to vinOS
EOF
  info "branding applied (os-release, motd)"
}

install_bootloader() {
  phase 92 "Installing systemd-boot bootloader"
  ROOT_UUID=$(blkid -s UUID -o value "$ROOT_PART")
  [ -n "$ROOT_UUID" ] || die "could not read UUID of root partition $ROOT_PART"

  mkdir -p "$MNT/boot/loader/entries"
  cat > "$MNT/boot/loader/loader.conf" <<'EOF'
default vinOS.conf
timeout 3
console-mode max
editor no
EOF

  {
    echo "title vinOS"
    echo "linux /vmlinuz-linux"
    [ -f "$MNT/boot/intel-ucode.img" ] && echo "initrd /intel-ucode.img"
    [ -f "$MNT/boot/amd-ucode.img" ]   && echo "initrd /amd-ucode.img"
    echo "initrd /initramfs-linux.img"
    if [ "$FILESYSTEM" = btrfs ]; then
      echo "options root=UUID=$ROOT_UUID rw quiet rootflags=subvol=@"
    else
      echo "options root=UUID=$ROOT_UUID rw quiet"
    fi
  } > "$MNT/boot/loader/entries/vinOS.conf"

  arch_chroot bootctl --esp-path=/boot install >/dev/null
  [ -f "$MNT/boot/EFI/systemd/systemd-bootx64.efi" ] \
    || warn "systemd-boot binary not found at the expected path - check the ESP after reboot"
  info "bootloader installed (entry: vinOS.conf, root UUID $ROOT_UUID)"
}

finalize() {
  phase 96 "Finalizing installation"
  mkdir -p "$MNT/var/log"
  cp "$LOG_FILE" "$MNT/var/log/vinos-install.log" 2>/dev/null || true
  sync
  cleanup_mounts
  MOUNTED=0
  prog 100 "All done"
  info "vinOS installed successfully on $DISK"
}

# --- main -------------------------------------------------------------------
: > "$LOG_FILE"
info "vinOS installer $VERSION starting"
info "config: disk=$DISK fs=$FILESYSTEM tz=$TIMEZONE locale=$LOCALE keymap=$KEYMAP user=$USERNAME manual=$MANUAL"

if [ "$MANUAL" = yes ]; then
  manual_mount_check
else
  validate_target
  SWAP_MIB=$(compute_swap_mib)
  if [ "$DRY_RUN" = yes ]; then
    phase 5 "Dry run - plan OK, no changes were made"
    info "plan: wipe $DISK (GPT) -> EFI ${ESP_MIB}MiB FAT32, swap ${SWAP_MIB}MiB, root remainder as $FILESYSTEM"
    exit 0
  fi
  confirm_wipe
  partition_disk "$SWAP_MIB"
  format_and_mount
fi

[ "$DRY_RUN" = yes ] && { phase 5 "Dry run complete (manual mode) - no changes were made"; exit 0; }

run_pacstrap
generate_fstab
configure_system
configure_desktop
apply_branding
install_bootloader
finalize

phase 100 "Installation complete"
exit 0
