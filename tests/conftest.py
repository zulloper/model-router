"""Load the plugin directory as the package ``model_router_plugin`` (Hermes imports a directory
plugin as a package, so the relative imports need one). Hermes core (``agent``, ``hermes_cli``) is
only needed by the tests that say so; run with a hermes-agent checkout on PYTHONPATH for those."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

PACKAGE = "model_router_plugin"
_ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _ROOT]


def _load_plugin_package():
    if PACKAGE in sys.modules:
        return sys.modules[PACKAGE]
    spec = importlib.util.spec_from_file_location(
        PACKAGE, _ROOT / "__init__.py", submodule_search_locations=[str(_ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
    return module


_load_plugin_package()


@pytest.fixture(autouse=True)
def _hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "os-home"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "os-home" / ".hermes"))
    (tmp_path / "os-home" / ".hermes").mkdir(parents=True)


class FakeState:
    def __init__(self):
        self.data: dict[str, Any] = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class FakeLlm:
    def __init__(self, text="", parsed=None):
        self.text, self.parsed, self.calls = text, parsed, []

    def complete(self, messages, **kw):
        self.calls.append(("complete", messages, kw))
        return type("R", (), {"text": self.text})()

    def complete_structured(self, **kw):
        self.calls.append(("structured", kw))
        return type("R", (), {"parsed": self.parsed})()


class FakeCtx:
    def __init__(self, settings=None, llm=None):
        self.settings = dict(settings or {})
        self.state = FakeState()
        self.llm = llm or FakeLlm()
        self.hooks, self.middleware, self.tools, self.commands, self.cli = {}, {}, {}, {}, {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def set_config(self, key, value):
        self.settings[key] = value

    def register_hook(self, name, cb, hold_stream=False):
        self.hooks[name] = (cb, hold_stream)

    def register_middleware(self, kind, cb):
        self.middleware[kind] = cb

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = (schema, handler)

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands[name] = handler

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.cli[name] = (setup_fn, handler_fn)


TIERS = [["openai-codex/gpt-6.2-sol", "openai-codex/gpt-mini"], ["anthropic/claude-opus-4-8"]]


@pytest.fixture
def pkg():
    return sys.modules[PACKAGE]


@pytest.fixture
def ctx():
    return FakeCtx({"tiers": TIERS})


@pytest.fixture
def make_ctx():
    return FakeCtx


@pytest.fixture
def fake_llm():
    return FakeLlm


class _RootIsNotATestPackage:
    @pytest.hookimpl(tryfirst=True)
    def pytest_collect_directory(self, path, parent):
        if Path(path).resolve() == _ROOT:
            return pytest.Dir.from_parent(parent, path=path)
        return None


def pytest_configure(config):
    if not config.pluginmanager.has_plugin("model-router-root"):
        config.pluginmanager.register(_RootIsNotATestPackage(), "model-router-root")
