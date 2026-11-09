"""Check the v4 bank against a fresh build, with one provenance exception (bank/README.md, "Provenance").

tools/build_bank.py --check compares every bank file with a fresh build byte for byte, manifest.json included. The
manifest's built_by is `git log -1 -- tools/build_bank.py`, and the development commit that built the pinned bank is not
in this repository's published history, so here a fresh build's built_by is another commit. This check is the one the
tests and docs/ordo/apply_ordo.sh run. It passes when every file matches a fresh build byte for byte, except that
manifest.json's built_by may name a commit outside this checkout's history, and then only if the manifest's
builder_sha256 is the sha256 of tools/build_bank.py as it is now. Any other difference fails. tools/build_bank.py is
frozen and never edited for this.

    python3 tools/check_v4_bank.py                # needs the pinned datasets in .cache/
"""
import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILDER = ROOT / "tools" / "build_bank.py"
BANK = ROOT / "bank"
COMMIT = re.compile(r"[0-9a-f]{40}")


def load_builder():
    """tools/build_bank.py as a module, without running its command line."""
    spec = importlib.util.spec_from_file_location("build_bank", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def in_history(commit, repo=ROOT):
    """True when commit is HEAD or an ancestor of it. A commit object can exist locally (a shared object store, another
    branch) and still lie outside the history this checkout publishes, so presence alone is not the test."""
    out = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, "HEAD"], capture_output=True)
    return out.returncode == 0


def problems(fresh, bank, builder_bytes, pretty, in_history=in_history):
    """Every way the bank folder differs from a fresh build (fresh: file name -> text, as build_bank.build returns it)
    beyond the provenance exception; [] when it passes. pretty is the builder's own JSON writer."""
    bank = Path(bank)
    bad = sorted(n for n, t in fresh.items()
                 if not (bank / n).is_file() or (bank / n).read_text(encoding="utf-8") != t)
    if bad != ["manifest.json"]:
        return [f"{n} differs from a fresh build" for n in bad]
    text = (bank / "manifest.json").read_text(encoding="utf-8")
    pinned, built = json.loads(text), json.loads(fresh["manifest.json"])
    commit = pinned.get("built_by")
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        return [f"manifest.json differs from a fresh build, and its built_by {commit!r} is not a commit id"]
    found = []
    if pretty(dict(built, built_by=commit)) != text:
        found.append("manifest.json differs from a fresh build in more than built_by")
    if in_history(commit):
        found.append(f"manifest.json's built_by {commit[:7]} is in this checkout's history, so a fresh build must "
                     "reproduce it")
    if pinned.get("builder_sha256") != hashlib.sha256(builder_bytes).hexdigest():
        found.append("manifest.json's builder_sha256 is not the sha256 of tools/build_bank.py")
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", default=str(ROOT / ".cache"), help="folder of <dataset>/<commit>/<config>.jsonl")
    a = ap.parse_args(argv)
    builder = load_builder()
    files, _ = builder.build(builder.read_all(a.cache, download=False))
    found = problems(files, BANK, BUILDER.read_bytes(), builder.pretty)
    if found:
        print("bank differs from a fresh build:\n" + "\n".join(f"- {p}" for p in found))
        return 1
    commit = json.loads((BANK / "manifest.json").read_text(encoding="utf-8"))["built_by"]
    exact = (BANK / "manifest.json").read_text(encoding="utf-8") == files["manifest.json"]
    print("bank matches a fresh build" if exact else
          f"bank matches a fresh build apart from built_by {commit[:7]}, a commit outside this history (the provenance "
          "exception; builder_sha256 matches tools/build_bank.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
