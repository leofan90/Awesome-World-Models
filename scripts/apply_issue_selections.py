#!/usr/bin/env python3
"""Insert checked arXiv Issue candidates into the appropriate README sections."""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from scripts.arxiv_candidates import TARGET_SECTIONS, extract_readme_arxiv_ids, normalize_arxiv_id

CANDIDATE_PATTERN = re.compile(
    r"^- \[([ xX])\] Add to `([^`]+)`: .*?<!-- arxiv-candidate:([A-Za-z0-9_-]+) -->",
    re.MULTILINE,
)
PROPOSED_README_ENTRY_PATTERN = re.compile(
    r"- Proposed README entry:\r?\n```markdown\r?\n([^\r\n]+)\r?\n```"
)
ARXIV_ID_PATTERN = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})$", re.IGNORECASE)


@dataclass(frozen=True)
class AcceptedCandidate:
    arxiv_id: str
    title: str
    published: date
    section: str
    readme_entry: str | None = None


def decode_metadata(encoded_metadata: str) -> dict[str, str]:
    padding = "=" * (-len(encoded_metadata) % 4)
    decoded = base64.urlsafe_b64decode(encoded_metadata + padding)
    metadata = json.loads(decoded)
    if not isinstance(metadata, dict):
        raise ValueError("Candidate metadata must be a JSON object")
    return metadata


def parse_checked_candidates(issue_body: str) -> list[AcceptedCandidate]:
    candidates = []
    seen_ids = set()
    matches = list(CANDIDATE_PATTERN.finditer(issue_body))
    for index, match in enumerate(matches):
        checked, section, encoded_metadata = match.groups()
        if checked.lower() != "x":
            continue
        if section not in TARGET_SECTIONS:
            raise ValueError(f"Unsupported README section: {section}")
        metadata = decode_metadata(encoded_metadata)
        arxiv_id = normalize_arxiv_id(required_metadata(metadata, "arxiv_id"))
        if not ARXIV_ID_PATTERN.fullmatch(arxiv_id):
            raise ValueError(f"Invalid arXiv ID: {arxiv_id}")
        title = normalize_title(required_metadata(metadata, "title"))
        published = date.fromisoformat(required_metadata(metadata, "published"))
        next_match_start = matches[index + 1].start() if index + 1 < len(matches) else len(issue_body)
        candidate_body = issue_body[match.end() : next_match_start]
        readme_entry = extract_proposed_readme_entry(candidate_body, arxiv_id)
        if arxiv_id not in seen_ids:
            candidates.append(
                AcceptedCandidate(
                    arxiv_id=arxiv_id,
                    title=title,
                    published=published,
                    section=section,
                    readme_entry=readme_entry,
                )
            )
            seen_ids.add(arxiv_id)
    return candidates


def required_metadata(metadata: dict[str, str], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing candidate metadata: {key}")
    return value


def normalize_title(title: str) -> str:
    return " ".join(title.split())


def extract_proposed_readme_entry(candidate_body: str, arxiv_id: str) -> str | None:
    match = PROPOSED_README_ENTRY_PATTERN.search(candidate_body)
    if match is None:
        return None
    readme_entry = match.group(1).strip()
    if not readme_entry.startswith("* "):
        raise ValueError(f"Proposed README entry must start with '* ': {arxiv_id}")
    if arxiv_id not in extract_readme_arxiv_ids(readme_entry):
        raise ValueError(f"Proposed README entry must link to arXiv ID: {arxiv_id}")
    return readme_entry


def proposed_readme_entry(candidate: AcceptedCandidate) -> str:
    if candidate.readme_entry:
        return candidate.readme_entry
    month = candidate.published.strftime("%Y.%m")
    return (
        f'* "{candidate.title}", **`arxiv {month}`**. '
        f"[[Paper](https://arxiv.org/abs/{candidate.arxiv_id})]"
    )


def apply_candidates(readme_text: str, candidates: list[AcceptedCandidate]) -> tuple[str, list[AcceptedCandidate]]:
    newline = "\r\n" if "\r\n" in readme_text else "\n"
    known_ids = extract_readme_arxiv_ids(readme_text)
    inserted_candidates = [candidate for candidate in candidates if candidate.arxiv_id not in known_ids]
    for section in TARGET_SECTIONS:
        section_candidates = [
            candidate for candidate in inserted_candidates if candidate.section == section
        ]
        if not section_candidates:
            continue
        heading = f"## {section}{newline}"
        heading_index = readme_text.find(heading)
        if heading_index == -1:
            raise ValueError(f"README section not found: {section}")
        insertion_index = heading_index + len(heading)
        entries = "".join(
            f"{proposed_readme_entry(candidate)}{newline}" for candidate in section_candidates
        )
        readme_text = readme_text[:insertion_index] + entries + readme_text[insertion_index:]
    return readme_text, inserted_candidates


def write_summary(path: Path, inserted_candidates: list[AcceptedCandidate]) -> None:
    lines = ["## Added papers", ""]
    for candidate in inserted_candidates:
        lines.append(
            f"- [`{candidate.arxiv_id}`](https://arxiv.org/abs/{candidate.arxiv_id}) "
            f"to `{candidate.section}`: {candidate.title}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-body", type=Path, required=True)
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--summary-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    issue_body = args.issue_body.read_text(encoding="utf-8")
    readme_text = args.readme.read_bytes().decode("utf-8")
    candidates = parse_checked_candidates(issue_body)
    updated_readme, inserted_candidates = apply_candidates(readme_text, candidates)
    if inserted_candidates:
        args.readme.write_bytes(updated_readme.encode("utf-8"))
    if args.summary_output:
        write_summary(args.summary_output, inserted_candidates)
    print(len(inserted_candidates))
    return 0


if __name__ == "__main__":
    sys.exit(main())
