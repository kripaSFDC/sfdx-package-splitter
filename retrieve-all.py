#!/usr/bin/env python3
"""
retrieve-all.py - run every split manifest against an org, with a safety gate.

The cross-platform twin of retrieve-all.ps1. Same gate, same refusals, same
flags. macOS, Linux, Windows. Python 3.8+, standard library only.

    python retrieve-all.py --org mydev --dry-run
    python retrieve-all.py --org mydev
    python retrieve-all.py --org mydev --resume          # skip what succeeded
    python retrieve-all.py --org mydev --manifest-dir chunks-bytype

Retrieves in METADATA format (--target-metadata-dir) so the result can be
counted. The split was planned on an ESTIMATE of files per component; this
measures the truth and writes calibration.json, so the next split is planned on
real numbers instead of guesses.
"""
import argparse, functools, json, shutil, subprocess, sys, time, zipfile
from pathlib import Path

# unbuffered: output must interleave correctly with the sf CLI's own
print = functools.partial(print, flush=True)

# cmd.exe is not UTF-8. Replace unmappable characters instead of crashing.
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

HARD_FILES, HARD_MB = 10000, 39
WIN = sys.platform == "win32"


def sh(cmd, dry=False):
    # flush before handing the terminal to a child process, or our output
    # arrives after theirs when stdout is a pipe
    print("   $ " + " ".join(str(c) for c in cmd), flush=True)
    if dry:
        return 0, ""
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       shell=WIN)
    return r.returncode, (r.stderr or r.stdout or "")


def measure(path: Path):
    zips = list(path.glob("*.zip"))
    if zips:
        with zipfile.ZipFile(zips[0]) as zf:
            n = sum(1 for i in zf.infolist() if not i.is_dir())
        return n, zips[0].stat().st_size
    files = [f for f in path.rglob("*") if f.is_file()]
    return len(files), sum(f.stat().st_size for f in files)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--org", required=True)
    p.add_argument("--manifest-dir", "--chunks", dest="manifest_dir", default="chunks")
    p.add_argument("--source", default="package.xml",
                   help="the original manifest, for the verify gate")
    p.add_argument("--target", default="retrieved")
    p.add_argument("--wait", type=int, default=33, help="minutes to wait per manifest")
    p.add_argument("--resume", action="store_true", help="skip manifests already done")
    p.add_argument("--skip-verify", action="store_true")
    p.add_argument("--parallel", action="store_true", help="see the refusal below")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    # --- the parallel trap ---------------------------------------------------
    # Two retrieves at once can both be writing the SAME .object file, because a
    # CustomField manifest and a ValidationRule manifest both produce
    # Account.object. Sequential, the last write wins and you lose fields.
    # Parallel, you can get a half-written file. This is the exact failure this
    # tool exists to prevent, so it is off unless explicitly overridden.
    if a.parallel and not a.force:
        print("[REFUSED] --parallel writes several retrieves into one tree at once.")
        print("          Two manifests can produce the same .object file. Sequential")
        print("          loses fields; parallel can corrupt the file outright.")
        print("          If you have verified no object spans two manifests, use --force.")
        sys.exit(1)
    if a.parallel:
        print("[WARN] --parallel --force: you are on your own. Running sequentially")
        print("       anyway, because this tool will not race writes into one tree.")

    mdir = Path(a.manifest_dir)
    if not mdir.is_dir():
        sys.exit(f"[ERROR] Manifest directory not found: {mdir}")
    manifests = sorted(mdir.glob("package-*.xml"))
    if not manifests:
        sys.exit(f"[ERROR] No package-*.xml in {mdir}")

    # --- gate: never retrieve a split the verifier rejects -------------------
    if not a.skip_verify:
        verifier, src = Path("verify-split.py"), Path(a.source)
        if verifier.exists() and src.exists():
            print("[....] Verifying the split before touching the org...", flush=True)
            r = subprocess.run([sys.executable, str(verifier), str(src), str(mdir)])
            sys.stdout.flush()
            if r.returncode != 0:
                print("\n[REFUSED] The split is not safe to retrieve. Nothing was pulled.")
                print("          Fix the split, or re-run with --skip-verify if you")
                print("          know exactly why you are overriding this.")
                sys.exit(1)
            print()
        else:
            print(f"[WARN] {src} or {verifier} missing - skipping verification.")

    if not a.dry_run and not shutil.which("sf"):
        sys.exit("[ERROR] sf CLI not found on PATH.")

    target = Path(a.target)
    if not a.dry_run:
        target.mkdir(parents=True, exist_ok=True)

    results, failed, skipped = [], [], 0
    for i, m in enumerate(manifests, 1):
        label = m.stem.replace("package-", "")
        dest, done = target / m.stem, target / m.stem / ".complete"
        print(f"\n[{i}/{len(manifests)}] {label}")

        if a.resume and done.exists():
            print("   [SKIP] already done")
            results.append(json.loads(done.read_text(encoding="utf-8")))
            skipped += 1
            continue
        if any(k in label.lower() for k in ("access", "profile", "permissionset")):
            print("   NOTE: profiles and permission sets come back PARTIAL - they only")
            print("         carry permissions for metadata in the same manifest. Diff")
            print("         before you ever deploy this one.")

        if not a.dry_run:
            dest.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        code, err = sh(["sf", "project", "retrieve", "start",
                        "--manifest", m, "--target-org", a.org,
                        "--target-metadata-dir", dest, "--wait", a.wait], a.dry_run)
        secs = round(time.time() - t0, 1)
        if a.dry_run:
            continue
        if code != 0:
            tail = (err.strip().splitlines() or [""])[-1][:200]
            print(f"   [FAIL] after {secs}s  {tail}")
            failed.append(label)
            continue

        files, size = measure(dest)
        mb = size / 1_000_000
        flag = ""
        if files > HARD_FILES:
            flag = f"  !! over the {HARD_FILES:,}-file cap"
        elif mb > HARD_MB:
            flag = f"  !! over {HARD_MB} MB"
        print(f"   [OK] {files:,} files, {mb:.1f} MB, {secs}s{flag}")
        row = {"manifest": m.name, "files": files, "bytes": size, "seconds": secs}
        done.write_text(json.dumps(row), encoding="utf-8")
        results.append(row)

    if a.dry_run:
        print("\n[DONE] dry run - nothing retrieved")
        return

    if results:
        Path("calibration.json").write_text(
            json.dumps({"chunks": results}, indent=1), encoding="utf-8")
        tf = sum(r["files"] for r in results)
        tm = sum(r["bytes"] for r in results) / 1_000_000
        print(f"\n--- Retrieval Complete ---")
        print(f"  Succeeded: {len(results)} (of which {skipped} skipped)")
        print(f"  Failed:    {len(failed)}")
        print(f"  Files:     {tf:,}  ({tm:.1f} MB)")
        print(f"  Largest:   {max(r['files'] for r in results):,} files")
        print("  wrote calibration.json - feed the real numbers into the next split")
    if failed:
        print(f"\n[WARN] {len(failed)} failed: {', '.join(failed)}")
        print("       Re-run with --resume to retry only those.")
        sys.exit(1)
    print("\n[DONE] All manifests retrieved.")


if __name__ == "__main__":
    main()
