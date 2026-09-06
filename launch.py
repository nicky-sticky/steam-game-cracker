import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "launch.toml"

PINNED_ENV = "RUN_PYTHON_PINNED"


def load_config() -> dict:
    """Load launch.toml: project metadata, elevation default, and aliases."""
    data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
    project = data.get("project", {})
    aliases: dict[str, dict] = {}
    for key, value in data.get("aliases", {}).items():
        if isinstance(value, dict):
            tokens = [str(x) for x in value.get("cmd", [])]
            description = str(value.get("description", ""))
        else:
            tokens = [str(x) for x in value]
            description = ""
        aliases[str(key)] = {"tokens": tokens, "description": description}
    return {
        "name": str(project.get("name", "")),
        "package": str(project.get("package", "")),
        "venv": str(project.get("venv", ".venv")),
        "python": str(project.get("python", "")),
        "elevate": bool(project.get("elevate", False)),
        "aliases": aliases,
    }


def venv_python(cfg: dict) -> Path:
    """Resolve the project venv python from [project] venv + platform."""
    bin_dir = ROOT / cfg["venv"] / ("Scripts" if os.name == "nt" else "bin")
    return bin_dir / ("python.exe" if os.name == "nt" else "python")


def expand_alias(cfg: dict, tokens: list[str]) -> list[str]:
    """Expand a leading alias into its token list, guarding against alias cycles."""
    seen: set[str] = set()
    while tokens and tokens[0] in cfg["aliases"] and tokens[0] not in seen:
        seen.add(tokens[0])
        tokens = cfg["aliases"][tokens[0]]["tokens"] + tokens[1:]
    return tokens


def build_command(cfg: dict, argv: list[str]) -> list[str] | None:
    """
    Build the argv for the requested mode, or None for help/usage.
    An unknown leading token is treated as a CLI command.
    """
    tokens = expand_alias(cfg, list(argv))
    if not tokens or tokens[0] in ("help", "--help", "-h"):
        return None

    mode, args = tokens[0], tokens[1:]
    if mode == "cli":
        return [str(venv_python(cfg)), "-m", f"{cfg['package']}.cli", *args]
    if mode == "docker":
        return ["docker", "compose", *args]
    if mode == "exec":
        return _build_exec(cfg, args)
    return [str(venv_python(cfg)), "-m", f"{cfg['package']}.cli", *tokens]


def usage(cfg: dict) -> str:
    """Render the mode/alias help text."""
    title = cfg["name"] or cfg["package"]
    modes = ["cli <args...>", "exec <tool> <args...>", "docker <args...>"]
    lines = [
        f"[run] {title} launcher",
        "Usage: run <mode> [args...]    or    run <alias> [args...]    (no args: menu)",
        f"Modes: {' | '.join(modes)}",
    ]
    if cfg["aliases"]:
        lines.append("Aliases:")
        for name, alias in sorted(cfg["aliases"].items()):
            suffix = f"  ({alias['description']})" if alias["description"] else ""
            lines.append(f"  {name}  ->  {' '.join(alias['tokens'])}{suffix}")
    lines.append("--admin: run the command with administrator privileges (UAC).")
    return "\n".join(lines)


def _build_exec(cfg: dict, args: list[str]) -> list[str] | None:
    """Resolve a tool inside the project's virtualenv, falling back to PATH."""
    if not args:
        return None
    tool, rest = args[0], args[1:]
    bin_dir = ROOT / cfg["venv"] / ("Scripts" if os.name == "nt" else "bin")
    candidate = bin_dir / (tool + (".exe" if os.name == "nt" else ""))
    if candidate.exists():
        return [str(candidate), *rest]
    return [tool, *rest]


def _menu_items(cfg: dict, bare_modes: dict[str, str] | None = None) -> list[tuple[str, list[str], str]]:
    """Build the menu as (name, tokens, description): aliases, then bare modes."""
    items = [(name, alias["tokens"], alias["description"]) for name, alias in sorted(cfg["aliases"].items())]
    items.extend((mode, [mode], description) for mode, description in (bare_modes or {}).items())
    return items


def _select_action(cfg: dict, bare_modes: dict[str, str] | None = None) -> list[str] | None:
    """Prompt the user to pick an action, returning its token list or None to quit."""
    items = _menu_items(cfg, bare_modes)
    if not items:
        print("[run] No commands defined in launch.toml.")
        return None
    print(cfg["name"] or cfg["package"])
    print()
    print("Available Commands:")
    for i, (name, tokens, description) in enumerate(items, 1):
        line = f"  {i}) {name}"
        if description:
            line += f" - {description}"
        if tokens != [name]:
            line += f"  ({' '.join(tokens)})"
        print(line)
    print()
    print("  q) quit")
    print()
    while True:
        try:
            choice = input("> Select an option: ").strip().lower()
        except EOFError:
            return None
        if choice in ("", "q", "quit", "exit"):
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(items):
            return items[int(choice) - 1][1]
        if choice in cfg["aliases"]:
            return cfg["aliases"][choice]["tokens"]


def _is_admin() -> bool:
    """Whether the current process has administrator rights (Windows)."""
    if os.name != "nt":
        return True
    try:
        return subprocess.run(["fltmc"], capture_output=True).returncode == 0
    except OSError:
        return True


def _relaunch_elevated(argv: list[str]) -> int:
    """Relaunch this script through UAC, returning the elevated run's exit code."""
    script_args = [str(Path(__file__).resolve()), *argv, "--admin-elevated"]
    quoted = ", ".join(f"'{a.replace(chr(39), chr(39) * 2)}'" for a in script_args)
    script = (
        f"$p = Start-Process -FilePath '{sys.executable.replace(chr(39), chr(39) * 2)}' "
        f"-ArgumentList @({quoted}) -Verb RunAs -Wait -PassThru; exit $p.ExitCode"
    )
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script])
    return result.returncode


def _package_installed(venv_py: Path, package: str) -> bool:
    """Whether the package is importable from the project venv python."""
    if not venv_py.is_file():
        return False
    probe = f"import importlib.util, sys; sys.exit(0 if importlib.util.find_spec({package!r}) else 1)"
    try:
        result = subprocess.run([str(venv_py), "-c", probe], capture_output=True)
        return result.returncode == 0
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    """Dispatch a run invocation to the resolved command and propagate its exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg = load_config()
    if not cfg["package"]:
        print("[run] launch.toml is missing 'project.package' - cannot dispatch.", file=sys.stderr)
        return 2

    # Optional pinned interpreter ([project] python) - re-exec once with it.
    if cfg["python"] and os.environ.get(PINNED_ENV) != "1":
        pinned = Path(cfg["python"]).expanduser()
        if pinned.is_file() and os.path.realpath(pinned) != os.path.realpath(sys.executable):
            env = {**os.environ, PINNED_ENV: "1"}
            return subprocess.run([str(pinned), str(Path(__file__).resolve()), *argv], env=env).returncode

    admin_requested = "--admin" in argv
    argv = [a for a in argv if a != "--admin"]

    # No arguments: interactive action menu on a terminal, usage otherwise.
    if not argv:
        if not sys.stdin.isatty():
            print(usage(cfg))
            return 0
        bare: dict[str, str] = {}
        tokens = _select_action(cfg, bare)
        if tokens is None:
            return 0
        argv = tokens

    is_help = not argv or argv[0] in ("help", "--help", "-h")
    elevated_marker = "--admin-elevated" in argv
    argv = [a for a in argv if a != "--admin-elevated"]
    needs_elevation = admin_requested or cfg["elevate"]
    if os.name == "nt" and needs_elevation and not elevated_marker and not is_help and not _is_admin():
        return _relaunch_elevated(argv)
    if elevated_marker and not _is_admin():
        print("[run] Elevation failed - UAC is likely disabled or restricted.", file=sys.stderr)
        return 1

    command = build_command(cfg, argv)
    if command is None:
        print(usage(cfg))
        return 0

    # Python-mode commands require the project venv; docker/exec modes do not.
    if command[0] == str(venv_python(cfg)):
        venv_py = venv_python(cfg)
        if not venv_py.is_file():
            print(
                f"[run] '{cfg['venv']}' virtualenv is missing - run setup (setup.bat / setup.sh) first.",
                file=sys.stderr,
            )
            return 2
        if not _package_installed(venv_py, cfg["package"]):
            print(
                f"[run] '{cfg['package']}' is not installed in the venv - run setup (setup.bat / setup.sh) first.",
                file=sys.stderr,
            )
            return 2

    return subprocess.run(command).returncode


if __name__ == "__main__":
    raise SystemExit(main())
