from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.apply_issue_selections import (
    AcceptedCandidate,
    apply_candidates,
    main,
    parse_checked_candidates,
)
from scripts.arxiv_candidates import Paper, render_candidate_checkbox


def checkbox(arxiv_id: str, title: str, section: str, checked: bool = True) -> str:
    paper = Paper(
        arxiv_id=arxiv_id,
        title=title,
        summary="",
        published=datetime(2026, 5, 30, tzinfo=timezone.utc),
        updated=datetime(2026, 5, 30, tzinfo=timezone.utc),
        categories=("cs.AI",),
    )
    line = render_candidate_checkbox(paper, section, {arxiv_id} if checked else set())
    return line


class ApplyIssueSelectionsTests(unittest.TestCase):
    def test_parse_checked_candidates_uses_visible_section_override(self):
        body = "\n".join(
            [
                checkbox("2605.00001", "Accepted World Model", "World Models for Embodied AI"),
                checkbox("2605.00002", "Unchecked World Model", "General World Models", checked=False),
            ]
        )
        candidates = parse_checked_candidates(body)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].section, "World Models for Embodied AI")
        self.assertEqual(candidates[0].arxiv_id, "2605.00001")

    def test_parse_checked_candidates_rejects_unknown_section(self):
        body = checkbox("2605.00001", "Accepted World Model", "Unknown Section")
        with self.assertRaisesRegex(ValueError, "Unsupported README section"):
            parse_checked_candidates(body)

    def test_blog_or_technical_report_section_is_supported(self):
        body = checkbox("2605.00001", "Accepted World Model", "Blog or Technical Report")
        candidates = parse_checked_candidates(body)
        self.assertEqual(candidates[0].section, "Blog or Technical Report")

    def test_apply_candidates_inserts_at_section_top_and_preserves_crlf(self):
        readme = "# List\r\n## General World Models\r\n* old\r\n## Citation\r\n"
        candidate = AcceptedCandidate(
            arxiv_id="2605.00001",
            title="Accepted World Model",
            published=date(2026, 5, 30),
            section="General World Models",
        )
        updated_readme, inserted = apply_candidates(readme, [candidate])
        self.assertEqual(inserted, [candidate])
        self.assertIn(
            '## General World Models\r\n* "Accepted World Model", **`arxiv 2026.05`**. '
            "[[Paper](https://arxiv.org/abs/2605.00001)]\r\n* old",
            updated_readme,
        )
        self.assertNotIn("\n", updated_readme.replace("\r\n", ""))

    def test_apply_candidates_skips_existing_arxiv_id(self):
        readme = (
            "## General World Models\n"
            '* "Accepted World Model", **`arxiv 2026.05`**. '
            "[[Paper](https://arxiv.org/abs/2605.00001)]\n"
        )
        candidate = AcceptedCandidate(
            arxiv_id="2605.00001",
            title="Accepted World Model",
            published=date(2026, 5, 30),
            section="General World Models",
        )
        updated_readme, inserted = apply_candidates(readme, [candidate])
        self.assertEqual(updated_readme, readme)
        self.assertEqual(inserted, [])

    def test_main_updates_readme_and_writes_summary(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            issue_path = root / "issue.md"
            readme_path = root / "README.md"
            summary_path = root / "summary.md"
            issue_path.write_text(
                checkbox("2605.00001", "Accepted World Model", "General World Models"),
                encoding="utf-8",
            )
            readme_path.write_text("## General World Models\n* old\n", encoding="utf-8")
            with patch(
                "sys.argv",
                [
                    "apply_issue_selections.py",
                    "--issue-body",
                    str(issue_path),
                    "--readme",
                    str(readme_path),
                    "--summary-output",
                    str(summary_path),
                ],
            ), patch("sys.stdout", new_callable=StringIO) as stdout:
                self.assertEqual(main(), 0)
            self.assertEqual(stdout.getvalue(), "1\n")
            self.assertIn("Accepted World Model", readme_path.read_text(encoding="utf-8"))
            self.assertIn("2605.00001", summary_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
