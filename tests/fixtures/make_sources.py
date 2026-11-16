"""Write tests/fixtures/sources/: small slices of every pinned dataset config the bank builder reads (the 11 configs
of tools/build_bank.py's SOURCES, from 7 datasets; INTERFACES.md section 8, owned by W2, read by W3), so the
builder's adapters can be tested without the full datasets in .cache.

    python3 tests/fixtures/make_sources.py           # rewrites the folder from .cache (the full pinned configs)
    python3 tests/fixtures/make_sources.py --check   # exit 1 unless the folder is what a rewrite would give

A slice is a subset of a config's lines, byte for byte and in file order, chosen so that every adapter has what it
needs and every Class I template is read off the same row it is read off in the full build:
- Class I (sacramentum, par-potestas, devotio, salus-populi, patria class1): for each template, the row the builder
  reads it off (the first that can define it), and the first row naming the user, the control and, for nations, the
  United States and Arvenia; for each forced allegiance row, its strict twin (the forced instruction is read off the
  pair). So a build from the slices gives the full build's class1.jsonl, byte for byte.
- Errands: three tasks, 301 (labores's, a studio world), 625 (studio) and 626 (lab). anulus v0.5's realistic neutral
  rows of 625 and 626 with the user deploying, both beneficiaries, and one g_wish row; anulus v0.4's two overlay cells
  of task 625; labores's two rows of 301; patria class23's realistic neutral rows of each task for the pair its first
  such row names (each nation deploying, both beneficiaries), and one g_wish row.

manifest.json records, per config, the pinned commit, the full file's sha256 and row count, the line numbers kept
(1-based), each kept line's sha256 and the slice file's sha256. Without the full files, read() checks what can be
checked: the manifest's own sha256 against MANIFEST_SHA256, the commit, file sha256 and row count against the
builder's SOURCES, the line numbers increasing within the file, each slice file's and line's sha256, and every row's
published hashes, as the builder's read_config does for a full file. That a kept line is the full file's line at its
number needs .cache, and tests/test_bank.py's TestSourceSlices checks it. Standard library only.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OUT = HERE / "sources"
sys.path.insert(0, str(ROOT / "tools"))
import build_bank as B  # noqa: E402

TASKS = {"anulus/v0.5": (625, 626), "anulus/v0.4": (625,), "labores/refunds": (301,), "patria/class23": (301, 625, 626)}
PERSON_SIDES = (B.YOU, "nassar")          # the user and the control, as the person datasets key them
NATION_SIDES = ("usa", "arvenia")
# sources/manifest.json's sha256 as last written. read() refuses another, so a slice row dropped or a line number
# changed fails without .cache even when the rest of the manifest is resealed to match. Rewriting the slices on
# purpose means updating it: main() prints the new value.
MANIFEST_SHA256 = "d0f78cd3a70ceb7659c56d8d0cda0cf3dd0b2e5afcebc6dbdc88375656d258e9"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def select(data):
    """{dataset/config: sorted 0-based row indexes to keep} for the full pinned configs (B.read_all)."""
    where = {id(r): (k, i) for k, rows in data.items() for i, r in enumerate(rows)}
    keep = {k: set() for k in data}

    def take(row):
        k, i = where[id(row)]
        keep[k].add(i)

    def per_template(items, sides):
        """The defining row of each template (the first derivable one) and the first row naming each side."""
        done = set()
        for tid, _, _, _, row, who, derive in items:
            marks = [("rep",)] if derive else []
            marks += [("side", s) for s in sides if s in who.values()]
            for m in marks:
                if (tid,) + m not in done:
                    done.add((tid,) + m)
                    take(row)
    per_template(B.person_items(data), PERSON_SIDES)
    per_template([it for it, _, _ in B.nation_items(data)], NATION_SIDES)
    strict = {r["id"]: r for r in data["sacramentum/full-strict"]}
    for i in sorted(keep["sacramentum/full-forced"]):          # forced_tail reads the instruction off the twins
        take(strict[data["sacramentum/full-forced"][i]["id"][:-len("|forced")]])
    for r in data["anulus/v0.5"]:
        if r["principal_slot"] == "you" and r["realism"] == "realistic" and r["level"] == 3 and (
                r["arm"] == "neutral" and r["seed"] in TASKS["anulus/v0.5"]):
            take(r)
    take(next(r for r in data["anulus/v0.5"] if r["principal_slot"] == "you" and r["realism"] == "realistic"
              and r["arm"] == "g_wish"))
    cells = {("unaudited", "realistic"), ("audited", "disclosed")}
    for r in data["anulus/v0.4"]:
        if r["principal_slot"] == "you" and r["beneficiary"] == "principal" and r["seed"] in TASKS["anulus/v0.4"] \
                and (r["observation"], r["realism"]) in cells:
            take(r)
    for r in data["labores/refunds"]:
        if r["principal_slot"] == "you" and r["arm"] == "neutral" and r["seed"] in TASKS["labores/refunds"]:
            take(r)
    neutral = [r for r in data["patria/class23"] if r["condition"] == "neutral" and r["realism"] == "realistic"
               and r["beneficiary"] in ("principal", "partner")]
    for seed in TASKS["patria/class23"]:
        mine = [r for r in neutral if r["seed"] == seed]
        for r in mine:
            if r["pair"] == mine[0]["pair"]:          # the builder's source record cites a task's first row
                take(r)
    take(next(r for r in data["patria/class23"] if r["condition"] == "g_wish" and r["realism"] == "realistic"
              and r["deploying_nation"] in B.NATIONS))
    return {k: sorted(v) for k, v in keep.items()}


def lines_of(path):
    """A config file's lines, as bytes, the newline dropped (read_config's split on "\\n")."""
    return [x for x in path.read_bytes().split(b"\n") if x.strip()]


def build(cache, data):
    """{relative path: bytes} of the fixture folder."""
    files, configs = {}, {}
    for k, idx in select(data).items():
        dataset, config = k.split("/")
        full = B.config_path(cache, dataset, config)
        raw = lines_of(full)
        kept = [raw[i] for i in idx]
        body = b"".join(x + b"\n" for x in kept)
        files[f"{dataset}/{config}.jsonl"] = body
        configs[k] = {"commit": B.SOURCES[dataset][0], "file_sha256": sha(full.read_bytes()), "file_rows": len(raw),
                      "lines": [i + 1 for i in idx], "line_sha256": [sha(x) for x in kept], "slice_sha256": sha(body)}
    manifest = {"_about": "Slices of the pinned configs the bank builder reads; see make_sources.py and README.md.",
                "tasks": {k: list(v) for k, v in TASKS.items()}, "configs": configs}
    files["manifest.json"] = (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode("utf-8")
    return files


def read(folder=OUT, pinned=MANIFEST_SHA256):
    """The slices as the builder's data ({dataset/config: rows}), checked without the full files: the manifest's
    sha256 against pinned (when given); per config the commit, the full file's sha256 and row count against the
    builder's pins (B.SOURCES), the line numbers strictly increasing within 1 to the row count, the slice file's
    sha256, each line's, and every row's published prompt and world sha256 (build_bank.check_rows)."""
    folder = Path(folder)
    raw_manifest = (folder / "manifest.json").read_bytes()
    if pinned is not None and sha(raw_manifest) != pinned:
        raise SystemExit(f"{folder / 'manifest.json'}: sha256 {sha(raw_manifest)[:12]}, but make_sources.py pins "
                         f"{pinned[:12]}; rewrite the slices with make_sources.py and update MANIFEST_SHA256")
    manifest = json.loads(raw_manifest)
    data = {}
    for k, m in manifest["configs"].items():
        dataset, config = k.split("/")
        if B.SOURCES[dataset][0] != m["commit"]:
            raise SystemExit(f"{k}: sliced from {m['commit'][:8]}, but the builder pins {B.SOURCES[dataset][0][:8]}")
        rows_pinned, sha_pinned = B.SOURCES[dataset][1][config]
        if (m["file_sha256"], m["file_rows"]) != (sha_pinned, rows_pinned):
            raise SystemExit(f"{k}: the manifest's full file ({m['file_rows']} rows, sha256 {m['file_sha256'][:12]}) "
                             f"is not the one the builder pins ({rows_pinned} rows, sha256 {sha_pinned[:12]})")
        lines = m["lines"]
        if lines != sorted(set(lines)) or not all(isinstance(n, int) and 1 <= n <= rows_pinned for n in lines):
            raise SystemExit(f"{k}: the manifest's line numbers are not increasing within 1 to {rows_pinned}")
        body = (folder / dataset / f"{config}.jsonl").read_bytes()
        if sha(body) != m["slice_sha256"]:
            raise SystemExit(f"{k}: the slice file does not match its manifest")
        raw = lines_of(folder / dataset / f"{config}.jsonl")
        if [sha(x) for x in raw] != m["line_sha256"]:
            raise SystemExit(f"{k}: a slice line does not match its manifest")
        rows = [json.loads(x.decode("utf-8")) for x in raw]
        B.check_rows(rows, f"slice {k}", len(m["lines"]))
        data[k] = rows
    if set(data) != {f"{d}/{c}" for d, (_, cs) in B.SOURCES.items() for c in cs}:
        raise SystemExit("the slices do not cover every pinned config")
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", default=str(ROOT / ".cache"))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    files = build(a.cache, B.read_all(a.cache, download=False))
    now = sha(files["manifest.json"])
    if a.check:
        bad = [p for p, b in files.items() if not (OUT / p).exists() or (OUT / p).read_bytes() != b]
        print("the slices are current" if not bad else f"the slices differ in: {', '.join(bad)}")
        if now != MANIFEST_SHA256:
            print(f"MANIFEST_SHA256 is not the current manifest's sha256 ({now})")
        return 1 if bad or now != MANIFEST_SHA256 else 0
    for rel, body in files.items():
        (OUT / rel).parent.mkdir(parents=True, exist_ok=True)
        (OUT / rel).write_bytes(body)
    print(f"wrote {len(files)} files to {OUT}, {sum(len(b) for b in files.values()) // 1024} KB")
    if now != MANIFEST_SHA256:
        print(f"set MANIFEST_SHA256 in make_sources.py to {now}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
