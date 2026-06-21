#!/usr/bin/env python3
"""
run_gui.py - Quick-start script: launches the backend (FastAPI) and the
frontend (Next.js dev server), then opens the browser.

Usage:
    python run_gui.py                      # start both, open browser
    python run_gui.py --no-browser         # start both, don't open browser
    python run_gui.py --backend-only       # only the backend
    python run_gui.py --frontend-only      # only the frontend (backend must be running)

Requires:
    - Backend: Python 3.10+ with fastapi/uvicorn/pydantic installed
      (pip install fastapi "uvicorn[standard]" pydantic pillow numpy)
    - Frontend: Node.js 18+ / Bun, with dependencies installed
      (cd to project root and run `bun install` or `npm install`)

Press Ctrl+C to stop both services.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
# The backend lives in mini-services/node-editor-server/
BACKEND_DIR = SCRIPT_DIR / "mini-services" / "node-editor-server"
# The frontend is the Next.js project root (where package.json lives)
PROJECT_ROOT = SCRIPT_DIR
FRONTEND_PORT = 3000
BACKEND_PORT = 3030


def is_port_open(port: int, host: str = "localhost") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def wait_for_port(port: int, timeout: int = 30) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if is_port_open(port):
            return True
        time.sleep(0.5)
    return False


def find_python() -> str:
    """Find a suitable Python interpreter. Uses shutil.which so that Windows
    PATHEXT (.exe, .bat, .cmd) is respected."""
    for candidate in [sys.executable, "python3", "python"]:
        # sys.executable is already a full path — use it directly
        if candidate == sys.executable and Path(candidate).exists():
            try:
                r = subprocess.run([candidate, "--version"], capture_output=True, text=True, timeout=5)
                if r.returncode == 0 and "Python 3." in (r.stdout + r.stderr):
                    return candidate
            except Exception:
                pass
            continue
        resolved = shutil.which(candidate)
        if not resolved:
            continue
        try:
            r = subprocess.run([resolved, "--version"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and "Python 3." in (r.stdout + r.stderr):
                return resolved
        except Exception:
            continue
    return sys.executable


def find_pkg_runner() -> str | None:
    """Find bun or npm for the frontend.

    On Windows, `npm` is actually `npm.CMD` (a batch file). Python's
    `subprocess.run(["npm", ...])` fails because CreateProcess doesn't
    search PATHEXT. We use `shutil.which()` which DOES respect PATHEXT and
    returns the full path (e.g. 'C:\\...\\npm.CMD').  Then for .CMD/.BAT
    files we pass shell=True so cmd.exe can execute them.
    """
    for cmd in ["bun", "npm", "npx"]:
        resolved = shutil.which(cmd)
        if not resolved:
            continue
        is_cmd_file = resolved.lower().endswith((".cmd", ".bat"))
        try:
            r = subprocess.run(
                [resolved, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                shell=is_cmd_file,  # required for .CMD/.BAT on Windows
            )
            if r.returncode == 0:
                return resolved
        except Exception:
            continue
    return None


def main():
    parser = argparse.ArgumentParser(description="Start the Multimodal Node Editor (backend + frontend)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open the browser")
    parser.add_argument("--backend-only", action="store_true", help="Only start the backend")
    parser.add_argument("--frontend-only", action="store_true", help="Only start the frontend")
    parser.add_argument("--backend-port", type=int, default=BACKEND_PORT, help=f"Backend port (default {BACKEND_PORT})")
    parser.add_argument("--frontend-port", type=int, default=FRONTEND_PORT, help=f"Frontend port (default {FRONTEND_PORT})")
    args = parser.parse_args()

    processes = []
    python = find_python()

    try:
        # ---- Backend ----
        if not args.frontend_only:
            if is_port_open(args.backend_port):
                print(f"⚠️  Backend port {args.backend_port} already in use — assuming it's running.")
            else:
                print(f"▶  Starting backend on port {args.backend_port} …")
                # check deps
                try:
                    subprocess.run([python, "-c", "import fastapi, uvicorn, pydantic"],
                                   capture_output=True, check=True, timeout=5)
                except subprocess.CalledProcessError:
                    print("   Installing backend dependencies…")
                    subprocess.run([python, "-m", "pip", "install", "fastapi", "uvicorn[standard]",
                                    "pydantic", "pillow", "numpy"], check=False)
                backend_proc = subprocess.Popen(
                    [python, "-m", "uvicorn", "main:app",
                     "--host", "0.0.0.0", "--port", str(args.backend_port),
                     "--reload", "--reload-dir", "."],
                    cwd=str(BACKEND_DIR),
                )
                processes.append(("backend", backend_proc))
                if wait_for_port(args.backend_port, 30):
                    print(f"✓  Backend ready at http://localhost:{args.backend_port}")
                else:
                    print(f"✗  Backend failed to start on port {args.backend_port}")
                    return

        if args.backend_only:
            print("\nBackend-only mode. Press Ctrl+C to stop.")
            for _, p in processes:
                p.wait()
            return

        # ---- Frontend ----
        runner = find_pkg_runner()
        if not runner:
            print("✗  Neither bun nor npm found. Install one to run the frontend.")
            print("   (Checked PATH for: bun, npm, npx)")
            return
        # detect whether this is bun or npm by basename
        runner_name = Path(runner).stem.lower()  # e.g. "bun" or "npm"
        is_bun = "bun" in runner_name
        is_cmd_file = runner.lower().endswith((".cmd", ".bat"))
        # helper: run a command with the right shell flag
        def run_pkg(args_list, **kw):
            return subprocess.run(args_list, cwd=str(PROJECT_ROOT),
                                   shell=is_cmd_file, **kw)

        if is_port_open(args.frontend_port):
            print(f"⚠️  Frontend port {args.frontend_port} already in use — assuming it's running.")
        else:
            print(f"▶  Starting frontend on port {args.frontend_port} (using {runner})…")
            # ensure deps installed
            if not (PROJECT_ROOT / "node_modules").exists():
                print("   Installing frontend dependencies…")
                run_pkg([runner, "install"], capture_output=True, text=True)
            # start Next.js dev server on the chosen port
            #   bun:  bun x next dev -p <port>
            #   npm:  npx next dev -p <port>   (use npx so we don't need a package.json script)
            if is_bun:
                cmd = [runner, "x", "next", "dev", "-p", str(args.frontend_port)]
            else:
                # use npx (resolved alongside npm) to run next directly
                npx = shutil.which("npx") or runner
                cmd = [npx, "next", "dev", "-p", str(args.frontend_port)]
            env = os.environ.copy()
            frontend_proc = subprocess.Popen(
                cmd, cwd=str(PROJECT_ROOT), env=env,
                shell=is_cmd_file or (not is_bun),  # npm/npx .CMD needs shell on Windows
            )
            processes.append(("frontend", frontend_proc))
            if wait_for_port(args.frontend_port, 60):
                print(f"✓  Frontend ready at http://localhost:{args.frontend_port}")
            else:
                print(f"✗  Frontend failed to start on port {args.frontend_port}")

        # ---- Browser ----
        if not args.no_browser and not args.backend_only:
            url = f"http://localhost:{args.frontend_port}"
            print(f"🌐  Opening browser: {url}")
            time.sleep(2)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        print("\n" + "=" * 60)
        print("Both services running. Press Ctrl+C to stop.")
        print("=" * 60)
        for _, p in processes:
            p.wait()

    except KeyboardInterrupt:
        print("\n\nShutting down…")
    finally:
        for name, p in processes:
            if p.poll() is None:
                print(f"  stopping {name}…")
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
        print("All services stopped.")


if __name__ == "__main__":
    main()
