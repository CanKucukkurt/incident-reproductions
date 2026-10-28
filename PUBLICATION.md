# Publication notes

This release selects files from three research snapshots rather than exposing their
Git histories. The source revision IDs are compaction `e158f526873f976725bb2417c5c671fb7ad5b7c4`,
citation `5a4e5d023999389a62caf6ffbd970064383fe592` and portal/wiki `63cb8783bb00452da6b777088b8eafd5f772a83f`. File-level source hashes are recorded in
PROVENANCE.json; final exported hashes are recorded in SHA256SUMS.json.

Publication edits:

- Replaced collection and runtime navigation with standalone public instructions.
- Removed unrelated campaign implementations, planning notes, chats, notebooks,
  archived handoffs, upload metadata, and local/private source inventories.
- Retained shared World support code, including counter URL handling, because portal
  and wiki import it. No unrelated counter campaign is shipped.
- Replaced workstation roots in portal judgments with relative historical artifact IDs.
  Those IDs identify unbundled evidence; they are not links to downloadable run files.
- Removed unrelated package console entry points. Retained experiment spec-builder
  dependencies and added a standalone wiki launcher using the same builders and episode loop.
- Changed legacy builder launchers' neighboring `.env` lookup to this project's `.env`.
- Removed an upstream spreadsheet author’s local directory metadata from `COA.xlsx`;
  workbook cell contents are unchanged.
- Added dependency snapshots for validated installs.
- Corrected citation package Python metadata to >=3.11, matching its agent dependencies.
- Replaced the root source inventory with provenance covering released files only.
- Compaction aggregate CSVs are supplemental local analysis snapshots, not part of
  the pinned source commit. Their source hashes are recorded separately.

Prompts, routing, deterministic tool behavior, scheduling, clocks, scoring, and historical
judgment values are preserved. Launcher and packaging edits do not establish identical
historical provider behavior. No live model calls or transcript uploads are needed for
release checks. Historical aggregates cannot all be independently regenerated from
this release because full raw trajectories are not included.

A license decision remains pending. Public visibility is not an open-source license.

The retained mixed authentication test file excludes its unrelated color-campaign tests.
Historical environment-prompt tests read an extracted fixture from original commit
`b8d284c` rather than requiring private Git history. Docent conversion support remains
for existing runtime checks; no collection IDs, upload records, or private transcripts
are shipped. Additional sparse-condition configs support the retained mechanics tests.
