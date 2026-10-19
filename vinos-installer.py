#!/usr/bin/env python3
"""vinOS graphical installer.

A four-page PyQt6 wizard that collects installation settings and drives the
backend script (vinos-install.sh):

  1. Language / keyboard layout / timezone
  2. User account (name, password, hostname, sudo)
  3. Disk selection (erase & auto-partition, or manual partitioning)
  4. Progress and log while the backend runs

Run as root on the live Arch ISO (install.sh takes care of that).
"""

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time

from PyQt6.QtCore import Qt, QProcess, QProcessEnvironment, QTimer
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QSizePolicy, QSpinBox,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(SCRIPT_DIR, "vinos-install.sh")

# --------------------------------------------------------------------------
# Translations (a small i18n table; keys are stable across languages)
# --------------------------------------------------------------------------
STRINGS = {
    "en": {
        "app_title": "vinOS Installer",
        "next": "Next", "back": "Back", "cancel": "Cancel",
        "page1_title": "Welcome to vinOS",
        "page1_sub": "Choose your language, keyboard layout and timezone.",
        "language": "Language", "keymap": "Keyboard layout", "timezone": "Timezone",
        "page2_title": "Create your user",
        "page2_sub": "Set up the account that will log in to the Hyprland desktop.",
        "fullname": "Full name (optional)", "username": "Username",
        "password": "Password", "password2": "Confirm password",
        "hostname": "Hostname", "sudo": "Give this user sudo (administrator) privileges",
        "err_pw_short": "Password must be at least 8 characters.",
        "err_pw_mismatch": "Passwords do not match.",
        "err_username": "Username must be lowercase letters, digits, '-' or '_', and start with a letter or '_'.",
        "err_hostname": "Hostname must be alphanumeric (dashes allowed, not at the ends).",
        "page3_title": "Where should vinOS be installed?",
        "page3_sub": "Select the target disk. ALL DATA ON IT WILL BE DESTROYED.",
        "mode_erase": "Erase disk and install vinOS (guided partitioning)",
        "mode_manual": "Manual partitioning (advanced)",
        "manual_hint": ("Manual mode: partition the disk yourself (e.g. with cfdisk), "
                        "format an EFI system partition as FAT32 and mount everything under /mnt "
                        "(root at /mnt, ESP at /mnt/boot), then continue. The installer skips "
                        "partitioning and only installs the system."),
        "filesystem": "Root filesystem", "swap": "Swap",
        "refresh": "Refresh disk list",
        "col_device": "Device", "col_size": "Size", "col_model": "Model",
        "col_transport": "Transport", "col_mount": "Mountpoints",
        "live_disk": "Live system disk – cannot be selected",
        "no_disks": "No disks found. Is this machine using NVMe/virtio?",
        "err_pick_disk": "Please select a target disk first.",
        "err_manual_mount": "Manual mode requires a root filesystem mounted at /mnt and the ESP at /mnt/boot.",
        "swap_auto": "Automatic (based on RAM)", "swap_none": "No swap",
        "swap_custom": "Custom size (MiB):",
        "confirm_title": "Confirm destructive installation",
        "confirm_text": ("This will permanently erase EVERYTHING on {disk}.\n\n"
                         "Type the device path below to confirm:"),
        "err_confirm": "The typed path does not match the selected disk.",
        "page4_title": "Installing vinOS",
        "page4_sub": "Do not power off this machine.",
        "installing": "Installing…", "done": "Installation finished successfully.",
        "reboot_now": "Reboot now", "close": "Close",
        "log_label": "Installation log",
        "elapsed": "Elapsed: {t}",
        "err_backend_missing": "Backend script not found:\n{path}",
        "err_not_root": "This installer must run as root.\nRun install.sh on the live Arch ISO.",
        "err_install_failed": "Installation failed:\n{msg}\n\nSee the log above.",
    },
    "pl": {
        "app_title": "Instalator vinOS",
        "next": "Dalej", "back": "Wstecz", "cancel": "Anuluj",
        "page1_title": "Witamy w vinOS",
        "page1_sub": "Wybierz język, układ klawiatury i strefę czasową.",
        "language": "Język", "keymap": "Układ klawiatury", "timezone": "Strefa czasowa",
        "page2_title": "Utwórz użytkownika",
        "page2_sub": "Skonfiguruj konto do logowania w środowisku Hyprland.",
        "fullname": "Imię i nazwisko (opcjonalnie)", "username": "Nazwa użytkownika",
        "password": "Hasło", "password2": "Powtórz hasło",
        "hostname": "Nazwa komputera", "sudo": "Nadaj temu użytkownikowi uprawnienia sudo (administrator)",
        "err_pw_short": "Hasło musi mieć co najmniej 8 znaków.",
        "err_pw_mismatch": "Hasła nie są zgodne.",
        "err_username": "Nazwa użytkownika: małe litery, cyfry, '-' i '_', rozpoczyna się od litery lub '_'.",
        "err_hostname": "Nazwa komputera może zawierać litery i cyfry (myślniki nie na początku/końcu).",
        "page3_title": "Gdzie zainstalować vinOS?",
        "page3_sub": "Wybierz dysk docelowy. WSZYSTKIE DANE ZOSTANĄ SKASOWANE.",
        "mode_erase": "Wymaż dysk i zainstaluj vinOS (partycjonowanie automatyczne)",
        "mode_manual": "Partycjonowanie ręczne (zaawansowane)",
        "manual_hint": ("Tryb ręczny: partycjonuj dysk samodzielnie (np. cfdisk), sformatuj "
                        "partycję EFI jako FAT32 i zamontuj wszystko pod /mnt (root w /mnt, "
                        "ESP w /mnt/boot), a następnie kontynuuj. Instalator pominie partycjonowanie."),
        "filesystem": "System plików główny", "swap": "Partycja swap",
        "refresh": "Odśwież listę dysków",
        "col_device": "Urządzenie", "col_size": "Rozmiar", "col_model": "Model",
        "col_transport": "Magistrala", "col_mount": "Punkty montowania",
        "live_disk": "Dysk systemu live – nie można wybrać",
        "no_disks": "Nie znaleziono dysków.",
        "err_pick_disk": "Najpierw wybierz dysk docelowy.",
        "err_manual_mount": "Tryb ręczny wymaga systemu w /mnt oraz ESP w /mnt/boot.",
        "swap_auto": "Automatycznie (zależnie od RAM)", "swap_none": "Brak swap",
        "swap_custom": "Rozmiar własny (MiB):",
        "confirm_title": "Potwierdź destrukcyjną instalację",
        "confirm_text": ("To TRWALE skasuje WSZYSTKO na {disk}.\n\n"
                         "Wpisz poniżej ścieżkę urządzenia, aby potwierdzić:"),
        "err_confirm": "Wpisana ścieżka nie zgadza się z wybranym dyskiem.",
        "page4_title": "Instalowanie vinOS",
        "page4_sub": "Nie wyłączaj tego komputera.",
        "installing": "Instalowanie…", "done": "Instalacja zakończona pomyślnie.",
        "reboot_now": "Uruchom ponownie", "close": "Zamknij",
        "log_label": "Dziennik instalacji",
        "elapsed": "Czas: {t}",
        "err_backend_missing": "Nie znaleziono skryptu instalującego:\n{path}",
        "err_not_root": "Ten instalator musi być uruchomiony jako root.\nUruchom install.sh na live ISO Arch.",
        "err_install_failed": "Instalacja nie powiodła się:\n{msg}\n\nZobacz dziennik powyżej.",
    },
    "de": {
        "app_title": "vinOS-Installer",
        "next": "Weiter", "back": "Zurück", "cancel": "Abbrechen",
        "page1_title": "Willkommen bei vinOS",
        "page1_sub": "Sprache, Tastaturlayout und Zeitzone wählen.",
        "language": "Sprache", "keymap": "Tastaturlayout", "timezone": "Zeitzone",
        "page2_title": "Benutzer anlegen",
        "page2_sub": "Konto für die Hyprland-Anmeldung einrichten.",
        "fullname": "Vollständiger Name (optional)", "username": "Benutzername",
        "password": "Passwort", "password2": "Passwort bestätigen",
        "hostname": "Hostname", "sudo": "Diesem Benutzer sudo-Rechte (Administrator) geben",
        "err_pw_short": "Das Passwort muss mindestens 8 Zeichen lang sein.",
        "err_pw_mismatch": "Die Passwörter stimmen nicht überein.",
        "err_username": "Benutzername: kleine Buchstaben, Ziffern, '-' oder '_', Buchstabe oder '_' am Anfang.",
        "err_hostname": "Hostname: alphanumerisch (Bindestriche nicht am Anfang/Ende).",
        "page3_title": "Wo soll vinOS installiert werden?",
        "page3_sub": "Zielplatte wählen. ALLE DATEN WERDEN GELÖSCHT.",
        "mode_erase": "Platte löschen und vinOS installieren (automatische Partitionierung)",
        "mode_manual": "Manuelle Partitionierung (fortgeschritten)",
        "manual_hint": ("Manueller Modus: Partitionieren Sie die Platte selbst (z. B. mit cfdisk), "
                        "formatieren Sie eine EFI-Systempartition als FAT32 und mounten Sie alles "
                        "unter /mnt (Root in /mnt, ESP in /mnt/boot). Der Installer überspringt "
                        "die Partitionierung."),
        "filesystem": "Root-Dateisystem", "swap": "Swap",
        "refresh": "Plattenliste aktualisieren",
        "col_device": "Gerät", "col_size": "Größe", "col_model": "Modell",
        "col_transport": "Schnittstelle", "col_mount": "Mountpunkte",
        "live_disk": "Live-System-Platte – nicht auswählbar",
        "no_disks": "Keine Platten gefunden.",
        "err_pick_disk": "Bitte zuerst eine Zielplatte auswählen.",
        "err_manual_mount": "Manueller Modus erfordert Root unter /mnt und ESP unter /mnt/boot.",
        "swap_auto": "Automatisch (je nach RAM)", "swap_none": "Kein Swap",
        "swap_custom": "Eigene Größe (MiB):",
        "confirm_title": "Destruktive Installation bestätigen",
        "confirm_text": ("Auf {disk} werden ALLE DATEN dauerhaft gelöscht.\n\n"
                         "Geben Sie unten den Gerätepfad zur Bestätigung ein:"),
        "err_confirm": "Der eingegebene Pfad stimmt nicht mit der gewählten Platte überein.",
        "page4_title": "vinOS wird installiert",
        "page4_sub": "Diesen Rechner nicht ausschalten.",
        "installing": "Installation läuft…", "done": "Installation erfolgreich abgeschlossen.",
        "reboot_now": "Jetzt neu starten", "close": "Schließen",
        "log_label": "Installationsprotokoll",
        "elapsed": "Zeit: {t}",
        "err_backend_missing": "Backend-Skript nicht gefunden:\n{path}",
        "err_not_root": "Dieser Installer muss als root laufen.\ninstall.sh auf dem Live-Arch-ISO ausführen.",
        "err_install_failed": "Installation fehlgeschlagen:\n{msg}\n\nSiehe Protokoll oben.",
    },
}

# sensible defaults per UI language
LANG_DEFAULTS = {
    "en": ("us", "en_US.UTF-8", "UTC"),
    "pl": ("pl", "pl_PL.UTF-8", "Europe/Warsaw"),
    "de": ("de", "de_DE.UTF-8", "Europe/Berlin"),
}

KEYMAPS = [
    ("us", "English (US)"), ("gb", "English (UK)"), ("pl", "Polski (QWERTY)"),
    ("de", "Deutsch (QWERTZ)"), ("fr", "Français (AZERTY)"), ("es", "Español"),
    ("it", "Italiano"), ("se", "Svenska"), ("no", "Norsk"), ("ru", "Русский"),
    ("br", "Português (BR)"), ("jp", "Japanese"),
]

FALLBACK_TIMEZONES = [
    "UTC", "Europe/Warsaw", "Europe/Berlin", "Europe/Paris", "Europe/Madrid",
    "Europe/London", "Europe/Rome", "Europe/Vienna", "Europe/Prague",
    "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
    "America/Toronto", "America/Sao_Paulo", "Asia/Tokyo", "Asia/Shanghai",
    "Asia/Kolkata", "Australia/Sydney",
]


def tr(key: str, **fmt) -> str:
    lang = getattr(app, "lang", "en")
    text = STRINGS.get(lang, STRINGS["en"]).get(key) or STRINGS["en"][key]
    return text.format(**fmt) if fmt else text


def list_timezones():
    try:
        out = subprocess.run(["timedatectl", "list-timezones"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return sorted(out.stdout.split())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return FALLBACK_TIMEZONES


def list_disks():
    """Return (disks, error_message). disks = list of dicts from lsblk -J."""
    try:
        out = subprocess.run(
            ["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,MODEL,TYPE,RM,MOUNTPOINTS,TRAN"],
            capture_output=True, text=True, timeout=15)
        if out.returncode != 0:
            return [], out.stderr.strip() or "lsblk failed"
        data = json.loads(out.stdout)
        disks = [d for d in data.get("blockdevices", []) if d.get("type") == "disk"]
        return disks, ""
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return [], str(exc)


def human_size(nbytes):
    try:
        n = float(nbytes)
    except (TypeError, ValueError):
        return str(nbytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024


# --------------------------------------------------------------------------
# Styling
# --------------------------------------------------------------------------
STYLESHEET = """
QWidget { background: #14161c; color: #cdd6f4; font-size: 14px; }
QLabel#headerTitle { font-size: 26px; font-weight: 700; color: #89b4fa; }
QLabel#headerSub { color: #8a8fa3; font-size: 14px; }
QLabel#stepBadge { color: #5b8fd6; font-weight: 600; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTableWidget {
    background: #1c1f28; border: 1px solid #2c3140; border-radius: 6px;
    padding: 6px 8px; selection-background-color: #5b8fd6;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid #5b8fd6; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: #1c1f28; border: 1px solid #2c3140; }
QPushButton {
    background: #2c3140; border: 1px solid #3a4054; border-radius: 6px;
    padding: 8px 18px; font-weight: 600;
}
QPushButton:hover { background: #3a4054; }
QPushButton#primary { background: #5b8fd6; color: #101218; border: none; }
QPushButton#primary:hover { background: #7aa2e3; }
QPushButton#primary:disabled { background: #2c3140; color: #6c7289; }
QPushButton#danger { background: #b8454f; color: #ffffff; border: none; }
QPushButton#danger:hover { background: #d0555f; }
QPushButton:disabled { color: #6c7289; }
QRadioButton, QCheckBox { spacing: 8px; }
QRadioButton::indicator, QCheckBox::indicator { width: 16px; height: 16px; }
QProgressBar {
    background: #1c1f28; border: 1px solid #2c3140; border-radius: 6px;
    text-align: center; height: 22px;
}
QProgressBar::chunk { background: #5b8fd6; border-radius: 5px; }
QTableWidget::item:selected { background: #5b8fd6; color: #101218; }
QHeaderView::section {
    background: #1c1f28; color: #8a8fa3; border: none;
    border-bottom: 1px solid #2c3140; padding: 6px; font-weight: 600;
}
QGroupBox { border: 1px solid #2c3140; border-radius: 8px; margin-top: 12px; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #8a8fa3; }
QFrame#warnFrame { background: #3a2c1c; border: 1px solid #8a6d2f; border-radius: 6px; }
"""


# --------------------------------------------------------------------------
# Confirmation dialog (type-the-disk-name)
# --------------------------------------------------------------------------
class ConfirmWipeDialog(QDialog):
    def __init__(self, disk, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("confirm_title"))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        label = QLabel(tr("confirm_text", disk=disk))
        label.setWordWrap(True)
        layout.addWidget(label)
        self.input = QLineEdit()
        self.input.setPlaceholderText(disk)
        layout.addWidget(self.input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes |
                                   QDialogButtonBox.StandardButton.No)
        buttons.button(QDialogButtonBox.StandardButton.Yes).setProperty("class", "danger")
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.disk = disk
        self.ok = False

    def _check(self):
        if self.input.text().strip() == self.disk:
            self.ok = True
            self.accept()
        else:
            QMessageBox.warning(self, tr("confirm_title"), tr("err_confirm"))


# --------------------------------------------------------------------------
# Wizard pages
# --------------------------------------------------------------------------
class Page1Locale(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        form.setSpacing(16)
        form.setContentsMargins(40, 30, 40, 10)
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("English", "en")
        self.lang_combo.addItem("Polski", "pl")
        self.lang_combo.addItem("Deutsch", "de")
        self.keymap_combo = QComboBox()
        for code, name in KEYMAPS:
            self.keymap_combo.addItem(f"{name}  ({code})", code)
        self.tz_combo = QComboBox()
        self.tz_combo.setEditable(True)
        self.tz_combo.addItems(list_timezones())
        self.lbl_lang = QLabel(); self.lbl_key = QLabel(); self.lbl_tz = QLabel()
        form.addRow(self.lbl_lang, self.lang_combo)
        form.addRow(self.lbl_key, self.keymap_combo)
        form.addRow(self.lbl_tz, self.tz_combo)
        self.lang_combo.currentIndexChanged.connect(self._on_lang)
        self._on_lang()

    def _on_lang(self):
        app.lang = self.lang_combo.currentData()
        keymap, locale, tz = LANG_DEFAULTS[app.lang]
        app.locale = locale
        idx = self.keymap_combo.findData(keymap)
        self.keymap_combo.setCurrentIndex(idx if idx >= 0 else 0)
        i = self.tz_combo.findText(tz)
        self.tz_combo.setCurrentIndex(i if i >= 0 else 0)
        w = self.window()
        if w is not None and w is not self:  # not yet in a window during startup
            w.retranslate()

    def retranslate(self):
        self.lbl_lang.setText(tr("language"))
        self.lbl_key.setText(tr("keymap"))
        self.lbl_tz.setText(tr("timezone"))


class Page2User(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 30, 40, 10)
        form = QFormLayout()
        form.setSpacing(14)
        self.fullname = QLineEdit()
        self.username = QLineEdit()
        self.username.textChanged.connect(self._suggest_hostname)
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password2 = QLineEdit(); self.password2.setEchoMode(QLineEdit.EchoMode.Password)
        self.hostname = QLineEdit(); self.hostname.setText("vinos")
        self.sudo = QCheckBox(); self.sudo.setChecked(True)
        self.lbl_full = QLabel(); self.lbl_user = QLabel()
        self.lbl_pw = QLabel(); self.lbl_pw2 = QLabel()
        self.lbl_host = QLabel()
        form.addRow(self.lbl_full, self.fullname)
        form.addRow(self.lbl_user, self.username)
        form.addRow(self.lbl_pw, self.password)
        form.addRow(self.lbl_pw2, self.password2)
        form.addRow(self.lbl_host, self.hostname)
        form.addRow("", self.sudo)
        outer.addLayout(form)
        self.error = QLabel(""); self.error.setStyleSheet("color: #e06c75;")
        self.error.setWordWrap(True)
        outer.addWidget(self.error)
        outer.addStretch(1)

    def _suggest_hostname(self, text):
        if not self.hostname.isModified() or self.hostname.text() == "vinos":
            cleaned = re.sub(r"[^a-z0-9-]", "-", text.lower()).strip("-")
            if cleaned:
                self.hostname.setText(cleaned[:63])

    def retranslate(self):
        self.lbl_full.setText(tr("fullname")); self.lbl_user.setText(tr("username"))
        self.lbl_pw.setText(tr("password")); self.lbl_pw2.setText(tr("password2"))
        self.lbl_host.setText(tr("hostname")); self.sudo.setText(tr("sudo"))

    def validate(self):
        if len(self.password.text()) < 8:
            return tr("err_pw_short")
        if self.password.text() != self.password2.text():
            return tr("err_pw_mismatch")
        if not re.match(r"^[a-z_][a-z0-9_-]{0,31}$", self.username.text()):
            return tr("err_username")
        if not re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$", self.hostname.text()):
            return tr("err_hostname")
        return ""


class Page3Disk(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 20, 40, 10)
        outer.setSpacing(12)

        self.mode_erase = QRadioButton(); self.mode_erase.setChecked(True)
        self.mode_manual = QRadioButton()
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.mode_erase); self.mode_group.addButton(self.mode_manual)
        outer.addWidget(self.mode_erase); outer.addWidget(self.mode_manual)

        self.manual_hint = QLabel(); self.manual_hint.setWordWrap(True)
        self.manual_hint.setStyleSheet("color: #8a8fa3;")
        self.manual_hint.setVisible(False)
        outer.addWidget(self.manual_hint)
        self.mode_erase.toggled.connect(lambda on: self.manual_hint.setVisible(not on))

        table_box = QHBoxLayout()
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["", "", "", "", "", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.cellClicked.connect(lambda *_: self.error.setText(""))
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnHidden(5, True)  # holds the device path
        table_box.addWidget(self.table, 1)
        outer.addLayout(table_box)

        opts = QHBoxLayout()
        self.refresh_btn = QPushButton()
        self.refresh_btn.clicked.connect(self.refresh)
        self.fs_combo = QComboBox()
        self.fs_combo.addItem("ext4", "ext4")
        self.fs_combo.addItem("Btrfs", "btrfs")
        self.swap_combo = QComboBox()
        self.swap_combo.addItem(tr("swap_auto"), "auto")
        self.swap_combo.addItem(tr("swap_none"), "none")
        self.swap_combo.addItem(tr("swap_custom"), "custom")
        self.swap_spin = QSpinBox()
        self.swap_spin.setRange(256, 32768); self.swap_spin.setValue(4096)
        self.swap_spin.setSuffix(" MiB"); self.swap_spin.setVisible(False)
        self.swap_combo.currentTextChanged.connect(
            lambda _: self.swap_spin.setVisible(self.swap_combo.currentData() == "custom"))
        self.lbl_fs = QLabel(); self.lbl_swap = QLabel()
        opts.addWidget(self.refresh_btn)
        opts.addStretch(1)
        opts.addWidget(self.lbl_fs); opts.addWidget(self.fs_combo)
        opts.addWidget(self.lbl_swap); opts.addWidget(self.swap_combo)
        opts.addWidget(self.swap_spin)
        outer.addLayout(opts)

        warn = QFrame(); warn.setObjectName("warnFrame")
        wl = QVBoxLayout(warn)
        wl.setContentsMargins(10, 8, 10, 8)
        self.warn_label = QLabel("⚠ " + tr("page3_sub"))
        self.warn_label.setWordWrap(True)
        wl.addWidget(self.warn_label)
        outer.addWidget(warn)

        self.error = QLabel(""); self.error.setStyleSheet("color: #e06c75;")
        self.error.setWordWrap(True)
        outer.addWidget(self.error)
        outer.addStretch(1)

        self.disks = []
        self.refresh()

    def refresh(self):
        self.disks, err = list_disks()
        self.table.setRowCount(0)
        if err and not self.disks:
            self.error.setText(err)
            return
        for d in self.disks:
            mounts = [m for m in (d.get("mountpoints") or []) if m]
            is_live = any(str(m).startswith("/run/archiso") for m in mounts)
            path = d.get("path") or f"/dev/{d.get('name')}"
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                d.get("name") or path,
                human_size(d.get("size")),
                (d.get("model") or "—").strip() or "—",
                d.get("tran") or "—",
                " ".join(mounts) or "—",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if is_live:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable
                                  & ~Qt.ItemFlag.ItemIsEnabled)
                    item.setToolTip(tr("live_disk"))
                self.table.setItem(row, col, item)
            holder = QTableWidgetItem(path)
            holder.setData(Qt.ItemDataRole.UserRole, path)
            holder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.table.setItem(row, 5, holder)
        if not self.disks:
            self.error.setText(tr("no_disks"))

    def retranslate(self):
        self.mode_erase.setText(tr("mode_erase"))
        self.mode_manual.setText(tr("mode_manual"))
        self.manual_hint.setText(tr("manual_hint"))
        self.refresh_btn.setText(tr("refresh"))
        self.lbl_fs.setText(tr("filesystem"))
        self.lbl_swap.setText(tr("swap"))
        self.warn_label.setText("⚠ " + tr("page3_sub"))
        for i, (key, _) in enumerate([("swap_auto", None), ("swap_none", None), ("swap_custom", None)]):
            self.swap_combo.setItemText(i, tr(key))
        self.table.setHorizontalHeaderLabels(
            [tr("col_device"), tr("col_size"), tr("col_model"),
             tr("col_transport"), tr("col_mount"), ""])

    def selected_disk(self):
        row = self.table.currentRow()
        if row < 0:
            return ""
        item = self.table.item(row, 5)
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def validate(self):
        if self.mode_manual.isChecked():
            # the backend re-checks the actual mounts; only sanity-check here
            return ""
        disk = self.selected_disk()
        if not disk:
            return tr("err_pick_disk")
        return ""


class Page4Install(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 20, 40, 10)
        outer.setSpacing(12)
        self.status = QLabel(tr("installing"))
        self.status.setStyleSheet("font-size: 16px; font-weight: 600;")
        outer.addWidget(self.status)
        self.bar = QProgressBar(); self.bar.setRange(0, 100)
        outer.addWidget(self.bar)
        self.elapsed_lbl = QLabel("")
        self.elapsed_lbl.setStyleSheet("color: #8a8fa3;")
        outer.addWidget(self.elapsed_lbl)
        lbl = QLabel(tr("log_label")); lbl.setStyleSheet("color: #8a8fa3;")
        outer.addWidget(lbl)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("monospace", 10))
        self.log.setMaximumBlockCount(5000)
        outer.addWidget(self.log, 1)
        btns = QHBoxLayout()
        self.reboot_btn = QPushButton(tr("reboot_now"))
        self.reboot_btn.setObjectName("primary")
        self.reboot_btn.setVisible(False)
        self.reboot_btn.clicked.connect(self._reboot)
        self.close_btn = QPushButton(tr("close"))
        self.close_btn.setVisible(False)
        self.close_btn.clicked.connect(self._close_window)
        btns.addWidget(self.reboot_btn)
        btns.addWidget(self.close_btn)
        btns.addStretch(1)
        outer.addLayout(btns)

        self.proc = None
        self.start_time = None
        self.error_seen = ""
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

    def retranslate(self):
        self.reboot_btn.setText(tr("reboot_now"))
        self.close_btn.setText(tr("close"))

    def _tick(self):
        delta = datetime.timedelta(seconds=int(time.time() - self.start_time))
        self.elapsed_lbl.setText(tr("elapsed", t=str(delta)))

    def _close_window(self):
        self.window().close()

    def _reboot(self):
        if QMessageBox.question(self.window(), tr("app_title"), tr("reboot_now")) == QMessageBox.StandardButton.Yes:
            subprocess.Popen(["systemctl", "reboot"])

    def start(self):
        if not os.path.exists(BACKEND):
            QMessageBox.critical(self.window(), tr("app_title"), tr("err_backend_missing", path=BACKEND))
            self.window().close()
            return
        disk_page = self.window().stack.widget(2)
        user_page = self.window().stack.widget(1)
        locale_page = self.window().stack.widget(0)

        args = ["bash", BACKEND,
                "--username", user_page.username.text(),
                "--hostname", user_page.hostname.text(),
                "--keymap", locale_page.keymap_combo.currentData(),
                "--locale", app.locale,
                "--timezone", locale_page.tz_combo.currentText(),
                "--filesystem", disk_page.fs_combo.currentData(),
                "--yes"]
        if user_page.fullname.text().strip():
            args += ["--fullname", user_page.fullname.text().strip()]
        args += ["--sudo"] if user_page.sudo.isChecked() else ["--no-sudo"]
        if disk_page.mode_manual.isChecked():
            args += ["--manual"]
        else:
            args += ["--disk", disk_page.selected_disk()]
            swap = disk_page.swap_combo.currentData()
            if swap == "none":
                args += ["--swap-size", "none"]
            elif swap == "custom":
                args += ["--swap-size", str(disk_page.swap_spin.value())]

        self.append_log("» " + " ".join(a for a in args if a != "--yes"
                                        and "password" not in a.lower()) + " --yes")
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._on_output)
        self.proc.finished.connect(self._on_finished)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("VINOS_PASSWORD", user_page.password.text())
        self.proc.setProcessEnvironment(env)
        self.proc.start("bash", args)
        self.start_time = time.time()
        self.timer.start()

    def _on_output(self):
        data = bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        for line in data.splitlines():
            line = line.rstrip()
            if not line:
                continue
            if line.startswith("VINOS_PHASE:") or line.startswith("VINOS_PROGRESS:"):
                _, pct, msg = line.split(":", 2)
                try:
                    self.bar.setValue(int(pct))
                except ValueError:
                    pass
                self.status.setText(msg)
                self.append_log(f"[{pct:>3}%] {msg}")
            elif line.startswith("VINOS_ERROR:"):
                self.error_seen = line.split(":", 1)[1]
                self.append_log("‼ " + self.error_seen)
            elif line.startswith("VINOS_DONE:"):
                msg = line.split(":", 1)[1]
                self.bar.setValue(100)
                self.status.setText("✅ " + msg)
            else:
                self.append_log(line)

    def append_log(self, text):
        self.log.appendPlainText(text)

    def _on_finished(self, code, _status):
        self.timer.stop()
        self._tick()
        if code == 0 and not self.error_seen:
            self.status.setText("✅ " + tr("done"))
            self.reboot_btn.setVisible(True)
            self.close_btn.setVisible(True)
        else:
            self.status.setText("‼ " + tr("page4_title"))
            self.close_btn.setVisible(True)
            msg = self.error_seen or f"exit code {code}"
            QMessageBox.critical(self.window(), tr("app_title"), tr("err_install_failed", msg=msg))


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("app_title"))
        self.resize(880, 620)
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setStyleSheet("background: #171a22;")
        hv = QVBoxLayout(header)
        hv.setContentsMargins(40, 24, 40, 18)
        self.title = QLabel("vinOS")
        self.title.setObjectName("headerTitle")
        self.sub = QLabel(); self.sub.setObjectName("headerSub")
        hv.addWidget(self.title); hv.addWidget(self.sub)
        root.addWidget(header)

        self.stack = QStackedWidget()
        self.page1 = Page1Locale()
        self.page2 = Page2User()
        self.page3 = Page3Disk()
        self.page4 = Page4Install()
        for p in (self.page1, self.page2, self.page3, self.page4):
            self.stack.addWidget(p)
        root.addWidget(self.stack, 1)

        footer = QFrame()
        footer.setStyleSheet("background: #171a22; border-top: 1px solid #2c3140;")
        fv = QHBoxLayout(footer)
        fv.setContentsMargins(20, 12, 20, 12)
        self.step_label = QLabel(); self.step_label.setObjectName("stepBadge")
        self.cancel_btn = QPushButton()
        self.back_btn = QPushButton()
        self.next_btn = QPushButton(); self.next_btn.setObjectName("primary")
        self.cancel_btn.clicked.connect(self._cancel)
        self.back_btn.clicked.connect(self._back)
        self.next_btn.clicked.connect(self._next)
        fv.addWidget(self.step_label)
        fv.addStretch(1)
        fv.addWidget(self.cancel_btn)
        fv.addWidget(self.back_btn)
        fv.addWidget(self.next_btn)
        root.addWidget(footer)

        self.setCentralWidget(central)
        self.retranslate()

    def retranslate(self):
        self.setWindowTitle(tr("app_title"))
        self.sub.setText(getattr(self, "_sub_placeholder", "") or tr("page1_sub"))
        self.cancel_btn.setText(tr("cancel"))
        self.back_btn.setText(tr("back"))
        self.next_btn.setText(tr("next"))
        page_subs = {0: "page1_sub", 1: "page2_sub", 2: "page3_sub", 3: "page4_sub"}
        page_titles = {0: "page1_title", 1: "page2_title", 2: "page3_title", 3: "page4_title"}
        idx = self.stack.currentIndex()
        self.sub.setText(tr(page_subs[idx]))
        self.title.setText("vinOS — " + tr(page_titles[idx]) if idx else "vinOS")
        self.step_label.setText(f"{idx + 1} / 4")
        for p in (self.page1, self.page2, self.page3, self.page4):
            p.retranslate()

    def _cancel(self):
        if self.stack.currentIndex() == 3 and self.page4.proc is not None \
                and self.page4.proc.state() != QProcess.ProcessState.NotRunning:
            return  # never offer to abort mid-install silently
        if QMessageBox.question(self, tr("app_title"), tr("cancel")) == QMessageBox.StandardButton.Yes:
            self.close()

    def _back(self):
        i = self.stack.currentIndex()
        if i > 0 and i < 3:
            self.stack.setCurrentIndex(i - 1)
            self.retranslate()

    def _next(self):
        i = self.stack.currentIndex()
        if i == 1:
            err = self.page2.validate()
            self.page2.error.setText(err)
            if err:
                return
        if i == 2:
            err = self.page3.validate()
            self.page3.error.setText(err)
            if err:
                return
            if self.page3.mode_erase.isChecked():
                disk = self.page3.selected_disk()
                dlg = ConfirmWipeDialog(disk, self)
                if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.ok:
                    return
        if i == 3:
            return
        nxt = min(i + 1, 3)
        self.stack.setCurrentIndex(nxt)
        self.retranslate()
        if nxt == 3:
            self.cancel_btn.setVisible(False)
            self.back_btn.setVisible(False)
            self.next_btn.setVisible(False)
            self.page4.start()


def main():
    global app, window
    app = QApplication(sys.argv)
    app.lang = "en"
    app.locale = "en_US.UTF-8"
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)

    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        QMessageBox.critical(None, "vinOS Installer", STRINGS["en"]["err_not_root"])
        sys.exit(1)


    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
