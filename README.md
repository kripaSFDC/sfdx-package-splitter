# sfdx-package-splitter

Split an oversized Salesforce `package.xml` into chunks that actually retrieve —
without silently losing fields.

**Python 3.8+, standard library only. macOS, Linux, Windows.** No dependencies,
no install, nothing leaves your machine.

## The problem

A Metadata API retrieve caps at **10,000 files**, **39 MB compressed**,
**600 MB uncompressed**. An enterprise org's manifest is bigger than that, so the
CLI times out or refuses, and you have to split.

Everyone splits by `<members>` count. That is the wrong unit twice over:

- **Members aren't files.** In metadata format a custom field is not a file — it
  lives inside its object's `.object` file. 15,000 fields can cost a few hundred
  files, while one Lightning bundle costs five.
- **Sub-components can't be separated from their object.** Put
  `Account.Field_A__c` in chunk 1 and `Account.Field_B__c` in chunk 2 and each
  retrieve returns its own partial `Account.object`. Merge them and the last one
  wins — the object comes back with a fraction of its fields. The retrieve
  succeeded. Nothing errored. You just quietly deleted most of an object.

## Use it

```bash
python make-manifest.py 40000 > package.xml          # synthetic, deterministic
python split-manifest.py package.xml --out chunks
python verify-split.py  package.xml chunks/
python retrieve-all.py  --org mydev --chunks chunks/ --target ./retrieved
```

See the failure for yourself:

```bash
python split-manifest.py package.xml --out chunks-bytype --by-type
python verify-split.py  package.xml chunks-bytype/
```

Or the other wrong way — equal slices:

```bash
python split-manifest.py package.xml --out chunks-naive --naive
python verify-split.py  package.xml chunks-naive/
```

| | `--by-type` | `--naive` | default |
|---|---|---|---|
| chunks | 24 (one per type) | 5 (equal slices) | 7 + `package-access.xml` |
| missing / invented / duplicated | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| **objects split across chunks** | **280** | **280** | **0** |
| objects rebuilt 5× / 4× / 3× | 181 / 95 / 4 | — | 0 |
| verdict | **FAIL** | **FAIL** | **PASS** |

Two completely different wrong splits, same 280 broken objects. The unit is the
problem, not the arithmetic.

Every check a normal person would write passes on the by-type split. It is still
broken — and not by accident. Putting `CustomField`, `ListView`, `RecordType`,
`ValidationRule` and `WebLink` in separate manifests means every object that has
them gets rebuilt by four or five separate retrieves, and the last one to land
wins.

**`--by-type` is a good inventory and a bad retrieve plan.** Use it to read the
org; don't use it to pull the org.

## The scripts

| | |
|---|---|
| `make-manifest.py` | Generate a synthetic manifest of any size, so none of this needs a real org's file. Deterministic. |
| `anonymise-manifest.py` | Strip every identifying name out of a real `package.xml` while keeping its exact shape. Default-deny: a word survives only if it is a standard Salesforce name or a metadata suffix. One-way - no mapping is written out. |
| `split-manifest.py` | Group into atoms, weight by files, pack, write `chunks/` + `plan.md`. `--naive` reproduces the broken version. |
| `verify-split.py` | Nothing missing, nothing invented, nothing duplicated, **no object split across chunks**. Exit 1 on failure — put it in CI. |
| `retrieve-all.py` | Run every chunk, resume after a failure, measure what actually came back, write `calibration.json`. |

## Profiles and permission sets

They are pulled out into `package-access.xml` on purpose. A retrieved Profile
carries permissions **only for metadata that was in the same manifest** — so a
profile retrieved inside a small chunk comes back describing a small org. Deploy
it and you remove access.

Prove it in two minutes: retrieve one profile alone, retrieve the same profile
alongside ten objects, diff the two files.

## Known limits

- File weights are **estimates** until `retrieve-all.py` has written
  `calibration.json` from a real retrieve. Feed the real numbers back.
- Metadata format. Retrieving straight into a source-format project changes the
  file maths.
- Wildcards (`*`) are rejected — a wildcard has no count, so it can't be packed.
  List the members first with `sf project list metadata`.

## Working from a real org's manifest

Don't. Anonymise it first:

```bash
python anonymise-manifest.py manifest/package.xml --out package-anon.xml
```

Shape is preserved exactly - same line count, same member count per type, same
object/sub-component relationships, same separators - so it is a far better test
case than anything generated. Every custom name, company prefix and project
codename is replaced with a deterministic pseudonym, consistently, so
`Foo__c.Bar__c` and `Foo__c` still point at the same object.

Verify before you trust it:

```bash
python - <<'EOF'
import re,io
src=set(re.findall(r"[A-Za-z][A-Za-z0-9]{1,}", io.open("manifest/package.xml").read()))
dst=set(re.findall(r"[A-Za-z][A-Za-z0-9]{1,}", io.open("package-anon.xml").read()))
print(sorted(src & dst)[:40])      # should be standard names and metadata types only
EOF
```

Companion to **The Architect in Progress**, episode W03.
Views are my own. No client code, no client metadata, no client manifests.
