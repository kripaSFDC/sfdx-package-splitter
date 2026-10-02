#!/usr/bin/env python3
"""
verify-split.py - prove the split lost nothing and duplicated nothing.

    python verify-split.py package.xml chunks/

Checks three things:
  1. every (type, member) in the original appears in exactly one chunk
  2. no chunk invented anything
  3. no object's sub-components are spread across two chunks  <- the one that matters

Exit code 0 only if all three pass. Run it in CI, run it before you retrieve.
"""
import sys, re
from pathlib import Path
from collections import defaultdict

# cmd.exe is not UTF-8. Replace unmappable characters instead of crashing.
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

SUBCOMPONENT_TYPES = {
    "CustomField", "ValidationRule", "ListView", "RecordType", "WebLink",
    "CompactLayout", "FieldSet", "BusinessProcess", "SharingReason", "Index",
}


def pairs(path):
    text = Path(path).read_text(encoding="utf-8")
    out = []
    for blk in re.findall(r"<types>(.*?)</types>", text, re.S):
        n = re.search(r"<name>(.*?)</name>", blk)
        if not n:
            continue
        t = n.group(1).strip()
        for m in re.findall(r"<members>(.*?)</members>", blk, re.S):
            out.append((t, m.strip()))
    return out


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: verify-split.py <original.xml> <chunks-dir>")
    original = pairs(sys.argv[1])
    orig = set(original)
    if len(original) != len(orig):
        print(f"note: original had {len(original) - len(orig)} duplicate entries")

    files = sorted(Path(sys.argv[2]).glob("package-*.xml"))
    if not files:
        sys.exit("no package-*.xml found")

    seen, where, obj_home, ok = defaultdict(int), {}, defaultdict(set), True
    for f in files:
        for t, m in pairs(f):
            seen[(t, m)] += 1
            where.setdefault((t, m), f.name)
            if t in SUBCOMPONENT_TYPES or t == "CustomObject":
                parent = m.split(".", 1)[0]
                obj_home[parent].add(f.name)

    missing = orig - set(seen)
    extra = set(seen) - orig
    dupes = {k: v for k, v in seen.items() if v > 1}
    split_objects = {o: c for o, c in obj_home.items() if len(c) > 1}

    print(f"original      {len(orig):,} components")
    print(f"chunks        {len(files)} files, {sum(seen.values()):,} entries")
    for label, bad in (("missing", missing), ("invented", extra), ("duplicated", dupes)):
        print(f"{label:<13} {len(bad):,}")
        if bad:
            ok = False
            for k in list(bad)[:5]:
                print(f"    {k[0]}: {k[1]}")

    print(f"objects split across chunks   {len(split_objects):,}")
    if split_objects:
        ok = False
        for o, c in list(split_objects.items())[:5]:
            print(f"    {o} -> {', '.join(sorted(c))}")
        print("    ^ each of those chunks returns its own partial .object file.")
        print("      Merging them overwrites one with the other. Fix before retrieving.")

    print("\nPASS - safe to retrieve" if ok else "\nFAIL - do not retrieve this split")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
