# Steam Game Cracker

Applies Steam game cracks using Goldberg, RUNE, Steamless, and Hypervisor Launchers with automated firewall blocking and file backups.

> [!WARNING]
> **Disclaimer & Legal Notice**
> This tool is designed strictly to facilitate running (Steam/DRM-free versions of) games that the user legally owns and has registered through Steam. The developers do not endorse, support, or condone the use of this software for piracy or any other unauthorized purposes. Use this tool responsibly and in compliance with your local laws and Steam's Terms of Service.

## Features

- **Emulator Injection** — Deploys Goldberg (Fork) or RUNE emulator binaries to run games without the Steam client, with full DLC unlocking and custom user configs.
- **SteamStub Unpacking** — Automatically unpacks Steam DRM-protected executables via Steamless before applying emulator hooks.
- **Hypervisor Crack Deployment** — Unpacks and links SHA-verified DenuvOwO cracks on-the-fly with a centralized Hypervisor Launcher.
- **Outbound Firewall Blocking** — Automatically creates Windows Defender Firewall rules to block outbound network calls from cracked executables.
- **Original File Restoration** — Maintains backups of modified binaries to allow instant rollback to clean original files.

---

## Requirements

**Supported platform: Windows only.** Relies on Windows crack executables (Steamless, Goldberg/RUNE) and Windows Defender Firewall PowerShell cmdlets.

- [Python](https://www.python.org/) 3.11–3.13 (in PATH)
- [7-Zip](https://www.7-zip.org/) (installed, expected at `C:\Program Files\7-Zip\7z.exe`)
- **External Binaries** — Cracking tools and emulators are not bundled; place them under `bin/` matching the directory layout below:

  ```text
  bin/
  ├── Goldberg Emu (Fork)/
  │   ├── Goldberg Emu/    (contains regular/, experimental/, steamclient_experimental/, tools/)
  │   └── current_version  (single line version string, e.g. 2026.03.15)
  ├── HypervisorLauncher/
  │   ├── HypervisorLauncher/  (contains launcher.exe, launcher.ini, tools/)
  │   └── current_version      (single line version string, e.g. v1.0.0)
  ├── RUNE Emulator/
  │   ├── RUNE Emulator/       (contains steam_api.dll, steam_api64.dll, steam_emu.ini)
  │   └── current_version      (single line version string, e.g. v1.0.1.70)
  └── Steamless/
      ├── Steamless/           (contains Steamless.exe, Steamless.CLI.exe, Plugins/)
      └── current_version      (single line version string, e.g. v3.1.0.5)
  ```

  Each `current_version` file is recorded in the crack info manifest when a crack is applied.

## Installation

Run `setup.bat` from the repository root to create the virtual environment (`env/`), install dependencies, and generate config files from `.dist` templates.

```powershell
setup.bat
```
*(For development tooling including pytest and ruff, use `setup.bat --dev`)*

### Configuration Templates

| Template | Live File | Purpose |
| :--- | :--- | :--- |
| `.env.dist` | `.env` | Steam credentials (`STEAM_USERNAME`, `STEAM_API_KEY`) for DLC/metadata lookups. |
| `config.dist.toml` | `config.toml` | Environment options (`game_root_dirs`, `zip7_path`, crack info filename). |
| `games.dist.json` | `games.json` | Managed games library mapping folders to Steam App IDs. |
| `settings.dist.json` | `settings.json` | Emulation, unpacker, and hypervisor tool configuration. |

## Configuration

### 1. Game Library (`games.json`)

Edit `games.json` at the project root to register managed games keyed by Steam App ID:

```json
{
  "3357650": {
    "app_name": "PRAGMATA",
    "game_folder": "PRAGMATA"
  }
}
```

`game_folder` directories are resolved against `game_root_dirs` in `config.toml`. Running `.\run.bat scan` populates unmapped games automatically.

### 2. Emulation Settings (`settings.json`)

Edit `settings.json` at the project root to set global defaults for tools and emulators:

```json
{
  "config_settings": {
    "apply_emu": true,
    "apply_steam_stub_unpacker": true,
    "apply_hypervisor": false,
    "copy_extra_files": false,
    "generate_crack_only": false
  },
  "emu_settings": {
    "emulator": "RUNE",
    "rune_settings": {
      "settings": {
        "user_name": "steam_user",
        "language": "english"
      }
    },
    "goldberg_settings": {
      "user": {
        "account_name": "steam_user"
      }
    }
  },
  "steam_stub_unpacker_settings": {
    "keep_bind": true,
    "keep_stub": false
  }
}
```

### 3. Environment Options (`config.toml`)

Edit `config.toml` at the project root to specify tool and scan paths:

| Key | Default | Purpose |
| :--- | :--- | :--- |
| `zip7_path` | `C:\Program Files\7-Zip\7z.exe` | Absolute path to the 7-Zip executable. |
| `game_root_dirs` | `[]` | List of root directory paths scanned for game folders. |
| `crack_info_filename` | `!crack.info` | Filename used for the crack marker file. |

### 4. Game Overrides (`overrides/{app_id}.json`)

Create `overrides/<app_id>.json` to override global defaults for a specific title (e.g. enabling hypervisor crack mode):

```json
{
  "config_settings": {
    "apply_emu": false,
    "apply_steam_stub_unpacker": false,
    "apply_hypervisor": true,
    "copy_extra_files": false,
    "generate_crack_only": false
  }
}
```

*Merge Priority (lowest to highest)*: `settings.json` $\rightarrow$ `overrides/{app_id}.json` $\rightarrow$ CLI Flags.

---

## Usage

All operations run through the launcher (`run.bat <command>` or `tui.bat`):

### Direct Execution

#### Interactive TUI

```powershell
.\tui.bat
```

#### CLI Quick Commands

```powershell
# Scan game roots and register missing titles
.\run.bat scan

# Apply crack to a library game
.\run.bat library --app-id 3357650 crack apply

# Generate standalone crack files without modifying game folder
.\run.bat library --app-id 3357650 crack apply --generate-crack-only

# Restore original unmodified binaries
.\run.bat library --app-id 3357650 crack restore

# Apply crack to an ad-hoc directory outside the library
.\run.bat target --app-id 3357650 --game-dir "C:\Games\PRAGMATA" crack apply

# Batch crack all registered games
.\run.bat library --all crack apply
```

## CLI Reference

### Top-Level Commands

| Command | Description |
| :--- | :--- |
| `scan` | Scan game root directories and register missing titles in `games.json`. |
| `library --app-id <id> \| --all {crack,firewall}` | Run operations on registered library games. |
| `target --app-id <id> --game-dir <path> {crack,firewall}` | Run operations on an ad-hoc game directory. |

### Tool: `crack`

| Command | Description |
| :--- | :--- |
| `crack apply [flags]` | Apply crack files to the game directory. |
| `crack restore` | Revert game files to original unmodified state. |

`apply` flags override config defaults when passed (`--apply-emu`, `--apply-steam-stub`, `--apply-hypervisor`, `--copy-extra-files`, `--generate-crack-only`).

### Tool: `firewall`

| Command | Description |
| :--- | :--- |
| `firewall add [--excludes-path <file>]` | Create outbound firewall block rules for game executables. |
| `firewall refresh [--excludes-path <file>]` | Remove and recreate firewall rules for the target game. |
| `firewall remove` | Remove all outbound firewall rules for the target game. |
