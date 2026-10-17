"""AnkiSync Advanced: manage multiple self-hosted sync server URLs.

Target: Anki 25.09+
"""
from __future__ import annotations

import os
import platform
import sys
from urllib.parse import urlparse
from uuid import uuid4

from aqt import gui_hooks, mw
from aqt.qt import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    Qt,
)
from aqt.utils import qconnect, showInfo, showWarning

# The release ZIP vendors keyring dependencies separately for each OS. Select the
# matching bundle before importing keyring so users do not need to pip-install it.
_ADDON_DIR = os.path.dirname(__file__)
_VENDOR_BY_OS = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}
_VENDOR_NAME = _VENDOR_BY_OS.get(platform.system())
if _VENDOR_NAME:
    _VENDOR_PATH = os.path.join(_ADDON_DIR, "vendor", _VENDOR_NAME)
    if os.path.isdir(_VENDOR_PATH) and _VENDOR_PATH not in sys.path:
        sys.path.insert(0, _VENDOR_PATH)

try:
    import keyring
    from keyring.errors import KeyringError
except ImportError:
    keyring = None
    KeyringError = Exception

ADDON_NAME = "AnkiSync Advanced"
DEFAULT_CONFIG = {
    "enabled": False,
    "selected_endpoint_id": None,
    "endpoints": [],
    # Baseline URL per Anki profile, so disabling restores the value that was
    # configured before this add-on took control.
    "original_urls_by_profile": {},
}


def _config() -> dict:
    config = mw.addonManager.getConfig(__name__)
    if config is None:
        # config.json ships with the add-on, so this is only a defensive path.
        config = dict(DEFAULT_CONFIG)
    for key, value in DEFAULT_CONFIG.items():
        if key not in config:
            config[key] = value.copy() if isinstance(value, (dict, list)) else value
    return config


def _save(config: dict) -> None:
    mw.addonManager.writeConfig(__name__, config)


def _profile_key() -> str:
    # Profile names are unique in Anki's profile manager and are stable across
    # launches. Fall back safely if no profile is currently open.
    try:
        return str(mw.pm.name or "default")
    except Exception:
        return "default"


def _selected_endpoint(config: dict) -> dict | None:
    selected_id = config.get("selected_endpoint_id")
    return next(
        (endpoint for endpoint in config.get("endpoints", []) if endpoint.get("id") == selected_id),
        None,
    )


def _valid_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _credential_service(endpoint_id: str) -> str:
    return f"AnkiSyncAdvanced:{endpoint_id}"


def _get_password(endpoint: dict) -> str | None:
    if keyring is None:
        return None
    try:
        return keyring.get_password(_credential_service(endpoint["id"]), endpoint.get("username", ""))
    except Exception:
        return None


def _store_password(endpoint: dict, username: str, password: str) -> None:
    if keyring is None:
        raise RuntimeError(
            "The Python 'keyring' package is unavailable in Anki. Install it into Anki's Python environment "
            "to store passwords in the operating system credential manager. Passwords are not saved to add-on config."
        )
    backend_module = keyring.get_keyring().__class__.__module__.lower()
    if not any(allowed in backend_module for allowed in (
        "keyring.backends.macOS".lower(), "keyring.backends.windows".lower(),
        "keyring.backends.secretservice".lower(), "keyring.backends.kwallet".lower(),
        "keyring.backends.libsecret".lower(),
    )):
        raise RuntimeError(
            f"Refusing to store a password with the configured keyring backend ({backend_module}). "
            "Configure keyring to use macOS Keychain, Windows Credential Manager, or a Linux Secret Service backend."
        )
    try:
        service = _credential_service(endpoint["id"])
        old_username = endpoint.get("username", "")
        if old_username and old_username != username:
            try:
                keyring.delete_password(service, old_username)
            except Exception:
                pass
        keyring.set_password(service, username, password)
    except Exception as exc:
        raise RuntimeError(f"Could not store password in the operating system credential manager: {exc}") from exc

def _delete_password(endpoint: dict) -> None:
    if keyring is None:
        return
    username = endpoint.get("username", "")
    if not username:
        return
    try:
        keyring.delete_password(_credential_service(endpoint["id"]), username)
    except Exception:
        pass

def _login_selected_endpoint(endpoint: dict, parent=None) -> bool:
    username = (endpoint.get("username") or "").strip()
    if not endpoint.get("override_credentials") or not username:
        showWarning("Enable credential override and enter a username for this endpoint.", parent=parent)
        return False
    password = _get_password(endpoint)
    if password is None:
        showWarning(
            "No password was found in the operating system credential manager. Edit this endpoint and save its password first.",
            parent=parent,
        )
        return False
    try:
        # Anki's supported login flow obtains an endpoint-specific hkey. Normal sync
        # operations then use that hkey, not the raw password.
        auth = mw.col.sync_login(username=username, password=password, endpoint=mw.pm.sync_endpoint())
        mw.pm.set_sync_key(auth.hkey)
        mw.pm.set_sync_username(username)
        mw.pm.save()
        showInfo(f"Logged in to {endpoint.get('name', 'selected endpoint')} as {username}.", parent=parent)
        return True
    except Exception as exc:
        showWarning(f"Login failed: {exc}", parent=parent)
        return False


def _apply_selected_endpoint(config: dict) -> bool:
    endpoint = _selected_endpoint(config)
    if not endpoint:
        return False
    url = endpoint.get("url", "").strip()
    if not _valid_url(url):
        return False
    # Anki 25.09's supported preference API. The Preferences dialog itself uses
    # mw.pm.custom_sync_url() / mw.pm.set_custom_sync_url(...).
    mw.pm.set_custom_sync_url(url)
    mw.pm.save()
    return True


def _enable(config: dict) -> bool:
    endpoint = _selected_endpoint(config)
    if not endpoint:
        showWarning("Add and select a sync endpoint before enabling AnkiSync Advanced.")
        return False
    if not _valid_url(endpoint.get("url", "")):
        showWarning("The selected endpoint URL is invalid. Use an http:// or https:// URL.")
        return False

    profile_key = _profile_key()
    originals = config.setdefault("original_urls_by_profile", {})
    # Capture the pre-add-on URL only the first time we take control of this
    # profile; don't overwrite it when Anki starts again with the override active.
    if profile_key not in originals:
        originals[profile_key] = mw.pm.custom_sync_url()
    config["enabled"] = True
    _save(config)
    if not _apply_selected_endpoint(config):
        config["enabled"] = False
        _save(config)
        return False
    return True


def _disable(config: dict) -> None:
    originals = config.setdefault("original_urls_by_profile", {})
    profile_key = _profile_key()
    if profile_key in originals:
        mw.pm.set_custom_sync_url(originals.pop(profile_key))
        mw.pm.save()
    config["enabled"] = False
    _save(config)


def _on_profile_open() -> None:
    """Reapply the selected endpoint after an Anki profile is opened."""
    config = _config()
    profile_key = _profile_key()
    originals = config.setdefault("original_urls_by_profile", {})
    if not config.get("enabled"):
        # If the add-on was disabled while another profile was not active,
        # restore that profile the next time it is opened.
        if profile_key in originals:
            mw.pm.set_custom_sync_url(originals.pop(profile_key))
            mw.pm.save()
            _save(config)
        return
    if profile_key not in originals:
        # This profile has not previously been managed; remember its current URL.
        originals[profile_key] = mw.pm.custom_sync_url()
    if _apply_selected_endpoint(config):
        _save(config)


class EndpointDialog(QDialog):
    def __init__(self, parent, title: str, endpoint: dict | None = None):
        super().__init__(parent)
        endpoint = endpoint or {}
        self.endpoint_id = endpoint.get("id")
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(endpoint.get("name", ""))
        self.url_edit = QLineEdit(endpoint.get("url", ""))
        self.url_edit.setPlaceholderText("https://anki.example.com/")
        self.override_credentials = QCheckBox("Override Anki's default sync credentials for this endpoint")
        self.override_credentials.setChecked(bool(endpoint.get("override_credentials", False)))
        self.username_edit = QLineEdit(endpoint.get("username", ""))
        self.username_edit.setPlaceholderText("Username or email")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("Leave blank to keep the saved password")
        self.password_note = QLabel("Passwords are stored in the operating system credential manager, not in config.json or user_files.")
        self.password_note.setWordWrap(True)
        form.addRow("Name:", self.name_edit)
        form.addRow("Server URL:", self.url_edit)
        form.addRow(self.override_credentials)
        form.addRow("Username:", self.username_edit)
        form.addRow("Password:", self.password_edit)
        form.addRow(self.password_note)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        qconnect(buttons.accepted, self._accept_if_valid)
        qconnect(buttons.rejected, self.reject)
        layout.addWidget(buttons)
        self.override_credentials.toggled.connect(self._credentials_toggled)
        self._credentials_toggled(self.override_credentials.isChecked())

    def _credentials_toggled(self, enabled: bool):
        self.username_edit.setEnabled(enabled)
        self.password_edit.setEnabled(enabled)

    def _accept_if_valid(self):
        name = self.name_edit.text().strip()
        url = self.url_edit.text().strip()
        if not name:
            showWarning("Please enter a name for this endpoint.", parent=self)
            return
        if not _valid_url(url):
            showWarning("Enter a valid http:// or https:// URL, including the host.", parent=self)
            return
        if self.override_credentials.isChecked() and not self.username_edit.text().strip():
            showWarning("Enter a username or disable credential override.", parent=self)
            return
        if self.override_credentials.isChecked() and not self.endpoint_id and not self.password_edit.text():
            showWarning("Enter a password when adding an endpoint with credential override enabled.", parent=self)
            return
        if self.override_credentials.isChecked() and self.password_edit.text() and keyring is None:
            showWarning(
                "The Python 'keyring' package is unavailable. Install it before saving a password; "
                "the password will not be written to a file.", parent=self
            )
            return
        self.accept()

    def values(self) -> tuple[str, str, bool, str, str]:
        return (
            self.name_edit.text().strip(),
            self.url_edit.text().strip(),
            self.override_credentials.isChecked(),
            self.username_edit.text().strip(),
            self.password_edit.text(),
        )


class AnkiSyncAdvancedDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent or mw)
        self.setWindowTitle(ADDON_NAME)
        self.setMinimumSize(560, 390)
        self.config = _config()

        root = QVBoxLayout(self)
        self.enabled_checkbox = QCheckBox("Enable AnkiSync Advanced (override Anki's sync server URL)")
        self.enabled_checkbox.setChecked(bool(self.config.get("enabled")))
        root.addWidget(self.enabled_checkbox)
        root.addWidget(QLabel("Select the self-hosted sync server to use:"))

        self.endpoint_list = QListWidget()
        self.endpoint_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        root.addWidget(self.endpoint_list, 1)

        buttons_row = QHBoxLayout()
        self.add_button = QPushButton("Add…")
        self.edit_button = QPushButton("Edit…")
        self.remove_button = QPushButton("Remove")
        self.login_button = QPushButton("Log in with endpoint credentials")
        buttons_row.addWidget(self.add_button)
        buttons_row.addWidget(self.edit_button)
        buttons_row.addWidget(self.remove_button)
        buttons_row.addWidget(self.login_button)
        buttons_row.addStretch(1)
        root.addLayout(buttons_row)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        qconnect(close_buttons.rejected, self.close)
        qconnect(close_buttons.clicked, self._close_clicked)
        root.addWidget(close_buttons)

        qconnect(self.endpoint_list.currentRowChanged, self._selection_changed)
        qconnect(self.enabled_checkbox.toggled, self._enabled_toggled)
        qconnect(self.add_button.clicked, self._add_endpoint)
        qconnect(self.edit_button.clicked, self._edit_endpoint)
        qconnect(self.remove_button.clicked, self._remove_endpoint)
        qconnect(self.login_button.clicked, self._login_endpoint)
        self._refresh_list()

    def _close_clicked(self, _button=None):
        self.close()

    def _refresh_list(self, select_id: str | None = None):
        self.endpoint_list.clear()
        endpoints = self.config.get("endpoints", [])
        selected_id = select_id or self.config.get("selected_endpoint_id")
        selected_row = -1
        for row, endpoint in enumerate(endpoints):
            item = QListWidgetItem(f"{endpoint.get('name', 'Unnamed')}\n{endpoint.get('url', '')}")
            item.setData(Qt.ItemDataRole.UserRole, endpoint.get("id"))
            self.endpoint_list.addItem(item)
            if endpoint.get("id") == selected_id:
                selected_row = row
        if selected_row >= 0:
            self.endpoint_list.setCurrentRow(selected_row)
        self._selection_changed(self.endpoint_list.currentRow())
        if self.config.get("enabled"):
            endpoint = _selected_endpoint(self.config)
            self.status_label.setText(
                f"Active endpoint: {endpoint.get('name')}" if endpoint else "Enabled, but no endpoint is selected."
            )
        else:
            self.status_label.setText("AnkiSync Advanced is disabled. Anki's normal sync URL is in use.")

    def _selection_changed(self, row: int):
        has_selection = row >= 0 and row < len(self.config.get("endpoints", []))
        self.edit_button.setEnabled(has_selection)
        self.remove_button.setEnabled(has_selection)
        self.login_button.setEnabled(has_selection)
        if not has_selection:
            return
        item = self.endpoint_list.item(row)
        endpoint_id = item.data(Qt.ItemDataRole.UserRole)
        if endpoint_id != self.config.get("selected_endpoint_id"):
            self.config["selected_endpoint_id"] = endpoint_id
            _save(self.config)
            if self.config.get("enabled"):
                if not _apply_selected_endpoint(self.config):
                    showWarning("Could not apply the selected endpoint URL.", parent=self)
                self._refresh_list(endpoint_id)

    def _enabled_toggled(self, enabled: bool):
        if enabled:
            if not _enable(self.config):
                self.enabled_checkbox.blockSignals(True)
                self.enabled_checkbox.setChecked(False)
                self.enabled_checkbox.blockSignals(False)
                return
        else:
            _disable(self.config)
        self._refresh_list()

    def _add_endpoint(self):
        dialog = EndpointDialog(self, "Add sync endpoint")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, url, override_credentials, username, password = dialog.values()
        endpoint = {"id": str(uuid4()), "name": name, "url": url, "override_credentials": override_credentials, "username": username if override_credentials else ""}
        if override_credentials and password:
            try:
                _store_password(endpoint, username, password)
            except RuntimeError as exc:
                showWarning(str(exc), parent=self)
                return
        self.config.setdefault("endpoints", []).append(endpoint)
        self.config["selected_endpoint_id"] = endpoint["id"]
        _save(self.config)
        if self.config.get("enabled"):
            _apply_selected_endpoint(self.config)
        self._refresh_list(endpoint["id"])

    def _edit_endpoint(self):
        endpoint = self._current_endpoint()
        if not endpoint:
            return
        dialog = EndpointDialog(self, "Edit sync endpoint", endpoint)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, url, override_credentials, username, password = dialog.values()
        old_username = endpoint.get("username", "")
        endpoint["name"] = name
        endpoint["url"] = url
        endpoint["override_credentials"] = override_credentials
        endpoint["username"] = username if override_credentials else ""
        if not override_credentials:
            _delete_password({**endpoint, "username": old_username})
        elif password:
            try:
                _store_password({**endpoint, "username": old_username}, username, password)
            except RuntimeError as exc:
                showWarning(str(exc), parent=self)
                return
        elif old_username and old_username != username and keyring is not None:
            try:
                saved = keyring.get_password(_credential_service(endpoint["id"]), old_username)
                if saved is not None:
                    keyring.set_password(_credential_service(endpoint["id"]), username, saved)
                    keyring.delete_password(_credential_service(endpoint["id"]), old_username)
            except Exception as exc:
                showWarning(f"Could not migrate the saved password to the new username: {exc}", parent=self)
                return
        _save(self.config)
        if self.config.get("enabled") and endpoint.get("id") == self.config.get("selected_endpoint_id"):
            _apply_selected_endpoint(self.config)
        self._refresh_list(endpoint["id"])

    def _remove_endpoint(self):
        endpoint = self._current_endpoint()
        if not endpoint:
            return
        answer = QMessageBox.question(
            self,
            "Remove endpoint",
            f"Remove '{endpoint.get('name', 'Unnamed')}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        endpoint_id = endpoint.get("id")
        _delete_password(endpoint)
        self.config["endpoints"] = [e for e in self.config.get("endpoints", []) if e.get("id") != endpoint_id]
        if self.config.get("selected_endpoint_id") == endpoint_id:
            self.config["selected_endpoint_id"] = (
                self.config["endpoints"][0]["id"] if self.config["endpoints"] else None
            )
        _save(self.config)
        if self.config.get("enabled"):
            if not _apply_selected_endpoint(self.config):
                # No endpoint remains: release control and restore the original URL.
                _disable(self.config)
                self.enabled_checkbox.blockSignals(True)
                self.enabled_checkbox.setChecked(False)
                self.enabled_checkbox.blockSignals(False)
        self._refresh_list()

    def _login_endpoint(self):
        endpoint = self._current_endpoint()
        if not endpoint:
            return
        if not _valid_url(endpoint.get("url", "")):
            showWarning("The selected endpoint URL is invalid.", parent=self)
            return
        _login_selected_endpoint(endpoint, parent=self)

    def _current_endpoint(self) -> dict | None:
        row = self.endpoint_list.currentRow()
        endpoints = self.config.get("endpoints", [])
        if row < 0 or row >= len(endpoints):
            return None
        item = self.endpoint_list.item(row)
        endpoint_id = item.data(Qt.ItemDataRole.UserRole)
        return next((e for e in endpoints if e.get("id") == endpoint_id), None)


def show_dialog():
    # Keep a reference so the dialog is not garbage-collected while open.
    mw.anki_sync_advanced_dialog = AnkiSyncAdvancedDialog(mw)
    mw.anki_sync_advanced_dialog.show()


def _add_menu_item():
    action = mw.form.menuTools.addAction(ADDON_NAME)
    qconnect(action.triggered, show_dialog)


# A default config.json is required by getConfig(); config is persisted by Anki
# in its add-on metadata through writeConfig().
gui_hooks.profile_did_open.append(_on_profile_open)
_add_menu_item()

# Restore/apply the requested state for the currently open profile as well. The
# profile hook normally handles this; this is a defensive check for load order.
if mw.pm.name and _config().get("enabled"):
    _on_profile_open()
