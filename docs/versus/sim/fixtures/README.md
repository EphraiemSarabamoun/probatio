# Interchange fixtures

The full-output files in catalog.json include losslessly encoded complete
planned reply tables, exact expected records, frozen invented referents and
sentence output. fixture_io.unpack_rows decodes row_table into ordinary
reference dictionaries. The encoding is a column list and zlib/base85 JSON
row values; row_count checks truncation. Refusal fixtures pin the plain error.

These fixtures pin Python's random.Random(seed).choice stream, in canonical
frame/family/ID order. Replay uses 128 draws; the inferential studies use
10,000. Another runtime must reproduce that stream or supply the same draws
when comparing interval endpoints. This is explicit, not PRNG independence.

boundaries.json contains 32 full-grid guard-neighbor tables with exact expected
eligibility records. replicate_boundaries.json adds the 9,500/9,499 defined
replicate recipe and expected claim outputs on a complete standard table.
The 25_boundary files contain real tables adjacent by one changed run at
both joint-interval sign thresholds, for both metrics and both page shapes.
The mutation table in ../results/repair_mutations.csv names the fixture/test
that catches each formerly undetected mutation.
