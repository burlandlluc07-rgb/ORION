"""Regression: the frozen GUI .exe runs without a console (sys.stdout/stderr are None)."""

import subprocess
import sys
import textwrap


def test_engine_server_starts_without_console(tmp_path):
    script = textwrap.dedent(f"""
        import sys, threading, time, json, os
        sys.stdout = None
        sys.stderr = None
        os.environ["ORION_HOME"] = {str(tmp_path)!r}
        os.environ["GEMINI_API_KEY"] = "x"
        from orion import app
        app._ensure_std_streams()
        from orion.config import Config
        server = app.ServerThread(Config(), app.free_port(), "tok", None)
        server.start()
        ok = server.ready.wait(30) and server.error is None
        server.shutdown()
        server.join(10)
        sys.__stdout__.write("STARTED" if ok else f"FAILED {{server.error!r}}")
    """)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=90)
    assert "STARTED" in out.stdout, out.stdout + out.stderr
