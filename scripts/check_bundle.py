"""The Claude Desktop bundle (.mcpb) starts the way Claude Desktop starts it.

Validates manifest.json, packs the repo root into a bundle (minus .mcpbignore), unpacks it, then runs the manifest's
own command with `doctor` in place of `serve` against a throwaway data folder. Fails unless doctor reports
"Nothing is broken". Needs Node (npx) and uv. The wheel is release_smoke.py's job; this is the bundle's.

    python scripts/check_bundle.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MCPB = "@anthropic-ai/mcpb@2.1.2"
ROOT = Path(__file__).resolve().parents[1]


def run(step: str, cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print(f"== {step}")
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600, **kw)
    if result.returncode != 0:
        print(result.stdout[-2000:], result.stderr[-2000:], sep="\n")
        raise SystemExit(f"FAILED: {step} (exit {result.returncode})")
    return result


def main() -> int:
    npx = shutil.which("npx")
    if not npx:
        print("FAILED: npx not found (install Node.js)")
        return 1
    mcpb = [npx, "-y", MCPB]
    run("validate manifest.json", mcpb + ["validate", "manifest.json"])
    with tempfile.TemporaryDirectory() as tmp:
        bundle, unpacked = Path(tmp) / "bundle.mcpb", Path(tmp) / "unpacked"
        run("pack the repo into a bundle", mcpb + ["pack", ".", str(bundle)])
        run("unpack the bundle", mcpb + ["unpack", str(bundle), str(unpacked)])
        cfg = json.loads((unpacked / "manifest.json").read_text(encoding="utf-8"))["server"]["mcp_config"]
        args = [a.replace("${__dirname}", str(unpacked)) for a in cfg["args"]]
        if not args or args[-1] != "serve":
            print(f"FAILED: expected the manifest's command to end in 'serve', got {args[-1:]}")
            return 1
        command = shutil.which(cfg["command"]) or cfg["command"]
        env = {**os.environ, "DATA_DIR": str(Path(tmp) / "data")}
        out = run("run the manifest's command with doctor", [command, *args[:-1], "doctor"], env=env)
        print(out.stdout[-1500:])
        if "Nothing is broken" not in out.stdout:
            print("FAILED: doctor didn't report 'Nothing is broken'")
            return 1
    print("ok: the bundle packs, unpacks and starts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
