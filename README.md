# AnkiSync Advanced

An Anki add-on for Anki 25.09+ that manages multiple self-hosted sync endpoints and optional per-endpoint credentials.

## Build a distributable with GitHub Actions

The workflow at `.github/workflows/build-addon.yml` builds the Python dependencies on Windows, macOS, and Linux independently, then combines the three dependency bundles into a single installable `AnkiSync_Advanced.ankiaddon` file.

1. Create a GitHub repository and push this project to its `main` branch.
2. Open the repository's **Actions** tab and run **Build AnkiSync Advanced**, or push a commit.
3. Open the completed workflow run.
4. Download the **AnkiSync_Advanced** artifact. It contains `AnkiSync_Advanced.ankiaddon`.
5. In Anki, open **Tools → Add-ons → Install from file…** and select the downloaded `.ankiaddon` file. Restart Anki if prompted.

Pushing a tag such as `v1.0.0` also creates a GitHub Release and attaches the distributable ZIP to it. The workflow pins `keyring==25.6.0` in `requirements-build.txt`; update that pin deliberately and test before releasing.

## What is packaged

- `__init__.py` and `config.json` at the archive root, as expected by Anki's add-on installer.
- `vendor/windows/`, `vendor/macos/`, and `vendor/linux/`, each built on its matching GitHub-hosted runner so OS-specific dependencies are included.
- The add-on automatically adds the matching vendor directory to `sys.path` before importing `keyring`.

## Features

- Tools → AnkiSync Advanced.
- Enable or disable the selected self-hosted sync URL override.
- Add, edit, remove, and select endpoints.
- Optional per-endpoint username/password overrides.
- Endpoint names, URLs, selection, enabled state, and usernames use Anki's add-on configuration API.
- Passwords are stored through `keyring` in the operating system credential manager, never in the add-on config or user files.
- An endpoint-specific login button obtains an Anki sync authentication key for the selected server.

## Credential manager notes

The add-on intentionally refuses to store passwords if keyring is unavailable or selects a backend outside the supported OS credential-manager modules. Windows uses its Windows credential backend, macOS uses Keychain, and Linux expects a working Secret Service/KWallet-compatible environment. Vendoring Python packages does not install or configure a Linux desktop secret service.

## Sync URL behavior

The add-on targets Anki 25.09+ and uses `mw.pm.custom_sync_url()` / `mw.pm.set_custom_sync_url(url)` to work with Anki's self-hosted sync URL preference. It restores the URL captured before the add-on takes control when disabled. It does not force sync or alter sync direction. Back up your collection before switching between servers with different collection histories.

## Development

- Dependency pin: `requirements-build.txt`
- CI packaging: `.github/workflows/build-addon.yml`
- The workflow smoke-tests importing the vendored keyring package on all three OS runners. It does not test GUI behavior or availability of an interactive OS credential service.
