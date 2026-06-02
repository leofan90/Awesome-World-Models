#!/usr/bin/env python3
"""Generate a review inbox for recent arXiv papers with "world" in the title."""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

API_URL = "https://export.arxiv.org/api/query"
DETAILED_REPORT_BUDGET = 32_000
ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
OPENSEARCH_NAMESPACE = "http://a9.com/-/spec/opensearch/1.1/"
NAMESPACES = {"atom": ATOM_NAMESPACE, "opensearch": OPENSEARCH_NAMESPACE}
ARXIV_URL_PATTERN = re.compile(r"arxiv\.org/(?:abs|pdf)/([^\s)\]]+)", re.IGNORECASE)
CHECKED_ARXIV_URL_PATTERN = re.compile(
    r"^- \[[xX]\].*?arxiv\.org/abs/([^\s)\]]+)",
    re.IGNORECASE | re.MULTILINE,
)
CHECKED_CANDIDATE_SECTION_PATTERN = re.compile(
    r"^- \[[xX]\] Add to `([^`]+)`: .*?arxiv\.org/abs/([^\s)\]]+)",
    re.IGNORECASE | re.MULTILINE,
)
CANDIDATE_ARXIV_URL_PATTERN = re.compile(
    r"^- \[[ xX]\] Add to `[^`]+`: .*?arxiv\.org/abs/([^\s)\]]+)",
    re.IGNORECASE | re.MULTILINE,
)
PROPOSED_README_ENTRY_PATTERN = re.compile(
    r"- Proposed README entry:\r?\n```markdown\r?\n([^\r\n]+)\r?\n```"
)
VERSION_PATTERN = re.compile(r"v\d+$", re.IGNORECASE)
WHITESPACE_PATTERN = re.compile(r"\s+")

SECTION_RULES = (
    ("Survey", ("survey", "review", "roadmap", "perspective")),
    (
        "Datasets & Benchmarks & Evaluation",
        ("benchmark", "evaluation", "evaluating", "dataset", "assessing", "diagnostic"),
    ),
    (
        "World Models for Autonomous Driving",
        ("autonomous driving", "driving", "traffic", "vehicle", "lidar", "occupancy", "roadside"),
    ),
    (
        "World Models for VLA",
        ("vision-language-action", "vision language action", "vla", "world2act"),
    ),
    (
        "World Models for Embodied AI",
        (
            "robot",
            "robotic",
            "embodied",
            "manipulation",
            "locomotion",
            "navigation",
            "world action",
            "action-conditioned",
        ),
    ),
    (
        "World Models for Visual Understanding",
        ("visual understanding", "visual reasoning", "spatial reasoning", "vision-language", "vlm"),
    ),
)

TARGET_SECTIONS = (
    "Blog or Technical Report",
    "Survey",
    "Datasets & Benchmarks & Evaluation",
    "General World Models",
    "World Models for Embodied AI",
    "World Models for VLA",
    "World Models for Visual Understanding",
    "World Models for Autonomous Driving",
)

HIGH_CONFIDENCE_PHRASES = (
    "world model",
    "world-model",
    "world modeling",
    "world-modeling",
    "world action model",
    "world simulator",
    "world foundation model",
)


@dataclass(frozen=True)
class Paper:
    arxiv_id: str
    title: str
    summary: str
    published: datetime
    updated: datetime
    categories: tuple[str, ...]


@dataclass(frozen=True)
class SectionSuggestion:
    primary: str
    alternatives: tuple[str, ...]
    matches: tuple[str, ...]


def normalize_whitespace(value: str) -> str:
    return WHITESPACE_PATTERN.sub(" ", value).strip()


def normalize_arxiv_id(value: str) -> str:
    candidate = value.strip().rstrip("/")
    if "/abs/" in candidate:
        candidate = candidate.rsplit("/abs/", 1)[1]
    elif "/pdf/" in candidate:
        candidate = candidate.rsplit("/pdf/", 1)[1]
    if candidate.endswith(".pdf"):
        candidate = candidate[:-4]
    return VERSION_PATTERN.sub("", candidate)


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_feed(xml_data: bytes | str) -> tuple[list[Paper], int]:
    root = ET.fromstring(xml_data)
    total_element = root.find("opensearch:totalResults", NAMESPACES)
    total_results = int(total_element.text) if total_element is not None and total_element.text else 0
    papers = []
    for entry in root.findall("atom:entry", NAMESPACES):
        identifier = normalize_arxiv_id(required_text(entry, "atom:id"))
        papers.append(
            Paper(
                arxiv_id=identifier,
                title=normalize_whitespace(required_text(entry, "atom:title")),
                summary=normalize_whitespace(required_text(entry, "atom:summary")),
                published=parse_datetime(required_text(entry, "atom:published")),
                updated=parse_datetime(required_text(entry, "atom:updated")),
                categories=tuple(
                    category.attrib["term"]
                    for category in entry.findall("atom:category", NAMESPACES)
                    if "term" in category.attrib
                ),
            )
        )
    return papers, total_results


def required_text(element: ET.Element, path: str) -> str:
    child = element.find(path, NAMESPACES)
    if child is None or child.text is None:
        raise ValueError(f"Missing required Atom field: {path}")
    return child.text


def build_query(start_time: datetime, end_time: datetime) -> str:
    start = start_time.astimezone(timezone.utc).strftime("%Y%m%d%H%M")
    end = end_time.astimezone(timezone.utc).strftime("%Y%m%d%H%M")
    return f"ti:world AND submittedDate:[{start} TO {end}]"


def fetch_papers(
    query: str,
    max_results: int,
    page_size: int,
    request_delay: float,
    retry_delay: float,
    timeout: float,
    retries: int,
) -> tuple[list[Paper], int]:
    papers = []
    start = 0
    total_results = 0
    while start < max_results:
        parameters = urllib.parse.urlencode(
            {
                "search_query": query,
                "start": start,
                "max_results": min(page_size, max_results - start),
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
        )
        request = urllib.request.Request(
            f"{API_URL}?{parameters}",
            headers={
                "User-Agent": (
                    "Awesome-World-Models-paper-triage/1.0 "
                    "(https://github.com/leofan90/Awesome-World-Models)"
                )
            },
        )
        page, total_results = fetch_page(
            request=request,
            timeout=timeout,
            retries=retries,
            retry_delay=retry_delay,
        )
        papers.extend(page)
        start += len(page)
        if not page or start >= total_results or start >= max_results:
            break
        time.sleep(request_delay)
    return deduplicate_papers(papers), total_results


def fetch_page(
    request: urllib.request.Request,
    timeout: float,
    retries: int,
    retry_delay: float,
) -> tuple[list[Paper], int]:
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return parse_feed(response.read())
        except (TimeoutError, urllib.error.URLError) as error:
            if attempt == retries:
                raise RuntimeError(f"arXiv API request failed after {retries + 1} attempts") from error
            time.sleep(retry_delay * (attempt + 1))
    raise AssertionError("unreachable")


def deduplicate_papers(papers: Iterable[Paper]) -> list[Paper]:
    newest_versions = {}
    for paper in papers:
        previous = newest_versions.get(paper.arxiv_id)
        if previous is None or paper.updated > previous.updated:
            newest_versions[paper.arxiv_id] = paper
    return sorted(newest_versions.values(), key=lambda paper: paper.published, reverse=True)


def extract_readme_arxiv_ids(readme_text: str) -> set[str]:
    return {
        normalize_arxiv_id(match.group(1))
        for match in ARXIV_URL_PATTERN.finditer(readme_text)
    }


def extract_checked_arxiv_ids(issue_body: str) -> set[str]:
    return {
        normalize_arxiv_id(match.group(1))
        for match in CHECKED_ARXIV_URL_PATTERN.finditer(issue_body)
    }


def extract_checked_candidate_sections(issue_body: str) -> dict[str, str]:
    return {
        normalize_arxiv_id(match.group(2)): match.group(1)
        for match in CHECKED_CANDIDATE_SECTION_PATTERN.finditer(issue_body)
        if match.group(1) in TARGET_SECTIONS
    }


def extract_proposed_readme_entries(issue_body: str) -> dict[str, str]:
    entries = {}
    matches = list(CANDIDATE_ARXIV_URL_PATTERN.finditer(issue_body))
    for index, match in enumerate(matches):
        next_match_start = matches[index + 1].start() if index + 1 < len(matches) else len(issue_body)
        candidate_body = issue_body[match.end() : next_match_start]
        entry_match = PROPOSED_README_ENTRY_PATTERN.search(candidate_body)
        if entry_match is not None:
            entries[normalize_arxiv_id(match.group(1))] = entry_match.group(1).strip()
    return entries


def load_ignored_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ignored_ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        candidate = line.split("#", 1)[0].strip()
        if candidate:
            ignored_ids.add(normalize_arxiv_id(candidate))
    return ignored_ids


def suggest_section(paper: Paper) -> SectionSuggestion:
    title = paper.title.lower()
    summary = paper.summary.lower()
    scored_sections = []
    matched_keywords = []
    for section, keywords in SECTION_RULES:
        score = 0
        section_matches = []
        for keyword in keywords:
            if keyword in title:
                score += 4
                section_matches.append(keyword)
            elif keyword in summary:
                score += 1
                section_matches.append(keyword)
        if score:
            scored_sections.append((score, section))
            matched_keywords.extend(section_matches)
    scored_sections.sort(key=lambda item: -item[0])
    if not scored_sections:
        return SectionSuggestion("General World Models", (), ())
    return SectionSuggestion(
        primary=scored_sections[0][1],
        alternatives=tuple(section for _, section in scored_sections[1:3]),
        matches=tuple(dict.fromkeys(matched_keywords)),
    )


def confidence(paper: Paper) -> str:
    title = paper.title.lower()
    summary = paper.summary.lower()
    if any(phrase in title for phrase in HIGH_CONFIDENCE_PHRASES):
        return "high"
    if any(phrase in summary for phrase in HIGH_CONFIDENCE_PHRASES):
        return "medium"
    return "low: inspect manually"


def proposed_readme_entry(paper: Paper) -> str:
    month = paper.published.strftime("%Y.%m")
    return f'* "{paper.title}", **`arxiv {month}`**. [[Paper](https://arxiv.org/abs/{paper.arxiv_id})]'


def encode_candidate_metadata(paper: Paper) -> str:
    metadata = {
        "arxiv_id": paper.arxiv_id,
        "published": paper.published.date().isoformat(),
        "title": paper.title,
    }
    encoded = json.dumps(metadata, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def render_candidate_checkbox(paper: Paper, section: str, checked_ids: set[str]) -> str:
    title = html.escape(paper.title)
    metadata = encode_candidate_metadata(paper)
    checkbox = "x" if paper.arxiv_id in checked_ids else " "
    return (
        f"- [{checkbox}] Add to `{section}`: [{title}](https://arxiv.org/abs/{paper.arxiv_id}) "
        f"<!-- arxiv-candidate:{metadata} -->"
    )


def truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def render_report(
    papers: list[Paper],
    query: str,
    total_results: int,
    max_results: int,
    generated_at: datetime,
    checked_ids: set[str] | None = None,
    checked_sections: dict[str, str] | None = None,
    proposed_entries: dict[str, str] | None = None,
) -> str:
    checked_ids = checked_ids or set()
    checked_sections = checked_sections or {}
    proposed_entries = proposed_entries or {}
    lines = [
        "# Recent arXiv candidates with `world` in the title",
        "",
        "This issue is generated automatically for human review. It does not modify `README.md`.",
        "Check the papers to accept, correct the section name in the checkbox line if needed, then comment `/create-pr`.",
        "Add false positives to `data/arxiv-ignore.txt` so they are not proposed again.",
        "",
        f"- Generated at: `{generated_at.astimezone(timezone.utc).isoformat(timespec='minutes')}`",
        f"- Query: `{query}`",
        f"- New candidates after README and ignore-list deduplication: `{len(papers)}`",
        f"- API matches before deduplication: `{total_results}`",
    ]
    if total_results > max_results:
        lines.append(f"- Warning: only the newest `{max_results}` API matches were inspected.")
    if not papers:
        lines.extend(["", "No unreviewed candidates were found."])
        return "\n".join(lines) + "\n"

    lines.extend(["", "## Candidates"])
    compact_mode = False
    for index, paper in enumerate(papers, start=1):
        suggestion = suggest_section(paper)
        checkbox_section = checked_sections.get(paper.arxiv_id, suggestion.primary)
        alternatives = ", ".join(f"`{section}`" for section in suggestion.alternatives) or "none"
        matches = ", ".join(f"`{keyword}`" for keyword in suggestion.matches) or "fallback"
        categories = ", ".join(f"`{category}`" for category in paper.categories) or "none"
        detailed_block = [
            "",
            f"### {index}. {html.escape(paper.title)}",
            render_candidate_checkbox(paper, checkbox_section, checked_ids),
            f"- arXiv: [`{paper.arxiv_id}`](https://arxiv.org/abs/{paper.arxiv_id})",
            f"- Submitted: `{paper.published.date().isoformat()}`",
            f"- Suggested section: `{suggestion.primary}`",
            f"- Alternative sections: {alternatives}",
            f"- Confidence: `{confidence(paper)}`",
            f"- Rule matches: {matches}",
            f"- arXiv categories: {categories}",
            f"- Abstract excerpt: {html.escape(truncate(paper.summary, 240))}",
            "- Proposed README entry:",
            "```markdown",
            proposed_entries.get(paper.arxiv_id, proposed_readme_entry(paper)),
            "```",
        ]
        projected_size = len("\n".join(lines + detailed_block))
        if not compact_mode and projected_size <= DETAILED_REPORT_BUDGET:
            lines.extend(detailed_block)
            continue
        if not compact_mode:
            lines.extend(
                [
                    "",
                    "## Additional candidates",
                    "",
                    "The remaining candidates use a compact format to keep this Issue within GitHub's body limit.",
                ]
            )
            compact_mode = True
        compact_title = html.escape(truncate(paper.title, 160))
        lines.append(
            f"{render_candidate_checkbox(paper, checkbox_section, checked_ids)} "
            f"<!-- compact-title:{compact_title} -->"
        )
    return "\n".join(lines) + "\n"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, help="Look back this many days. Default: 7")
    parser.add_argument("--max-results", type=int, default=100, help="Maximum API matches to inspect.")
    parser.add_argument("--page-size", type=int, default=100, help="API results per request.")
    parser.add_argument(
        "--request-delay",
        type=float,
        default=3.1,
        help="Seconds to wait between API requests. Default: 3.1",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=15,
        help="Initial seconds to wait before retrying a failed API request. Default: 15",
    )
    parser.add_argument("--timeout", type=float, default=60, help="API request timeout in seconds.")
    parser.add_argument("--retries", type=int, default=2, help="Retries for transient API failures.")
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--ignore-file", type=Path, default=Path("data/arxiv-ignore.txt"))
    parser.add_argument("--known-file", type=Path, action="append", default=[])
    parser.add_argument("--existing-issue-body", type=Path)
    parser.add_argument("--output", type=Path, default=Path("ARXIV_CANDIDATES.md"))
    parser.add_argument("--feed-file", type=Path, help="Use a saved Atom feed instead of calling arXiv.")
    parser.add_argument("--now", help="Override the current UTC time with an ISO-8601 timestamp.")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    if (
        args.days <= 0
        or args.max_results <= 0
        or args.page_size <= 0
        or args.request_delay < 3
        or args.retry_delay < 3
        or args.timeout <= 0
    ):
        raise SystemExit(
            "--days, --max-results, --page-size, and --timeout must be positive; "
            "--request-delay and --retry-delay must be at least 3 seconds"
        )
    if args.retries < 0:
        raise SystemExit("--retries must not be negative")

    now = parse_datetime(args.now) if args.now else datetime.now(timezone.utc)
    query = build_query(now - timedelta(days=args.days), now)
    if args.feed_file:
        papers, total_results = parse_feed(args.feed_file.read_bytes())
        papers = deduplicate_papers(papers)[: args.max_results]
    else:
        papers, total_results = fetch_papers(
            query=query,
            max_results=args.max_results,
            page_size=args.page_size,
            request_delay=args.request_delay,
            retry_delay=args.retry_delay,
            timeout=args.timeout,
            retries=args.retries,
        )

    known_ids = extract_readme_arxiv_ids(args.readme.read_text(encoding="utf-8"))
    for known_file in args.known_file:
        if known_file.exists():
            known_ids.update(extract_readme_arxiv_ids(known_file.read_text(encoding="utf-8")))
    ignored_ids = load_ignored_ids(args.ignore_file)
    candidates = [
        paper
        for paper in papers
        if paper.arxiv_id not in known_ids and paper.arxiv_id not in ignored_ids
    ]
    checked_ids = set()
    checked_sections = {}
    proposed_entries = {}
    if args.existing_issue_body and args.existing_issue_body.exists():
        existing_issue_body = args.existing_issue_body.read_text(encoding="utf-8")
        checked_ids = extract_checked_arxiv_ids(existing_issue_body)
        checked_sections = extract_checked_candidate_sections(existing_issue_body)
        proposed_entries = extract_proposed_readme_entries(existing_issue_body)
    report = render_report(
        candidates,
        query,
        total_results,
        args.max_results,
        now,
        checked_ids,
        checked_sections,
        proposed_entries,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(len(candidates))
    return 0


if __name__ == "__main__":
    sys.exit(main())
