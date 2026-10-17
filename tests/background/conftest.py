import copy
from types import SimpleNamespace

import pytest

from orion.background import config
from orion.background.engine import BackgroundAgent
from orion.background.notify import Notifier
from orion.background.store import Store


@pytest.fixture(autouse=True)
def orion_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("ORION_HOME", str(home))
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture
def workdir(tmp_path):
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture
def cfg(workdir):
    return config.load(overrides={
        "daemon": {"workers": 2},
        "tasks": {"dummy_seconds": 0.2, "timeout_seconds": 30},
        "safety": {"allowed_paths": [str(workdir)]},
        "notify": {"toast": False},
        "hotkey": {"enabled": False},
        "tray": {"enabled": False},
        "gemini": {"use_orion_keyring": False, "retries": 0},
    })


@pytest.fixture
def make_agent(cfg, tmp_path):
    made = []

    def make(runners=None, db=None, **over):
        c = config.Config(config._merge(cfg, over)) if over else cfg
        agent = BackgroundAgent(c, store=Store(db or tmp_path / "tasks.db"), notifier=Notifier(c), runners=runners)
        made.append(agent)
        return agent

    yield make
    for a in made:
        a.stop(timeout=2)
        a.store.close()


# -- fake Claude client ------------------------------------------------------

class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kw):
        self.calls.append(copy.deepcopy(kw))
        if not self.script:
            raise AssertionError("fake client ran out of scripted responses")
        content, stop = self.script.pop(0)
        return SimpleNamespace(content=content, stop_reason=stop)


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)
        self.beta = SimpleNamespace(messages=self.messages)

    def with_options(self, **kw):
        return self


def tool_use(id_, name, **inp):
    return {"type": "tool_use", "id": id_, "name": name, "input": inp}


def text(t):
    return {"type": "text", "text": t}


# -- fake Gemini client ------------------------------------------------------

class FakeModels:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": copy.deepcopy(contents), "config": config})
        if not self.script:
            raise AssertionError("fake Gemini client ran out of scripted responses")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeGenai:
    def __init__(self, script):
        self.models = FakeModels(script)


def gemini_reply(*parts, finish="STOP"):
    from google.genai import types
    built = []
    for p in parts:
        if isinstance(p, str):
            built.append(types.Part(text=p))
        else:
            name, args = p
            built.append(types.Part(function_call=types.FunctionCall(name=name, args=args),
                                    thought_signature=b"sig-" + name.encode()))
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=built), finish_reason=finish)])
