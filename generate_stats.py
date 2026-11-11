"""Generate the dynamic activity section for a GitHub profile README."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


START_MARKER = "<!--START_SECTION:profile-stats-->"
END_MARKER = "<!--END_SECTION:profile-stats-->"
DEFAULT_TIMEZONE = "Asia/Shanghai"
TIME_BAR_WIDTH = 15
WEEKDAY_BAR_WIDTH = 20
LANGUAGE_BAR_WIDTH = 24
PERIOD_ORDER = ("morning", "afternoon", "evening", "night")
PERIOD_LABELS = {
    "morning": "🌞 Morning / 上午 (06-12)",
    "afternoon": "🌆 Afternoon / 下午 (12-18)",
    "evening": "🌃 Evening / 傍晚 (18-24)",
    "night": "🌙 Late night / 深夜 (00-06)",
}
PERIOD_SHORT_LABELS = {
    "morning": "Morning / 上午 🌅",
    "afternoon": "Afternoon / 下午 ☀️",
    "evening": "Evening / 傍晚 🌆",
    "night": "Late night / 深夜 🌙",
}
DAY_LABELS = (
    "🐔 Monday / 周一",
    "🐱 Tuesday / 周二",
    "🐶 Wednesday / 周三",
    "🐮 Thursday / 周四",
    "🐯 Friday / 周五",
    "🐰 Saturday / 周六",
    "🐲 Sunday / 周日",
)
DAY_SHORT_LABELS = (
    "Monday / 周一",
    "Tuesday / 周二",
    "Wednesday / 周三",
    "Thursday / 周四",
    "Friday / 周五",
    "Saturday / 周六",
    "Sunday / 周日",
)
ZERO_WIDTH_CHARS = {"\ufe0e", "\ufe0f"}


def github_headers(token: str) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-profile-activity-generator",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def request_json(url: str, token: str, retries: int = 3) -> Any:
    request = urllib.request.Request(url, headers=github_headers(token))
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            last_error = error
            if attempt < retries:
                time.sleep(attempt * 2)

    raise RuntimeError(f"GitHub API request failed for {url}: {last_error}")


def fetch_commits(username: str, token: str) -> list[dict[str, Any]]:
    base_query = f"author:{username} is:public"
    commits: dict[str, dict[str, Any]] = {}
    pending = [base_query]

    def search(query: str, page: int = 1, order: str = "desc") -> dict[str, Any]:
        params = urllib.parse.urlencode(
            {"q": query, "sort": "author-date", "order": order, "per_page": 100, "page": page}
        )
        payload = request_json(f"https://api.github.com/search/commits?{params}", token)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise RuntimeError("GitHub Commit Search API returned an unexpected response")
        if payload["incomplete_results"]:
            raise RuntimeError("GitHub commit search was incomplete; keeping the existing README")
        return payload

    while pending:
        query = pending.pop()
        payload = search(query)
        if payload["total_count"] > 1000:
            # GitHub caps each search at 1,000 results, so split its date range.
            newest = datetime.fromisoformat(
                payload["items"][0]["commit"]["author"]["date"].replace("Z", "+00:00")
            )
            oldest_page = search(query, order="asc")
            oldest = datetime.fromisoformat(
                oldest_page["items"][0]["commit"]["author"]["date"].replace("Z", "+00:00")
            )
            midpoint = (oldest + (newest - oldest) / 2).replace(microsecond=0)
            if midpoint < oldest or midpoint >= newest:
                raise RuntimeError(
                    "GitHub search cannot split over 1,000 commits at the same author timestamp"
                )
            next_second = midpoint + timedelta(seconds=1)
            pending.extend([
                f"{base_query} author-date:{oldest.isoformat()}..{midpoint.isoformat()}",
                f"{base_query} author-date:{next_second.isoformat()}..{newest.isoformat()}",
            ])
            continue

        page_count = (payload["total_count"] + 99) // 100
        for page in range(1, page_count + 1):
            if page > 1:
                payload = search(query, page)
            for commit in payload["items"]:
                commits.setdefault(commit["sha"], commit)

    return list(commits.values())


def fetch_repository_languages(repository: str, token: str) -> dict[str, int]:
    encoded_repository = urllib.parse.quote(repository, safe="/")
    payload = request_json(
        f"https://api.github.com/repos/{encoded_repository}/languages",
        token,
    )
    if not isinstance(payload, dict):
        return {}
    return {
        str(language): int(byte_count)
        for language, byte_count in payload.items()
        if isinstance(byte_count, int)
    }


def classify_period(hour: int) -> str:
    if 6 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 24:
        return "evening"
    return "night"


def visual_width(value: str) -> int:
    width = 0
    for character in value:
        if character in ZERO_WIDTH_CHARS:
            continue
        if ord(character) > 127:
            width += 2
        else:
            width += 1
    return width


def pad_visual(value: str, target_width: int) -> str:
    return value + " " * max(target_width - visual_width(value), 1)


def text_bar(items: list[tuple[str, int]], total: int, width: int = 12) -> str:
    label_width = max(visual_width(label) for label, _ in items) + 2
    lines = []

    for label, value in items:
        percentage = value / total * 100 if total else 0.0
        filled = round(width * value / total) if total else 0
        if value and not filled:
            filled = 1
        bar = "█" * filled + "░" * (width - filled)
        value_text = f"{value} Commit" + ("s" if value != 1 else "")
        lines.append(
            f"{pad_visual(label, label_width)}"
            f"{value_text:>12}  {bar}  {percentage:5.1f} %"
        )

    return "```text\n" + "\n".join(lines) + "\n```"


def format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def language_bar(language_counts: Counter[str], width: int = 12) -> str:
    if not language_counts:
        return "No public language data available yet. / 暂无公开语言数据。"

    sorted_languages = language_counts.most_common()
    displayed = sorted_languages[:4]
    remaining = sum(value for _, value in sorted_languages[4:])
    if remaining:
        displayed.append(("Other", remaining))

    total = sum(language_counts.values())
    label_width = max(visual_width(label) for label, _ in displayed) + 2
    lines = []
    for label, value in displayed:
        percentage = value / total * 100 if total else 0.0
        filled = round(width * value / total) if total else 0
        if value and not filled:
            filled = 1
        bar = "█" * filled + "░" * (width - filled)
        lines.append(
            f"{pad_visual(label, label_width)}"
            f"{format_bytes(value):>10}  {bar}  {percentage:5.1f} %"
        )

    return "```text\n" + "\n".join(lines) + "\n```"


def analyze_commits(
    commits: list[dict[str, Any]], token: str, timezone_name: str
) -> tuple[Counter[str], Counter[int], Counter[str]]:
    local_timezone = ZoneInfo(timezone_name)
    period_counts: Counter[str] = Counter({period: 0 for period in PERIOD_ORDER})
    day_counts: Counter[int] = Counter({day: 0 for day in range(7)})
    repositories: set[str] = set()

    for commit in commits:
        authored_at = datetime.fromisoformat(
            commit["commit"]["author"]["date"].replace("Z", "+00:00")
        )
        local_time = authored_at.astimezone(local_timezone)
        period_counts[classify_period(local_time.hour)] += 1
        day_counts[local_time.weekday()] += 1
        repositories.add(commit["repository"]["full_name"])

    language_counts: Counter[str] = Counter()
    language_errors: list[str] = []
    for repository in sorted(repositories):
        try:
            language_counts.update(fetch_repository_languages(repository, token))
        except RuntimeError as error:
            print(f"Warning: skipping language data for {repository}: {error}")
            language_errors.append(repository)

    if repositories and not language_counts and language_errors:
        raise RuntimeError(
            "All repository language requests failed; keeping the existing README "
            "instead of replacing valid data with an empty result"
        )

    return period_counts, day_counts, language_counts


def render_stats(
    commits: list[dict[str, Any]],
    period_counts: Counter[str],
    day_counts: Counter[int],
    language_counts: Counter[str],
    timezone_name: str,
) -> str:
    total_commits = len(commits)
    most_active_period = max(PERIOD_ORDER, key=period_counts.get)
    most_active_day = max(range(7), key=day_counts.get)
    main_language = language_counts.most_common(1)[0][0] if language_counts else "N/A"
    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    time_items = [(PERIOD_LABELS[period], period_counts[period]) for period in PERIOD_ORDER]
    day_items = [(DAY_LABELS[day], day_counts[day]) for day in range(7)]

    return f"""**Coding Rhythm**

> Automatically updated by GitHub Actions · Last updated: {updated_at} · {timezone_name}

Based on **{total_commits}** public commits by this author / 基于该作者的 **{total_commits}** 条公开 Commit：

统计全部可检索的默认分支提交，按作者时间统计，同一提交只计一次；包含署名为本账号的自动提交。语言分布为相关仓库的代码体量。

| Most Active Time | Most Productive Day | Main Language |
|:---:|:---:|:---:|
| {PERIOD_SHORT_LABELS[most_active_period] if total_commits else "N/A"} | {DAY_SHORT_LABELS[most_active_day] if total_commits else "N/A"} | {main_language} |

### Time Distribution / 时段分布

{text_bar(time_items, total_commits, width=TIME_BAR_WIDTH)}

### Weekday Distribution / 星期分布

{text_bar(day_items, total_commits, width=WEEKDAY_BAR_WIDTH)}

### Language Distribution / 语言分布

{language_bar(language_counts, width=LANGUAGE_BAR_WIDTH)}"""


def replace_stats_section(readme: str, stats: str) -> str:
    if readme.count(START_MARKER) != 1 or readme.count(END_MARKER) != 1:
        raise RuntimeError("README must contain exactly one pair of profile stats markers")

    pattern = re.compile(
        rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}",
        flags=re.DOTALL,
    )
    replacement = f"{START_MARKER}\n{stats.rstrip()}\n{END_MARKER}"
    return pattern.sub(lambda _: replacement, readme)


def main() -> None:
    username = os.environ.get("GITHUB_USERNAME", "").strip()
    if not username:
        raise SystemExit("GITHUB_USERNAME is required")

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    timezone_name = os.environ.get("PROFILE_TIMEZONE", DEFAULT_TIMEZONE).strip()
    readme_path = Path(os.environ.get("README_PATH", "README.md"))

    print(f"Fetching all searchable public commits for {username}...")
    commits = fetch_commits(username, token)
    period_counts, day_counts, language_counts = analyze_commits(
        commits, token, timezone_name
    )
    stats = render_stats(
        commits,
        period_counts,
        day_counts,
        language_counts,
        timezone_name,
    )

    original_readme = readme_path.read_text(encoding="utf-8")
    updated_readme = replace_stats_section(original_readme, stats)
    if updated_readme == original_readme:
        print("README statistics are already up to date.")
        return

    readme_path.write_text(updated_readme, encoding="utf-8")
    print(f"Updated {readme_path} with {len(commits)} public commits.")


if __name__ == "__main__":
    main()
