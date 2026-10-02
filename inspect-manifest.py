#!/usr/bin/env python3
"""
inspect-manifest.py - what is actually in a package.xml.

    python inspect-manifest.py package.xml

Sorted by count, biggest first. 'dotted' marks a sub-component type: its members
carry their parent object in the name (Account.Field__c), which means they are
not files of their own and they cannot be separated from that object.
"""
import re, sys
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

SUBCOMPONENT = {"CustomField", "ValidationRule", "ListView", "RecordType",
                "WebLink", "CompactLayout", "FieldSet", "BusinessProcess",
                "SharingReason", "Index"}


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: inspect-manifest.py <package.xml> [--top N]")
    path = Path(sys.argv[1])
    top = None
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])

    text = path.read_text(encoding="utf-8")
    rows, objects, declared_objs, total = [], set(), set(), 0
    for blk in re.findall(r"<types>(.*?)</types>", text, re.S):
        n = re.search(r"<name>(.*?)</name>", blk) or re.search(r"<n>(.*?)</n>", blk)
        if not n:
            continue
        t = n.group(1).strip()
        members = re.findall(r"<members>(.*?)</members>", blk, re.S)
        dotted = sum(1 for m in members if "." in m)
        for m in members:
            if "." in m:
                objects.add(m.split(".", 1)[0])
        if t == "CustomObject":
            declared_objs.update(m.strip() for m in members)
        rows.append((t, len(members), dotted))
        total += len(members)

    rows.sort(key=lambda r: (-r[1], r[0]))
    shown = rows[:top] if top else rows

    print()
    print(f"  {path.name}")
    print()
    for t, c, dotted in shown:
        tag = "  dotted" if t in SUBCOMPONENT or dotted == c and c else ""
        print(f"  {t:<28}{c:>7,}{tag}")
    if top and len(rows) > top:
        print(f"  {'...':<28}{'':>7}")
    print(f"  {'':<28}{'-------':>7}")
    print(f"  {'':<28}{total:>7,} components, {len(rows)} types")
    print()
    orphans = objects - declared_objs
    print(f"  {len(objects):,} objects have fields, list views or rules here")
    print(f"  {len(declared_objs):,} objects are declared as CustomObject")
    print(f"  {len(objects | declared_objs):,} objects are touched in total")
    if orphans:
        print()
        print(f"  -> {len(orphans):,} of them have fields or rules in this manifest")
        print(f"     but are never listed as CustomObject. The manifest was already")
        print(f"     incomplete before anyone tried to split it.")
    print()


if __name__ == "__main__":
    main()
