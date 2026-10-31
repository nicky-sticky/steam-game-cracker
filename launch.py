import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "launch.toml"


def load_config() -> tuple[dict[str, Any], dict[str, Any]]:
    """Load project metadata and commands from launch.toml."""
    if not CONFIG_PATH.is_file():
        return {}, {}
    data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return data.get("project", {}), data.get("commands", {})


def resolve_python(project: dict[str, Any]) -> tuple[str | None, Path | None]:
    """Resolve Python executable and bin directory via project venv or host environment."""
    venv_raw = project.get("venv")
    if not venv_raw or venv_raw is False:
        dot_py = ROOT / ".python-path"
        if dot_py.is_file():
            custom = dot_py.read_text(encoding="utf-8").strip()
            if custom:
                custom_path = Path(custom).expanduser()
                if custom_path.is_file():
                    return str(custom_path), custom_path.parent
        return None, None

    venv_path = Path(venv_raw).expanduser()
    if not venv_path.is_absolute():
        venv_path = (ROOT / venv_path).resolve()

    bin_dir = venv_path / ("Scripts" if os.name == "nt" else "bin")
    py = bin_dir / ("python.exe" if os.name == "nt" else "python")
    if not py.is_file():
        print(f"[error] Virtual environment not found at {venv_path}.", file=sys.stderr)
        print("Please initialize your environment (e.g. run setup.bat) or check launch.toml.", file=sys.stderr)
        sys.exit(1)

    return str(py), bin_dir


def ensure_admin(argv: list[str]) -> None:
    """Relaunch process with administrator privileges on Windows via UAC."""
    import ctypes

    if os.name == "nt" and not ctypes.windll.shell32.IsUserAnAdmin():
        quoted = ", ".join(
            f"'{a.replace(chr(39), chr(39) * 2)}'" for a in [str(Path(__file__).resolve()), *argv, "--admin-elevated"]
        )
        script = (
            f"$p = Start-Process -FilePath '{sys.executable.replace(chr(39), chr(39) * 2)}'"
            f" -ArgumentList @({quoted}) -Verb RunAs -Wait -PassThru; exit $p.ExitCode"
        )
        sys.exit(subprocess.run(["powershell", "-NoProfile", "-Command", script]).returncode)


def resolve_command(project: dict[str, Any], commands: dict[str, Any], argv: list[str]) -> list[str] | None:
    """Resolve command from [commands] or forward unlisted arguments to default command."""
    if not argv:
        return None

    # Handle help unless user explicitly defined a "help" command
    if argv[0] in ("--help", "-h") or (argv[0] == "help" and "help" not in commands):
        return None

    # 1. Exact match in [commands]
    if argv[0] in commands:
        spec = commands[argv[0]]
        cmd = spec.get("cmd", spec) if isinstance(spec, dict) else spec
        base_cmd = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
        return base_cmd + argv[1:]

    # 2. Unlisted argument -> forward to configured default command
    default_name = project.get("default")
    if default_name and default_name in commands:
        spec = commands[default_name]
        cmd = spec.get("cmd", spec) if isinstance(spec, dict) else spec
        base_cmd = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
        return base_cmd + argv

    print(f"[error] Unknown command '{argv[0]}'.", file=sys.stderr)
    return None


def show_menu(project: dict[str, Any], commands: dict[str, Any]) -> list[str] | None:
    """Render interactive numeric action menu."""
    if not commands:
        print(f"[{project.get('name', 'App')}] No commands defined in launch.toml.")
        return None

    title = project.get("name") or project.get("package") or "Launcher"
    print(f"\n{title}\n\nAvailable Commands:")
    items = list(commands.items())
    default_name = project.get("default")
    for i, (name, spec) in enumerate(items, 1):
        desc = f" - {spec.get('description')}" if isinstance(spec, dict) and spec.get("description") else ""
        default_tag = " (default)" if name == default_name else ""
        print(f"  {i:>2}) {name:<16}{desc}{default_tag}")
    print("\n   q) quit\n")

    while True:
        try:
            choice = input("> Select an option: ").strip().lower()
        except EOFError:
            return None
        if choice in ("", "q", "quit", "exit"):
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(items):
            return [items[int(choice) - 1][0]]
        if choice in commands:
            return [choice]
        print("Invalid choice, please select a valid number or command.")


def show_usage(project: dict[str, Any], commands: dict[str, Any]) -> None:
    """Print command usage summary."""
    title = project.get("name") or project.get("package") or "Launcher"
    default_name = project.get("default")
    print(f"[{title}] Usage: python launch.py [command] [args...]")
    if default_name and default_name in commands:
        print(f"  Note: Unlisted arguments forward directly to '{default_name}'.")

    if commands:
        print("\nAvailable Commands:")
        for name, spec in sorted(commands.items()):
            desc = f"  ({spec.get('description')})" if isinstance(spec, dict) and spec.get("description") else ""
            default_tag = " [default]" if name == default_name else ""
            print(f"  {name:<16}{desc}{default_tag}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    project, commands = load_config()

    admin_requested = "--admin" in argv
    elevated_marker = "--admin-elevated" in argv
    argv = [a for a in argv if a not in ("--admin", "--admin-elevated")]
    is_help = not argv or argv[0] in ("help", "--help", "-h")

    if (project.get("elevate") or admin_requested) and not elevated_marker and not is_help:
        ensure_admin(argv)

    if not argv:
        if not sys.stdin.isatty():
            show_usage(project, commands)
            return 0
        selected = show_menu(project, commands)
        if not selected:
            return 0
        argv = selected

    cmd = resolve_command(project, commands, argv)
    if cmd is None:
        show_usage(project, commands)
        return 0 if is_help else 1

    py_exe, py_bin = resolve_python(project)
    env = dict(os.environ)

    if py_bin:
        env["PATH"] = f"{py_bin}{os.pathsep}{env.get('PATH', '')}"
    if cmd[0] in ("python", "python3"):
        cmd[0] = py_exe or shutil.which(cmd[0], path=env.get("PATH")) or sys.executable
    else:
        resolved = shutil.which(cmd[0], path=env.get("PATH"))
        if resolved:
            cmd[0] = resolved

    src_dir = ROOT / "src"
    if src_dir.is_dir():
        env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(os.pathsep)

    return subprocess.run(cmd, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
