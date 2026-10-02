#!/usr/bin/env python3
"""
split-manifest.py - split one oversized package.xml into chunks that retrieve.

Runs on macOS, Linux and Windows. Python 3.8+, standard library only.

    python split-manifest.py package.xml --out chunks/

Why this is not just "cut the list into tenths":

1. Metadata API caps a retrieve at 10,000 FILES, 39 MB compressed. Members are
   not files. 15,000 CustomField members produce zero files of their own - in
   metadata format a field lives inside its object's .object file.

2. So a chunk's cost is driven by the types that really are files, and by the
   objects they hang off.

3. A field, list view, record type, validation rule or web link MUST land in the
   same chunk as its object. Split them apart and each chunk returns its own
   partial copy of the same .object file - then your merge silently overwrites
   one with the other and fields disappear. This is the bug that makes naive
   splitters dangerous, and it is the whole reason this script groups before it
   packs.

4. Profiles and permission sets are pulled out separately on purpose. A retrieved
   Profile only carries permissions for metadata that was in the SAME manifest,
   so a profile retrieved in a small chunk comes back gutted. Deploy that and you
   remove access. See plan.md.
"""
import argparse, re, sys
from pathlib import Path
from collections import defaultdict

# cmd.exe is not UTF-8. Replace unmappable characters instead of crashing.
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

NS = "http://soap.sforce.com/2006/04/metadata"

# Sub-components: they live INSIDE their parent object's file in metadata format,
# so they cost no files of their own - but they must travel with the parent.
SUBCOMPONENT_TYPES = {
    "CustomField", "ValidationRule", "ListView", "RecordType", "WebLink",
    "CompactLayout", "FieldSet", "BusinessProcess", "SharingReason", "Index",
}

# Files produced per member, in metadata format.
FILE_WEIGHT = {
    "ApexClass": 2, "ApexTrigger": 2, "ApexPage": 2, "ApexComponent": 2,
    "StaticResource": 2, "EmailTemplate": 2,
    "LightningComponentBundle": 5, "AuraDefinitionBundle": 7,
    "CustomObject": 1,
}
DEFAULT_WEIGHT = 1

# Types where the whole type is a single file, however many members.
SINGLETON_FILE_TYPES = {"CustomLabel"}

# Pulled out into their own manifest - see point 4 above.
ACCESS_TYPES = {"Profile", "PermissionSet", "PermissionSetGroup"}


def parse(path):
    """Regex, not an XML parser: a 40,000-line manifest is a flat, boring shape
    and this keeps the script dependency-free and fast."""
    text = Path(path).read_text(encoding="utf-8")
    version = re.search(r"<version>(.*?)</version>", text)
    buckets, wildcards = {}, []
    for blk in re.findall(r"<types>(.*?)</types>", text, re.S):
        name = re.search(r"<name>(.*?)</name>", blk)
        if not name:
            continue
        t = name.group(1).strip()
        members = [m.strip() for m in re.findall(r"<members>(.*?)</members>", blk, re.S)]
        if "*" in members:
            wildcards.append(t)
        buckets.setdefault(t, [])
        buckets[t].extend(members)
    return buckets, (version.group(1) if version else "62.0"), wildcards


def build_atoms(buckets):
    """An atom is a set of members that must stay together in one chunk."""
    atoms, by_object = [], defaultdict(lambda: defaultdict(list))
    objects_declared = set(buckets.get("CustomObject", []))
    orphan_parents = set()

    for t, members in buckets.items():
        if t in ACCESS_TYPES:
            continue
        if t in SINGLETON_FILE_TYPES:
            atoms.append({"key": f"type:{t}", "members": {t: list(members)},
                          "files": 1, "count": len(members)})
            continue
        if t in SUBCOMPONENT_TYPES:
            for m in members:
                parent = m.split(".", 1)[0]
                by_object[parent][t].append(m)
                if parent not in objects_declared:
                    orphan_parents.add(parent)
            continue
        if t == "CustomObject":
            for m in members:
                by_object[m].setdefault("CustomObject", []).append(m)
            continue
        w = FILE_WEIGHT.get(t, DEFAULT_WEIGHT)
        for m in members:
            atoms.append({"key": f"{t}:{m}", "members": {t: [m]},
                          "files": w, "count": 1})

    for obj, types in by_object.items():
        count = sum(len(v) for v in types.values())
        # one .object file, whatever it contains
        atoms.append({"key": f"object:{obj}", "members": {k: list(v) for k, v in types.items()},
                      "files": 1, "count": count})
    return atoms, orphan_parents


def pack(atoms, max_files, max_members):
    """First-fit-decreasing by member count. Deterministic: atoms are sorted."""
    atoms = sorted(atoms, key=lambda a: (-a["count"], -a["files"], a["key"]))
    chunks = []
    for a in atoms:
        if a["count"] > max_members or a["files"] > max_files:
            # an atom that cannot be split - give it a chunk and flag it
            chunks.append({"atoms": [a], "files": a["files"], "count": a["count"],
                           "oversized": True})
            continue
        for c in chunks:
            if c.get("oversized"):
                continue
            if c["files"] + a["files"] <= max_files and c["count"] + a["count"] <= max_members:
                c["atoms"].append(a); c["files"] += a["files"]; c["count"] += a["count"]
                break
        else:
            chunks.append({"atoms": [a], "files": a["files"], "count": a["count"]})
    return chunks


def render(buckets_for_chunk, version):
    out = ['<?xml version="1.0" encoding="UTF-8"?>', f'<Package xmlns="{NS}">']
    for t in sorted(buckets_for_chunk):
        out.append("    <types>")
        out += [f"        <members>{m}</members>" for m in sorted(set(buckets_for_chunk[t]))]
        out.append(f"        <name>{t}</name>")
        out.append("    </types>")
    out.append(f"    <version>{version}</version>")
    out.append("</Package>")
    return "\n".join(out) + "\n"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("manifest")
    p.add_argument("--out", default="chunks")
    p.add_argument("--max-files", type=int, default=8000,
                   help="headroom under the 10,000 hard cap (default 8000)")
    p.add_argument("--max-members", type=int, default=8000)
    p.add_argument("--by-type", action="store_true",
                   help="one package-<Type>.xml per metadata type. Readable, stable, "
                        "and a good inventory - but see what verify-split.py says "
                        "about it before you retrieve with it.")
    p.add_argument("--naive", action="store_true",
                   help="cut the member list into equal slices, ignoring grouping. "
                        "This is what everyone writes first. Run verify-split.py on "
                        "the result and watch it fail.")
    a = p.parse_args()

    buckets, version, wildcards = parse(a.manifest)
    total_members = sum(len(v) for v in buckets.values())
    if wildcards:
        print(f"!! wildcard (*) in: {', '.join(wildcards)}", file=sys.stderr)
        print("   A wildcard has no count, so it cannot be packed. List the "
              "members first:\n   sf project list metadata --metadata-type <Type>",
              file=sys.stderr)
        sys.exit(2)

    if a.by_type:
        chunks, orphan_parents = [], set()
        for t in sorted(buckets):
            if not buckets[t]:
                continue
            chunks.append({"atoms": [{"members": {t: list(buckets[t])}}],
                           "count": len(buckets[t]), "files": 0, "label": t})
    elif a.naive:
        flat = [(t, m) for t in sorted(buckets) if t not in () for m in buckets[t]]
        size = a.max_members
        chunks = []
        for i in range(0, len(flat), size):
            merged = defaultdict(list)
            for t, m in flat[i:i + size]:
                merged[t].append(m)
            chunks.append({"atoms": [{"members": merged}],
                           "count": len(flat[i:i + size]), "files": 0})
        orphan_parents = set()
    else:
        atoms, orphan_parents = build_atoms(buckets)
        chunks = pack(atoms, a.max_files, a.max_members)

    outdir = Path(a.out); outdir.mkdir(parents=True, exist_ok=True)
    written, packed_members = [], 0
    for i, c in enumerate(chunks, 1):
        merged = defaultdict(list)
        for at in c["atoms"]:
            for t, ms in at["members"].items():
                merged[t].extend(ms)
        packed_members += sum(len(v) for v in merged.values())
        f = outdir / (f"package-{c['label']}.xml" if c.get("label")
                      else f"package-{i:03d}.xml")
        f.write_text(render(merged, version), encoding="utf-8")
        written.append((f.name, c["count"], c["files"], len(merged), c.get("oversized", False)))

    access = ({} if (a.naive or a.by_type)
              else {t: buckets[t] for t in ACCESS_TYPES if buckets.get(t)})
    access_file = None
    if access:
        access_file = outdir / "package-access.xml"
        access_file.write_text(render(access, version), encoding="utf-8")
        packed_members += sum(len(v) for v in access.values())

    # ---- plan.md
    L = [f"# Split plan — `{Path(a.manifest).name}`\n",
         f"\n{total_members:,} components → **{len(written)} chunks**"
         f"{' + 1 access manifest' if access_file else ''}\n",
         f"\nCaps used: {a.max_files:,} files, {a.max_members:,} members per chunk. "
         f"The hard limits are **10,000 files / 39 MB compressed / 600 MB uncompressed** — "
         f"the defaults leave headroom because file counts are estimated, not measured.\n",
         "\n## Chunks\n\n| file | members | est. files | types |\n|---|---|---|---|\n"]
    for n, cnt, fl, ty, over in written:
        L.append(f"| `{n}` | {cnt:,} | {fl:,} | {ty} |"
                 f"{' **ATOM TOO BIG — see below**' if over else ''}\n")
    if access_file:
        L.append(f"| `{access_file.name}` | {sum(len(v) for v in access.values()):,} | — | "
                 f"{len(access)} | **read the warning** |\n")

    L.append("\n## Why profiles and permission sets are separate\n\n"
             "A retrieved Profile contains permissions **only for metadata that was in the "
             "same manifest**. Retrieve a profile in a chunk of 40 components and you get a "
             "profile with permissions for 40 components. Deploy that file and you have not "
             "updated a profile — you have described a much smaller one.\n\n"
             "So: retrieve `package-access.xml` on its own, *after* the rest, and treat the "
             "result as a partial document. Never deploy it back without a diff.\n\n"
             "**Prove it to yourself in two minutes:** retrieve one profile alone, then "
             "retrieve the same profile in a manifest that also names ten objects, and diff "
             "the two files.\n")

    if orphan_parents:
        L.append(f"\n## {len(orphan_parents)} parent objects not declared\n\n"
                 "These objects have fields, list views or rules in the manifest, but the "
                 "object itself is not listed. The retrieve still returns an `.object` file "
                 "for them. They are grouped correctly here; just know the manifest was "
                 "already incomplete before it was split.\n\n")
        L.append("".join(f"- `{o}`\n" for o in sorted(orphan_parents)[:20]))
        if len(orphan_parents) > 20:
            L.append(f"- … and {len(orphan_parents) - 20} more\n")

    L.append("\n## Retrieve them\n\n```\npython retrieve-all.py --org <alias> "
             f"--chunks {outdir.as_posix()} --target ./force-app\n```\n")
    (outdir / "plan.md").write_text("".join(L), encoding="utf-8")

    if a.by_type:
        # ASCII only - cmd.exe on Windows is not UTF-8 and will mojibake or throw.
        S = ["=" * 72, "PACKAGE.XML BREAKDOWN BY METADATA TYPE", "=" * 72,
             f"Source: {Path(a.manifest).name}",
             f"Total metadata types: {len(written)}",
             f"Total components:     {total_members:,}", ""]
        for n, cnt, fl, ty, over in written:
            S.append(f"  {n:<44} {cnt:>7,} members")
        S += ["", "=" * 72,
              "WARNING: this layout is an inventory, not a retrieve plan.",
              "Sub-components (CustomField, ListView, RecordType, ValidationRule,",
              "WebLink) are in different files from their CustomObject, so every",
              "object with them is rebuilt by several separate retrieves and the",
              "last one to land wins. Run verify-split.py before retrieving.",
              "=" * 72]
        (outdir / "BREAKDOWN_SUMMARY.txt").write_text(
            "\n".join(S) + "\n", encoding="ascii", errors="replace")
        print(f"-> {outdir.as_posix()}/BREAKDOWN_SUMMARY.txt")

    print(f"{total_members:,} components in")
    print(f"{packed_members:,} components out  "
          f"({'OK - nothing lost' if packed_members == total_members else 'MISMATCH'})")
    print(f"{len(written)} chunks" + (" + package-access.xml" if access_file else ""))
    if orphan_parents:
        print(f"{len(orphan_parents)} parent objects referenced but not declared")
    print(f"-> {outdir.as_posix()}/  (plan.md written)")


if __name__ == "__main__":
    main()
