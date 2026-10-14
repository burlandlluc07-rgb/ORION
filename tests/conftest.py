import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orion.config import Config  # noqa: E402
from orion.engine import Engine  # noqa: E402


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("ORION_HOME", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    return Config(tmp_path)


@pytest.fixture
async def engine(config):
    e = Engine(config)
    e.bus.bind(asyncio.get_running_loop())
    e.load_tools()
    from orion.tools.apps import INDEX

    INDEX.ready.set()  # don't block on the background app scan in tests
    e.skills.load()
    from orion.tasks.scheduler import Scheduler

    e.scheduler = Scheduler(e.store, e.notify, e._run_routine, lambda: None)
    yield e
    e.store.close()


@pytest.fixture(autouse=True)
def no_side_effects(monkeypatch):
    """Tests must never open browsers, files or apps on the machine running them (Windows CI included)."""
    opened: list[str] = []
    import orion.tools.apps as apps
    import orion.tools.files as files
    import orion.tools.platform as platform
    import orion.tools.web as web

    for mod in (platform, web, files, apps):
        if hasattr(mod, "open_target"):
            monkeypatch.setattr(mod, "open_target", opened.append)
    monkeypatch.setattr(apps, "launch", opened.append)
    try:
        import orion.ecosystem.accounts as accounts

        monkeypatch.setattr(accounts, "open_target", opened.append)
    except ImportError:
        pass
    return opened
