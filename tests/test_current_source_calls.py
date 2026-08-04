from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))

from exp_query_conditioned_connectors import CurrentSourceCalls


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_resolves_conservative_static_attribute_calls(tmp_path: Path) -> None:
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(
        tmp_path / "pkg" / "helpers.py",
        "def helper():\n"
        "    pass\n\n"
        "class Worker:\n"
        "    @staticmethod\n"
        "    def run():\n"
        "        pass\n",
    )
    _write(
        tmp_path / "pkg" / "submod.py",
        "def work():\n"
        "    pass\n",
    )
    _write(
        tmp_path / "main.py",
        "from pkg.helpers import helper as imported_helper, Worker\n"
        "from pkg import submod as mod\n"
        "import pkg.submod\n\n"
        "class Local:\n"
        "    def target(self):\n"
        "        pass\n\n"
        "    def self_caller(self):\n"
        "        self.target()\n\n"
        "    def class_caller(self):\n"
        "        Local.target(self)\n\n"
        "def imported_caller():\n"
        "    imported_helper()\n"
        "    Worker.run()\n"
        "    mod.work()\n"
        "    pkg.submod.work()\n",
    )

    calls = CurrentSourceCalls(tmp_path)

    assert calls.resolved_static_calls("main.py", "self_caller") == {
        ("main.py", "target"): "same_file_method"
    }
    assert calls.resolved_static_calls("main.py", "class_caller") == {
        ("main.py", "target"): "same_file_class_method"
    }
    assert calls.resolved_static_calls("main.py", "imported_caller") == {
        ("pkg/helpers.py", "helper"): "imported_function",
        ("pkg/helpers.py", "run"): "imported_class_method",
        ("pkg/submod.py", "work"): "module_attribute",
    }


def test_leaves_receiver_dependent_dispatch_unresolved(tmp_path: Path) -> None:
    _write(
        tmp_path / "main.py",
        "def orphan():\n"
        "    pass\n\n"
        "class Local:\n"
        "    def caller(self, client):\n"
        "        self.orphan()\n"
        "        client.execute()\n"
        "        super().finish()\n",
    )

    calls = CurrentSourceCalls(tmp_path)

    assert calls.resolved_static_calls("main.py", "caller") == {}
    assert calls.unresolved_attribute_calls("main.py", "caller") == {
        "execute",
        "finish",
        "orphan",
    }
