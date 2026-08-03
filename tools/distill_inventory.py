#!/usr/bin/env python3
"""Distill docs/lom-raw.json into the compact summary the server ships.

The MCP server validates lom_set/lom_call against the inventory before touching
the wire (CONTRACT B.2) and needs enum values (e.g. RecordingQuantization).
Shipping the full 1 MB raw dump inside the package would be waste; this keeps
classes -> {props: {name: writable}, methods, listeners} plus all enums.

Regenerate after re-running the Phase 0 introspector on a new Live version:

    python3 tools/distill_inventory.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "docs" / "lom-raw.json"
OUT = ROOT / "server" / "src" / "alberton_mcp" / "data" / "lom_summary.json"


def listeners_of(members):
    return sorted(
        name[len("add_"):-len("_listener")]
        for name in members
        if name.startswith("add_") and name.endswith("_listener")
    )


def distill_class(qualname, cls, out):
    members = cls.get("members", {})
    props = {}
    methods = []
    for name, member in members.items():
        kind = member.get("kind")
        if kind == "property":
            props[name] = bool(member.get("writable"))
        elif kind == "method":
            if not (name.startswith(("add_", "remove_")) or
                    name.endswith("_has_listener")):
                methods.append(name)
    out["classes"][qualname] = {
        "props": props,
        "methods": sorted(methods),
        "listeners": listeners_of(members),
    }
    for nested_name, nested in cls.get("nested_classes", {}).items():
        if "error" in nested and "members" not in nested:
            continue
        distill_class("%s.%s" % (qualname, nested_name), nested, out)


def main():
    raw = json.loads(RAW.read_text())
    out = {
        "live": raw.get("meta", {}).get("live_version", {}),
        "generated_at": raw.get("meta", {}).get("generated_at"),
        "classes": {},
        "enums": {},
    }
    for module_name, module in raw.get("modules", {}).items():
        for enum_name, enum in module.get("enums", {}).items():
            out["enums"]["%s.%s" % (module_name, enum_name)] = enum.get("values", {})
        for class_name, cls in module.get("classes", {}).items():
            distill_class("%s.%s" % (module_name, class_name), cls, out)
            for enum_name, member in cls.get("members", {}).items():
                if member.get("kind") == "enum":
                    out["enums"]["%s.%s.%s" % (module_name, class_name, enum_name)] = \
                        member.get("values", {})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print("wrote %s: %d classes, %d enums"
          % (OUT, len(out["classes"]), len(out["enums"])))


if __name__ == "__main__":
    main()
