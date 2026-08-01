#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 and earlier on older macOS installations.
    tomllib = None


PLUGIN_NAME = "kio-executive-agent"


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the local KIO Executive Agent plugin for ChatGPT desktop/Codex.")
    parser.add_argument("--kps-root", required=True, help="Path to the kio-project-system checkout.")
    args = parser.parse_args()

    archive_root = Path(__file__).resolve().parents[1]
    kps_root = Path(args.kps_root).expanduser().resolve()
    source = archive_root / "plugins" / PLUGIN_NAME
    if not (source / ".codex-plugin" / "plugin.json").is_file():
        raise SystemExit(f"Plugin source not found: {source}")
    if not (kps_root / "projects" / "registry.md").is_file():
        raise SystemExit(f"KPS root is invalid: {kps_root}")

    home = Path.home()
    destination = home / "plugins" / PLUGIN_NAME
    shutil.copytree(source, destination, dirs_exist_ok=True)

    config_dir = home / ".config" / PLUGIN_NAME
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "archive-root").write_text(str(archive_root) + "\n", encoding="utf-8")
    (config_dir / "kps-root").write_text(str(kps_root) + "\n", encoding="utf-8")

    marketplace_path = home / ".agents" / "plugins" / "marketplace.json"
    marketplace_path.parent.mkdir(parents=True, exist_ok=True)
    marketplace = load_marketplace(marketplace_path)
    entry = {
        "name": PLUGIN_NAME,
        "source": {"source": "local", "path": f"./plugins/{PLUGIN_NAME}"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Productivity",
    }
    plugins = [item for item in marketplace.get("plugins", []) if item.get("name") != PLUGIN_NAME]
    plugins.append(entry)
    marketplace["plugins"] = plugins
    atomic_json(marketplace_path, marketplace)

    codex_config = home / ".codex" / "config.toml"
    install_direct_mcp(codex_config, archive_root, kps_root)

    print(f"Plugin installed: {destination}")
    print(f"Personal marketplace updated: {marketplace_path}")
    print(f"Direct MCP configured: {codex_config}")
    print(f"Archive root: {archive_root}")
    print(f"KPS root: {kps_root}")
    print("Restart ChatGPT/Codex, install KIO Executive Agent from Plugins, and start a new chat.")
    return 0


def install_direct_mcp(path: Path, archive_root: Path, kps_root: Path) -> None:
    start_marker = "# BEGIN KIO EXECUTIVE AGENT (managed by install_kio_plugin.py)"
    end_marker = "# END KIO EXECUTIVE AGENT"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if start_marker in existing:
        before, remainder = existing.split(start_marker, 1)
        if end_marker not in remainder:
            raise SystemExit(f"Incomplete managed KIO MCP block in {path}")
        _, after = remainder.split(end_marker, 1)
        existing = before.rstrip() + "\n" + after.lstrip("\n")
    elif '[mcp_servers."kio-executive-agent"]' in existing or "[mcp_servers.kio-executive-agent]" in existing:
        raise SystemExit(f"An unmanaged kio-executive-agent MCP entry already exists in {path}; review it before installing.")

    server = archive_root / "src" / "kio_mcp_server.py"
    block = "\n".join([
        start_marker,
        '[mcp_servers."kio-executive-agent"]',
        f"command = {toml_string(sys.executable)}",
        f"args = [{toml_string('-B')}, {toml_string(str(server))}]",
        f"cwd = {toml_string(str(archive_root))}",
        "enabled = true",
        "required = false",
        "startup_timeout_sec = 10",
        "tool_timeout_sec = 1800",
        'default_tools_approval_mode = "writes"',
        f"env = {{ KPS_ROOT = {toml_string(str(kps_root))} }}",
        end_marker,
        "",
    ])
    combined = existing.rstrip() + ("\n\n" if existing.strip() else "") + block
    if tomllib is not None:
        try:
            tomllib.loads(combined)
        except tomllib.TOMLDecodeError as exc:
            raise SystemExit(f"Generated Codex MCP configuration is invalid; no change written: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".toml.tmp")
    temporary.write_text(combined, encoding="utf-8")
    temporary.replace(path)


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def load_marketplace(path: Path) -> dict:
    if not path.exists():
        return {"name": "personal", "interface": {"displayName": "Personal"}, "plugins": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("plugins", []), list):
        raise SystemExit(f"Invalid marketplace file: {path}")
    data.setdefault("name", "personal")
    data.setdefault("interface", {"displayName": "Personal"})
    return data


def atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
