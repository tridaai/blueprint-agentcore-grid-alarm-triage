"""Law 1, mechanically: the tool catalogue holds read/draft/notify
shapes only, and no tool name carries a control verb. The registry
is the whole attack surface — if a control-shaped tool is ever
added, this test fails before anything else matters."""

import re
from pathlib import Path

from grid_triage.models import ToolShape
from grid_triage.tools import TOOL_REGISTRY

CONTROL_VERBS = [
    "switch",
    "reclose",
    "recloser_op",
    "shed",
    "energize",
    "deenergize",
    "open_breaker",
    "close_breaker",
    "trip_breaker",
    "operate",
    "setpoint",
    "control",
    "command",
    "dispatch_order",
    "execute",
]


def test_registry_shapes_are_read_draft_notify_only():
    assert TOOL_REGISTRY, "registry must not be empty"
    for tool in TOOL_REGISTRY:
        assert tool.shape in {ToolShape.READ, ToolShape.DRAFT, ToolShape.NOTIFY}


def test_no_control_verbs_in_tool_names():
    for tool in TOOL_REGISTRY:
        for verb in CONTROL_VERBS:
            assert verb not in tool.name, f"{tool.name} contains control verb {verb!r}"


def test_registry_source_has_no_control_shaped_entries():
    source = Path(
        __file__
    ).resolve().parents[1].joinpath("src/grid_triage/tools.py").read_text()
    entries = re.findall(r'ToolSpec\(\s*name="([^"]+)"', source)
    assert entries == [t.name for t in TOOL_REGISTRY]
