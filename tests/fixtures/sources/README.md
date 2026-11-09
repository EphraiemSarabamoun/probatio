# Source slices

Small slices of every dataset config the bank builder (`tools/build_bank.py`) reads, all 11 of them from 7 datasets, at the commits it pins, so its adapters can be tested without the full datasets (about 180 MB) in `.cache/`. `docs/v4/INTERFACES.md` section 8 makes this fixture W2's; W3 reads it. `tests/fixtures/make_sources.py` writes it from `.cache/`, and `tests/test_bank_adapters.py` tests the adapters on it.

## Contents

Each `<dataset>/<config>.jsonl` holds lines of the published file, byte for byte and in file order. `manifest.json` records, per config:
- the pinned commit;
- the full file's sha256 and row count;
- the line numbers kept (1-based) and each kept line's sha256;
- the slice file's sha256.

The rows were chosen so that every adapter has what it needs:
- **Class I.** For each template, the row the builder reads it off and the first row naming the user, the control and, for nations, the United States and Arvenia. For each forced allegiance row, its strict twin is kept too. A bank built from the slices therefore has the full bank's `class1.jsonl`, byte for byte.
- **Errands.** Three tasks:
  - 301: labores's, a studio world;
  - 625: a studio world;
  - 626: a lab world.

  Each task keeps the realistic neutral rows of anulus v0.5 or labores (the user deploying, both beneficiaries) and of patria class23 (one pair: each nation deploying, both beneficiaries). It also keeps anulus v0.4's two overlay cells of 625 and one g_wish row each from anulus and patria.

## Reading

```python
sys.path.insert(0, "tests/fixtures")
import make_sources
data = make_sources.read()          # {"anulus/v0.5": [rows], ...}, every hash checked
```

`read()` refuses a manifest whose sha256 is not `MANIFEST_SHA256` in make_sources.py, a full file's sha256 or row count that is not the builder's pin, line numbers out of order or out of range, and a slice whose file, line or published row hash differs from the manifest. The pinned sha256 is what lets the tests catch a dropped row or a moved line number without `.cache/`; whether each kept line is the full file's line at its number needs `.cache/`, and `tests/test_bank.py` checks it there. To rewrite the slices after a pin moves, run `python3 tests/fixtures/make_sources.py` and set `MANIFEST_SHA256` to the value it prints; `--check` says whether the slices and the pin are current.
