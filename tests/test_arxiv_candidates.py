from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.request import Request

from scripts.arxiv_candidates import (
    Paper,
    confidence,
    extract_readme_arxiv_ids,
    extract_checked_arxiv_ids,
    extract_checked_candidate_sections,
    extract_proposed_readme_entries,
    fetch_page,
    main,
    normalize_arxiv_id,
    parse_feed,
    proposed_readme_entry,
    render_report,
    suggest_section,
)


ATOM_FEED = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>2</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2605.00001v1</id>
    <updated>2026-05-30T00:00:00Z</updated>
    <published>2026-05-30T00:00:00Z</published>
    <title>DriveWorld: A World Model for Autonomous Driving</title>
    <summary>A driving simulator for autonomous vehicles.</summary>
    <category term="cs.CV"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2605.00002v2</id>
    <updated>2026-05-31T00:00:00Z</updated>
    <published>2026-05-29T00:00:00Z</published>
    <title>World Statistics for Regional Trade</title>
    <summary>An economics paper about international trade.</summary>
    <category term="econ.GN"/>
  </entry>
</feed>
"""


class ArxivCandidateTests(unittest.TestCase):
    def test_normalize_arxiv_id_removes_url_pdf_and_version(self):
        self.assertEqual(normalize_arxiv_id("https://arxiv.org/pdf/2605.12345v2.pdf"), "2605.12345")

    def test_parse_feed_and_extract_existing_ids(self):
        papers, total_results = parse_feed(ATOM_FEED)
        self.assertEqual(total_results, 2)
        self.assertEqual(papers[0].arxiv_id, "2605.00001")
        self.assertEqual(
            extract_readme_arxiv_ids("[[Paper](https://arxiv.org/abs/2605.00001)]"),
            {"2605.00001"},
        )

    def test_extract_checked_ids_preserves_review_state(self):
        body = "- [x] Add to `General World Models`: [Paper](https://arxiv.org/abs/2605.00001)"
        self.assertEqual(extract_checked_arxiv_ids(body), {"2605.00001"})
        self.assertEqual(
            extract_checked_candidate_sections(body),
            {"2605.00001": "General World Models"},
        )

    def test_extract_proposed_entry_preserves_manual_edit(self):
        edited_entry = (
            '* **DriveWorld**: "DriveWorld: A World Model for Autonomous Driving", '
            "**`arxiv 2026.05`**. [[Paper](https://arxiv.org/abs/2605.00001)] "
            "[[Code](https://example.com/code)]"
        )
        body = "\n".join(
            [
                "- [x] Add to `World Models for Autonomous Driving`: "
                "[DriveWorld](https://arxiv.org/abs/2605.00001)",
                "- Proposed README entry:",
                "```markdown",
                edited_entry,
                "```",
            ]
        )
        self.assertEqual(
            extract_proposed_readme_entries(body),
            {"2605.00001": edited_entry},
        )

    def test_driving_paper_gets_driving_suggestion(self):
        papers, _ = parse_feed(ATOM_FEED)
        suggestion = suggest_section(papers[0])
        self.assertEqual(suggestion.primary, "World Models for Autonomous Driving")
        self.assertEqual(confidence(papers[0]), "high")

    def test_low_confidence_match_is_kept_for_manual_review(self):
        papers, total_results = parse_feed(ATOM_FEED)
        report = render_report(
            [papers[1]],
            query="ti:world",
            total_results=total_results,
            max_results=100,
            generated_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        )
        self.assertIn("low: inspect manually", report)
        self.assertIn("World Statistics for Regional Trade", report)
        self.assertIn("- [ ] Add to `General World Models`", report)

    def test_report_preserves_checked_candidate(self):
        papers, total_results = parse_feed(ATOM_FEED)
        report = render_report(
            [papers[0]],
            query="ti:world",
            total_results=total_results,
            max_results=100,
            generated_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
            checked_ids={"2605.00001"},
        )
        self.assertIn("- [x] Add to `World Models for Autonomous Driving`", report)

    def test_report_preserves_manually_corrected_section(self):
        papers, total_results = parse_feed(ATOM_FEED)
        report = render_report(
            [papers[0]],
            query="ti:world",
            total_results=total_results,
            max_results=100,
            generated_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
            checked_ids={"2605.00001"},
            checked_sections={"2605.00001": "General World Models"},
        )
        self.assertIn("- [x] Add to `General World Models`", report)

    def test_report_preserves_manually_edited_readme_entry(self):
        papers, total_results = parse_feed(ATOM_FEED)
        edited_entry = (
            '* **DriveWorld**: "DriveWorld: A World Model for Autonomous Driving", '
            "**`arxiv 2026.05`**. [[Paper](https://arxiv.org/abs/2605.00001)] "
            "[[Code](https://example.com/code)]"
        )
        report = render_report(
            [papers[0]],
            query="ti:world",
            total_results=total_results,
            max_results=100,
            generated_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
            proposed_entries={"2605.00001": edited_entry},
        )
        self.assertIn(edited_entry, report)

    def test_large_report_switches_to_compact_candidates(self):
        papers, total_results = parse_feed(ATOM_FEED)
        many_papers = [
            Paper(
                arxiv_id=f"2605.{index:05d}",
                title=f"{papers[0].title} {index}",
                summary=papers[0].summary * 100,
                published=papers[0].published,
                updated=papers[0].updated,
                categories=papers[0].categories,
            )
            for index in range(100)
        ]
        report = render_report(
            many_papers,
            query="ti:world",
            total_results=total_results,
            max_results=100,
            generated_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        )
        self.assertIn("## Additional candidates", report)
        self.assertLess(len(report), 65_000)

    def test_proposed_entry_matches_repository_style(self):
        paper = Paper(
            arxiv_id="2605.00003",
            title="A Useful World Model",
            summary="",
            published=datetime(2026, 5, 1, tzinfo=timezone.utc),
            updated=datetime(2026, 5, 1, tzinfo=timezone.utc),
            categories=("cs.AI",),
        )
        self.assertEqual(
            proposed_readme_entry(paper),
            '* "A Useful World Model", **`arxiv 2026.05`**. [[Paper](https://arxiv.org/abs/2605.00003)]',
        )

    @patch("scripts.arxiv_candidates.time.sleep")
    @patch("scripts.arxiv_candidates.urllib.request.urlopen")
    def test_fetch_page_retries_transient_timeout(self, mock_urlopen, mock_sleep):
        mock_urlopen.side_effect = [TimeoutError(), BytesIO(ATOM_FEED.encode("utf-8"))]
        papers, total_results = fetch_page(
            request=Request("https://example.com"),
            timeout=1,
            retries=1,
            retry_delay=3.1,
        )
        self.assertEqual(total_results, 2)
        self.assertEqual(len(papers), 2)
        mock_sleep.assert_called_once_with(3.1)

    def test_cli_writes_deduplicated_report_from_saved_feed(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            feed_path = root / "feed.xml"
            readme_path = root / "README.md"
            ignore_path = root / "ignore.txt"
            output_path = root / "report.md"
            feed_path.write_text(ATOM_FEED, encoding="utf-8")
            readme_path.write_text(
                "[[Paper](https://arxiv.org/abs/2605.00001)]",
                encoding="utf-8",
            )
            ignore_path.write_text("", encoding="utf-8")
            with patch(
                "sys.argv",
                [
                    "arxiv_candidates.py",
                    "--feed-file",
                    str(feed_path),
                    "--readme",
                    str(readme_path),
                    "--ignore-file",
                    str(ignore_path),
                    "--output",
                    str(output_path),
                ],
            ), redirect_stdout(StringIO()) as stdout:
                self.assertEqual(main(), 0)
            self.assertEqual(stdout.getvalue(), "1\n")
            report = output_path.read_text(encoding="utf-8")
            self.assertNotIn("DriveWorld", report)
            self.assertIn("World Statistics for Regional Trade", report)

    def test_cli_excludes_arxiv_ids_from_open_pr_body(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            feed_path = root / "feed.xml"
            readme_path = root / "README.md"
            known_path = root / "open-prs.md"
            output_path = root / "report.md"
            feed_path.write_text(ATOM_FEED, encoding="utf-8")
            readme_path.write_text("", encoding="utf-8")
            known_path.write_text(
                "[`2605.00001`](https://arxiv.org/abs/2605.00001)\n"
                "[`2605.00002`](https://arxiv.org/abs/2605.00002)\n",
                encoding="utf-8",
            )
            with patch(
                "sys.argv",
                [
                    "arxiv_candidates.py",
                    "--feed-file",
                    str(feed_path),
                    "--readme",
                    str(readme_path),
                    "--known-file",
                    str(known_path),
                    "--output",
                    str(output_path),
                ],
            ), redirect_stdout(StringIO()) as stdout:
                self.assertEqual(main(), 0)
            self.assertEqual(stdout.getvalue(), "0\n")
            self.assertIn("No unreviewed candidates were found.", output_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
