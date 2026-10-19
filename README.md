# vinOS

**vinOS** is a modern, Arch-based Linux distribution with a slick PyQt6 graphical
installer and a Hyprland desktop environment. It boots from a stock Arch ISO (or
the prebuilt vinOS ISO) and installs itself in a few clicks.

```
bash -c "$(curl -fsSL https://raw.githubusercontent.com/michaldaniszewski03-hash/vinos/main/install.sh)"
```

## How it works

```
live Arch ISO ──▶ install.sh (bootstrap)
                    │  checks: root, Arch live env, network
                    │  installs: python, python-pyqt6, xorg, parted, pacstrap tools
                    │  fetches: vinos-install.sh + vinos-installer.py (local copy on the vinOS ISO)
                    ▼
              vinos-installer.py (PyQt6 GUI, 4 pages)
                    │  1. language / keyboard layout / timezone
                    │  2. username / password / hostname / sudo
                    │  3. disk selection (erase + auto-partition, or manual)
                    │  4. progress bar + live log
                    ▼  (CLI args + VINOS_PASSWORD env var)
              vinos-install.sh (backend, runs as root)
                   safety checks → GPT/EFI partitioning → pacstrap → fstab →
                   locale/users/services → systemd-boot → branding → unmount
```

## Repository layout

| File                  | Purpose                                                        |
|-----------------------|----------------------------------------------------------------|
| `install.sh`          | Live-ISO bootstrap one-liner; dependency install + GUI launch  |
| `vinos-installer.py`  | PyQt6 graphical installer (the wizard)                         |
| `vinos-install.sh`    | Backend: partitions, pacstraps, configures the target system   |
| `.github/workflows/`  | CI: builds the bootable ISO and attaches it to releases        |
| `ci/build-iso.sh`     | Container-side ISO build (archiso `releng` + installer baked in) |

## Disk safety

The installer is deliberately paranoid, in both the GUI and the backend
(the backend never trusts the GUI):

- refuses disks smaller than 8 GiB, partition devices, and anything that is not
  a whole-disk block device;
- refuses the medium the live system is running from (`/run/archiso`);
- refuses disks with mounted partitions, active swap, or LVM/RAID/btrfs holders;
- requires typing the exact device path before wiping (the GUI shows a
  confirmation dialog; the CLI prompts on a TTY unless `--yes` is passed);
- a cleanup trap unmounts and swaps off on any failure, and every run is logged
  to `/var/log/vinos-install.log` (also copied into the installed system).

## Backend protocol

The backend prints machine-readable progress on stdout (suppressed on a TTY):

```
VINOS_PHASE:<pct>:<message>
VINOS_PROGRESS:<pct>:<message>
VINOS_ERROR:<message>
VINOS_DONE:<message>
```

It also accepts a JSON config instead of flags:
`vinos-install.sh --config config.json`. For testing, `--dry-run` runs every
safety check and prints the partitioning plan without touching the disk.

## Building the ISO

CI (`.github/workflows/build-iso.yml`) builds a bootable vinOS ISO with
[archiso] on every push, and attaches the `.iso` + SHA-256 checksum to a
GitHub Release whenever a `v*` tag is pushed. The ISO is the stock Arch `releng`
profile with the installer baked in: root autologin on tty1 starts the GUI
automatically, offline (payload files are read from the ISO, not downloaded).

Locally (on Arch): `mkarchiso -v -w work -o out` on a profile derived from
`/usr/share/archiso/configs/releng` with the three scripts copied to
`airootfs/root/vinos/`.

## Roadmap

- [ ] MBR/BIOS + GRUB support (currently UEFI + systemd-boot only)
- [ ] Full disk encryption (LUKS) option
- [ ] `vinos-branding` pacman package (os-release, wallpaper, dotfiles) instead
      of overwriting files in place
- [ ] greetd greeter (gtkgreet) with user picker instead of pure autologin
- [ ] Partition editor inside the GUI (manual mode currently means `cfdisk`)
- [ ] Localization for the console (`vconsole.conf`) and installer screenshots

[archiso]: https://wiki.archlinux.org/title/Archiso
