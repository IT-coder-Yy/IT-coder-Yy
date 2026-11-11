import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import generate_stats as stats


def commit(sha, date, repository="example/project"):
    return {
        "sha": sha,
        "commit": {
            "author": {"date": date},
            "committer": {"date": "2026-11-11T12:00:00Z"},
        },
        "repository": {"full_name": repository},
    }


class CommitStatisticsTests(unittest.TestCase):
    def test_all_pages_and_date_splits_without_a_sample_limit(self):
        for count in (0, 501, 1000, 1005):
            with self.subTest(count=count):
                start = datetime(2026, 1, 1, tzinfo=timezone.utc)
                records = [
                    commit(str(i), (start + timedelta(seconds=i)).isoformat())
                    for i in range(count)
                ]

                def search(url, token):
                    params = parse_qs(urlparse(url).query)
                    query = params["q"][0]
                    self.assertTrue(query.startswith("author:example is:public"))
                    self.assertEqual(params["sort"], ["author-date"])
                    selected = records
                    if " author-date:" in query:
                        lower, upper = query.split(" author-date:")[1].split("..")
                        selected = [
                            row for row in records
                            if datetime.fromisoformat(lower)
                            <= datetime.fromisoformat(row["commit"]["author"]["date"])
                            <= datetime.fromisoformat(upper)
                        ]
                    selected = sorted(
                        selected,
                        key=lambda row: row["commit"]["author"]["date"],
                        reverse=params["order"] == ["desc"],
                    )
                    page = int(params["page"][0])
                    self.assertLessEqual(page, 10, "GitHub cannot return search results beyond 1,000")
                    return {
                        "total_count": len(selected),
                        "incomplete_results": False,
                        "items": selected[(page - 1) * 100:page * 100],
                    }

                with patch.object(stats, "request_json", side_effect=search):
                    result = stats.fetch_commits("example", "token")
                self.assertEqual({row["sha"] for row in result}, {str(i) for i in range(count)})
                self.assertEqual(len(result), count)

    def test_same_commit_in_two_repositories_counts_once(self):
        payload = {
            "total_count": 2,
            "incomplete_results": False,
            "items": [
                commit("same-sha", "2026-11-09T16:00:00Z"),
                commit("same-sha", "2026-11-09T16:00:00Z", "example/fork"),
            ],
        }
        with patch.object(stats, "request_json", return_value=payload):
            self.assertEqual(len(stats.fetch_commits("example", "token")), 1)

    def test_author_time_in_shanghai_and_language_repository_deduplication(self):
        records = [
            commit("a", "2026-11-09T15:59:59Z"),  # Monday 23:59:59
            commit("b", "2026-11-09T16:00:00Z"),  # Tuesday 00:00:00
            commit("c", "2026-11-09T22:00:00Z"),  # Tuesday 06:00:00
            commit("d", "2026-11-10T04:00:00Z"),  # Tuesday 12:00:00
        ]
        with patch.object(stats, "fetch_repository_languages", return_value={"Python": 123}) as languages:
            periods, days, totals = stats.analyze_commits(records, "token", "Asia/Shanghai")
        self.assertEqual(periods, Counter(morning=1, afternoon=1, evening=1, night=1))
        self.assertEqual(days[0], 1)
        self.assertEqual(days[1], 3)
        self.assertEqual(sum(days.values()), 4)
        self.assertEqual(totals, Counter(Python=123))
        languages.assert_called_once_with("example/project", "token")

    def test_incomplete_search_keeps_existing_readme(self):
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text("existing statistics", encoding="utf-8")
            with patch.dict("os.environ", {"GITHUB_USERNAME": "example", "README_PATH": str(readme)}):
                with patch.object(stats, "request_json", return_value={"items": [], "incomplete_results": True}):
                    with self.assertRaisesRegex(RuntimeError, "incomplete"):
                        stats.main()
            self.assertEqual(readme.read_text(encoding="utf-8"), "existing statistics")

    def test_render_commit_units_and_preserve_surrounding_readme(self):
        records = [commit("a", "2026-11-09T15:59:59Z")]
        with patch.object(stats, "fetch_repository_languages", return_value={"Python": 123}):
            counters = stats.analyze_commits(records, "token", "Asia/Shanghai")
        rendered = stats.render_stats(records, *counters, "Asia/Shanghai")
        self.assertIn("1 Commit", rendered)
        self.assertNotIn("Push", rendered)
        self.assertIn("100.0 %", rendered)
        readme = f"before\n{stats.START_MARKER}\nold\n{stats.END_MARKER}\nafter"
        updated = stats.replace_stats_section(readme, rendered)
        self.assertEqual(updated, f"before\n{stats.START_MARKER}\n{rendered}\n{stats.END_MARKER}\nafter")

    def test_empty_history_has_no_most_active_day(self):
        counters = stats.analyze_commits([], "token", "Asia/Shanghai")
        rendered = stats.render_stats([], *counters, "Asia/Shanghai")
        self.assertIn("| N/A | N/A | N/A |", rendered)
        self.assertIn("0 Commits", rendered)


if __name__ == "__main__":
    unittest.main()
