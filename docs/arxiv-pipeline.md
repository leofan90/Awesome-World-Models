# arXiv candidate pipeline

This repository uses a reviewable automation pipeline for recent papers whose titles contain `world`.
The pipeline keeps human verification in the loop because title matches and heuristic categories still need review.

## How it works

1. `.github/workflows/arxiv-candidates.yml` runs once per day and can also be started manually.
2. `scripts/arxiv_candidates.py` queries the arXiv API for papers with `world` in the title from the last three days.
3. The script removes papers already linked from `README.md` and IDs listed in `data/arxiv-ignore.txt`.
4. Keyword rules suggest the most likely README section and produce a checkbox for each candidate.
5. The workflow creates or updates one open GitHub Issue labeled `arxiv-candidates`.
6. After a maintainer checks accepted papers and comments `/create-pr`, `.github/workflows/arxiv-candidates-create-pr.yml` opens a PR that updates `README.md`.

The single Issue acts as an inbox. Open PRs created by this pipeline are excluded from later inbox refreshes, so pending papers are not proposed repeatedly. Inbox refreshes preserve checked papers, corrected categories, and edited `Proposed README entry` lines.

## Review an inbox

1. Open the Issue labeled `arxiv-candidates`.
2. Review each candidate and check the papers that should be included.
3. If needed, edit the category name in the checkbox line or refine its `Proposed README entry`.
4. Comment `/create-pr`.
5. Review and merge the generated PR.

The generated PR adds accepted papers to the top of the selected README sections. The inbox Issue is closed after the PR is opened. Unchecked candidates return in the next refresh.

Add unrelated title matches to `data/arxiv-ignore.txt` so they are not proposed again.

## Repository setting

The comment-triggered workflow uses `GITHUB_TOKEN` to push a branch and open a PR. Enable **Allow GitHub Actions to create and approve pull requests** under **Settings > Actions > General > Workflow permissions**.

## Run locally

```bash
python3 scripts/arxiv_candidates.py --days 7
```

The generated report is written to `ARXIV_CANDIDATES.md`, which is ignored by Git.

To test with a saved Atom feed instead of making a network request:

```bash
python3 scripts/arxiv_candidates.py --feed-file path/to/feed.xml
```

## Tuning

The section suggestions are intentionally conservative. Update `SECTION_RULES` in `scripts/arxiv_candidates.py` when the repository taxonomy changes or when recurring classification mistakes appear.

Use `--max-results` to inspect a larger API result set. If pagination is needed, the script waits at least 3.1 seconds between requests. Transient API failures are retried twice with a longer backoff by default; tune this with `--timeout`, `--retries`, and `--retry-delay`.

## arXiv API

The pipeline uses the public arXiv API described in the [API User's Manual](https://info.arxiv.org/help/api/user-manual.html) and follows the [API Terms of Use](https://info.arxiv.org/help/api/tou.html).

Thank you to arXiv for use of its open access interoperability.
