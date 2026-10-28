# Release validation

Validated October 28, 2026 using fresh dependency environments in the curated export.

| Check | Result |
| --- | --- |
| Compaction tests, Python 3.11 | 95 passed |
| Citation tests, Python 3.11 | 47 passed |
| Portal/wiki tests, Python 3.13 | 348 passed |
| Compaction mock prepare/run/dry-run | Passed; 8 summaries and 96 mock continuations |
| Portal context preview | Passed without model calls |
| Wiki matched-condition preview | Passed without model calls |
| Public wiki launcher | Dry run and two-agent stub-model runs tested end to end |
| Public wiki input download | Downloaded from public source; all pinned hashes verified |
| Citation initial-context preview | Passed without model calls |
| Citation Docker environment smoke | Passed; shell/browser separation, read-only public requests |
| Citation Docker submission smoke | Passed; fabricated references rejected, public source accepted, plain-text final remains unaccepted |
| Secret scan | Gitleaks v8.30.1: no secrets found; checksum-manifest digest false positives excluded |
| Nested asset scan | Checked workbook ZIP entries and decoded planted fixtures for secret patterns and private machine paths |
| Spreadsheet metadata cleanup | Only author directory metadata removed; other workbook entries byte-identical |

The release has a fresh Git history. No original branches, tags, archived conversations,
private transcript archives, or omitted-file inventories are imported. File-level
hash verification and environment checks run through GitHub Actions on pushes and PRs.

These checks validate packaging and selected environment mechanics. They do not
independently rejudge all historical behavioral evidence. No paid model calls,
remote transcript uploads, or full historical study regeneration were performed.
Docker smoke checks use scripted responses and public GET requests, with test
containers and volumes cleaned up afterward. Live model behavior and public websites
can change; the release does not promise exact replay of historical outcomes.

Dependency snapshots record the validated package versions. Local checks used macOS
arm64; Linux checks are configured in `.github/workflows/checks.yml`.

Original prompt whitespace, CSV line endings, and benchmark patches are preserved;
Git whitespace warnings on those imported assets are expected.

Fresh-clone review found an overly broad ignore rule that excluded three synthetic
compaction CSV inputs. The rule now targets only the downloaded shared-runtime
data directory. Release verification also requires every selected provenance path
to appear in the checksum inventory.
