#!/usr/bin/env python3
"""Build the combined activity card and recent-activity feed for the profile README.

Treats every login in ACCOUNTS as one person: contribution calendars are merged
day by day, lifetime totals are summed across all years each account existed,
and recent merged PRs from all accounts are interleaved.

Only public data (plus private-contribution *counts* the accounts already show
publicly) is read, so nothing private ends up in the README.
"""
import datetime as dt
import html
import json
import os
import re
import urllib.parse
import urllib.request

ACCOUNTS = ["sanil-23", "jainsanil18"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = os.environ["GITHUB_TOKEN"]


def gh(url, body=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body else None,
        headers={
            "Authorization": f"bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "sanil-23-profile",
        },
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def graphql(query, **variables):
    out = gh("https://api.github.com/graphql", {"query": query, "variables": variables})
    if out.get("errors"):
        raise RuntimeError(out["errors"])
    return out["data"]


CAL_Q = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    createdAt
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}"""


def calendar(login, start, end):
    data = graphql(CAL_Q, login=login, **{"from": start.isoformat(), "to": end.isoformat()})
    return data["user"]


def collect():
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    year_ago = now - dt.timedelta(days=364)
    days, per_account, lifetime, first_year = {}, {}, 0, now.year

    for login in ACCOUNTS:
        user = calendar(login, year_ago, now)
        cal = user["contributionsCollection"]["contributionCalendar"]
        per_account[login] = cal["totalContributions"]
        for week in cal["weeks"]:
            for d in week["contributionDays"]:
                days[d["date"]] = days.get(d["date"], 0) + d["contributionCount"]

        # Lifetime: one query per calendar year since the account was created.
        created = int(user["createdAt"][:4])
        first_year = min(first_year, created)
        for year in range(created, now.year + 1):
            start = dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc)
            end = min(dt.datetime(year, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc), now)
            lifetime += calendar(login, start, end)["contributionsCollection"][
                "contributionCalendar"]["totalContributions"]

    return {
        "days": dict(sorted(days.items())),
        "per_account": per_account,
        "lifetime": lifetime,
        "first_year": first_year,
        "now": now,
    }


def streaks(days):
    ordered = list(days.values())
    longest = run = 0
    for n in ordered:
        run = run + 1 if n else 0
        longest = max(longest, run)
    # Today may still be empty; don't let that break the current streak.
    tail = ordered[:-1] if ordered and ordered[-1] == 0 else ordered
    current = 0
    for n in reversed(tail):
        if not n:
            break
        current += 1
    return current, longest


PALETTE = ["#161b2e", "#0e4f5c", "#0a8a8f", "#16c2c2", "#5ef2ff"]


def level(n, cuts):
    if n == 0:
        return 0
    return 1 + sum(n > c for c in cuts)


def activity_svg(stats):
    days = stats["days"]
    counts = sorted(n for n in days.values() if n)
    cuts = [counts[int(len(counts) * q)] for q in (0.25, 0.5, 0.75)] if counts else [1, 2, 3]
    current, longest = streaks(days)
    year_total = sum(days.values())
    busiest = max(days.items(), key=lambda kv: kv[1]) if days else ("-", 0)

    cell, gap, left, top = 11, 3, 32, 128
    dates = list(days)
    first = dt.date.fromisoformat(dates[0])
    offset = (first.weekday() + 1) % 7  # GitHub weeks start on Sunday
    cols = (len(dates) + offset + 6) // 7
    width = left + cols * (cell + gap) + 24
    height = top + 7 * (cell + gap) + 44

    rects, months, last_month = [], [], None
    for i, date in enumerate(dates):
        slot = i + offset
        x, y = left + (slot // 7) * (cell + gap), top + (slot % 7) * (cell + gap)
        n = days[date]
        delay = (slot // 7) * 0.075
        rects.append(
            f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2.5" '
            f'fill="{PALETTE[level(n, cuts)]}" style="animation-delay:{delay:.3f}s">'
            f"<title>{date}: {n} contributions</title></rect>"
        )
        d = dt.date.fromisoformat(date)
        if d.day <= 7 and d.month != last_month and slot % 7 == 0:
            months.append(f'<text x="{x}" y="{top - 8}" class="dim">{d.strftime("%b")}</text>')
            last_month = d.month

    def stat(x, value, label):
        return (
            f'<text x="{x}" y="76" class="big">{value}</text>'
            f'<text x="{x}" y="96" class="dim">{label}</text>'
        )

    span = cols * (cell + gap)
    step = span / 5
    stat_row = "".join(
        stat(left + i * step, v, l)
        for i, (v, l) in enumerate([
            (f"{stats['lifetime']:,}", f"total since {stats['first_year']}"),
            (f"{year_total:,}", "last 12 months"),
            (f"{current}d", "current streak"),
            (f"{longest}d", "longest streak"),
            (f"{busiest[1]}", f"best day ({busiest[0][5:]})"),
        ])
    )
    accounts = "  ·  ".join(f"@{a} {n:,}" for a, n in stats["per_account"].items())
    legend_x = left + span - 5 * (cell + 3)
    legend = "".join(
        f'<rect x="{legend_x + i * (cell + 3)}" y="{height - 26}" width="{cell}" height="{cell}" rx="2.5" fill="{c}"/>'
        for i, c in enumerate(PALETTE)
    )
    stamp = stats["now"].strftime("%Y-%m-%d %H:%M UTC")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<style>
  text {{ font-family: 'JetBrains Mono','SFMono-Regular',Consolas,monospace; fill: #c9f6ff; }}
  .title {{ font-size: 13px; letter-spacing: 3px; fill: #5ef2ff; }}
  .big {{ font-size: 22px; font-weight: 700; fill: #ffffff; }}
  .dim {{ font-size: 10px; fill: #6b8ba4; }}
  rect[style] {{ animation: pulse 4s ease-in-out infinite; }}
  @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: .72; }} }}
  .scan {{ animation: scan 6s linear infinite; }}
  @keyframes scan {{ from {{ transform: translateX(-120px); }} to {{ transform: translateX({width}px); }} }}
</style>
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#0a0f1f"/><stop offset="1" stop-color="#0d1630"/>
  </linearGradient>
  <linearGradient id="beam" x1="0" x2="1">
    <stop offset="0" stop-color="#5ef2ff" stop-opacity="0"/>
    <stop offset=".5" stop-color="#5ef2ff" stop-opacity=".10"/>
    <stop offset="1" stop-color="#5ef2ff" stop-opacity="0"/>
  </linearGradient>
  <clipPath id="card"><rect width="{width}" height="{height}" rx="14"/></clipPath>
</defs>
<g clip-path="url(#card)">
  <rect width="{width}" height="{height}" fill="url(#bg)"/>
  <rect class="scan" width="120" height="{height}" fill="url(#beam)"/>
</g>
<rect x=".5" y=".5" width="{width - 1}" height="{height - 1}" rx="14" fill="none" stroke="#1f3a5c"/>
<text x="{left}" y="36" class="title">// ACTIVITY ACROSS ALL MY ACCOUNTS</text>
{stat_row}
{''.join(months)}
<text x="4" y="{top + 1 * (cell + gap) + 9}" class="dim">Mon</text>
<text x="4" y="{top + 3 * (cell + gap) + 9}" class="dim">Wed</text>
<text x="4" y="{top + 5 * (cell + gap) + 9}" class="dim">Fri</text>
{''.join(rects)}
<text x="{left}" y="{height - 16}" class="dim">{html.escape(accounts)}  ·  updated {stamp}</text>
{legend}
</svg>
"""


SEARCH = "https://api.github.com/search/issues?per_page=20&sort=updated&order=desc&q="


def recent_prs(limit=8):
    prs = []
    for login in ACCOUNTS:
        q = urllib.parse.quote(f"is:pr is:merged is:public author:{login}")
        for item in gh(SEARCH + q)["items"]:
            repo = item["repository_url"].split("/repos/")[1]
            prs.append((item["closed_at"], repo, item["title"], item["html_url"]))
    prs.sort(reverse=True)
    lines = []
    for closed, repo, title, url in prs[:limit]:
        title = title.replace("|", "\\|").replace("[", "(").replace("]", ")")
        lines.append(f"- `{closed[:10]}` **{repo}** · [{title}]({url})")
    return "\n".join(lines) or "- _nothing public this week_"


def replace_block(text, name, body):
    pattern = re.compile(rf"(<!-- {name}:START -->).*?(<!-- {name}:END -->)", re.S)
    return pattern.sub(lambda m: f"{m.group(1)}\n{body}\n{m.group(2)}", text)


def main():
    stats = collect()
    with open(os.path.join(ROOT, "assets", "activity.svg"), "w") as f:
        f.write(activity_svg(stats))

    readme_path = os.path.join(ROOT, "README.md")
    with open(readme_path) as f:
        readme = f.read()
    readme = replace_block(readme, "RECENT", recent_prs())
    with open(readme_path, "w") as f:
        f.write(readme)


if __name__ == "__main__":
    main()
