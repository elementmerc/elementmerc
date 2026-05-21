#!/usr/bin/env python3
"""Refresh the profile README: latest writing feed + a self-generated
GitHub stats card. Run weekly by .github/workflows/refresh.yml.

Standard library only, on purpose: no third-party packages, nothing to
rot, nothing to render at view time. The stats card is written into the
repo as an SVG, so the README never depends on an external service.
"""

import datetime as dt
import json
import os
import re
import sys
import urllib.request
from xml.etree import ElementTree as ET

USER = "elementmerc"
README = "README.md"
STATS = "assets/stats.svg"
FEEDS = [
    "https://medium.com/feed/@mercurysnotes",
    "https://www.freecodecamp.org/news/author/elementmerc/rss/",
]
MAX_POSTS = 5


def fetch(url, token=None):
    req = urllib.request.Request(url, headers={"User-Agent": "mercury-profile-refresh"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


def parse_date(s):
    s = (s or "").strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=None)
        except ValueError:
            continue
    return dt.datetime.min


# A post is kept only if one of its feed tags contains one of these.
# Keeps the writing section to security/tech work and leaves personal
# essays out, without anything needing to be hand-curated.
TOPICS = (
    "security", "hack", "malware", "infosec", "cyber", "rust", "python",
    "linux", "programming", "technolog", "steganograph", "encrypt",
    "privacy", "software", "coding", "computer", "devops", "network",
    "vulnerab", "threat", "reverse engineer", "open source",
)


def on_topic(item):
    tags = " ".join((c.text or "") for c in item.findall("category")).lower()
    return any(k in tags for k in TOPICS)


def latest_posts():
    """Recent on-topic posts from each feed, newest first, de-duplicated."""
    found = []
    for feed in FEEDS:
        try:
            root = ET.fromstring(fetch(feed))
        except Exception as e:  # a feed being down must never break the run
            print(f"  feed skipped: {feed} ({e})", file=sys.stderr)
            continue
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip().split("?")[0]
            if title and link and on_topic(item):
                found.append((parse_date(item.findtext("pubDate")), title, link))
    seen, posts = set(), []
    for _, title, link in sorted(found, key=lambda x: x[0], reverse=True):
        if link in seen:
            continue
        seen.add(link)
        posts.append((title.replace("[", "(").replace("]", ")"), link))
    return posts[:MAX_POSTS]


def github_stats(token):
    user = json.loads(fetch(f"https://api.github.com/users/{USER}", token))
    repos = json.loads(
        fetch(f"https://api.github.com/users/{USER}/repos?per_page=100&type=owner", token)
    )
    own = [r for r in repos if not r.get("fork")]
    langs = {}
    for r in own:
        lng = r.get("language")
        if lng:
            langs[lng] = langs.get(lng, 0) + 1
    top = sorted(langs.items(), key=lambda kv: -kv[1])[:4]
    return {
        "repos": user.get("public_repos", len(repos)),
        "followers": user.get("followers", 0),
        "stars": sum(r.get("stargazers_count", 0) for r in own),
        "langs": top or [("—", 1)],
    }


def stats_svg(s, today):
    total = sum(c for _, c in s["langs"]) or 1
    palette = ["#e0102a", "#c4c4c6", "#7a7a7e", "#3f3f43"]
    bar, legend, x, lx = [], [], 28.0, 28.0
    for i, (name, count) in enumerate(s["langs"]):
        col = palette[i % len(palette)]
        w = 664 * count / total
        bar.append(f'<rect x="{x:.1f}" y="172" width="{max(w - 3, 2):.1f}" height="9" rx="2" fill="{col}"/>')
        legend.append(
            f'<rect x="{lx:.1f}" y="191" width="9" height="9" rx="2" fill="{col}"/>'
            f'<text x="{lx + 15:.1f}" y="199" font-size="11" fill="#8a8a8e">{name}</text>'
        )
        lx += 15 + len(name) * 7.0 + 24
        x += w
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 212" role="img" aria-label="GitHub stats for {USER}">
  <style>text{{font-family:ui-monospace,'SF Mono','DejaVu Sans Mono',Consolas,monospace}}</style>
  <rect x="1" y="1" width="718" height="210" rx="14" fill="#0b0b0c" stroke="#2e2e30" stroke-width="1.5"/>
  <text x="28" y="35" font-size="13" letter-spacing="2" fill="#6f6f73">$ github --stats</text>
  <text x="692" y="35" text-anchor="end" font-size="13" fill="#8a8a8e">@{USER}</text>
  <line x1="28" y1="49" x2="692" y2="49" stroke="#2e2e30" stroke-width="1.5"/>
  <text x="150" y="113" text-anchor="middle" font-size="44" fill="#f4f4f5">{s["repos"]}</text>
  <text x="150" y="137" text-anchor="middle" font-size="11" letter-spacing="2" fill="#8a8a8e">REPOSITORIES</text>
  <text x="360" y="113" text-anchor="middle" font-size="44" fill="#e0102a">{s["stars"]}</text>
  <text x="360" y="137" text-anchor="middle" font-size="11" letter-spacing="2" fill="#8a8a8e">STARS EARNED</text>
  <text x="570" y="113" text-anchor="middle" font-size="44" fill="#f4f4f5">{s["followers"]}</text>
  <text x="570" y="137" text-anchor="middle" font-size="11" letter-spacing="2" fill="#8a8a8e">FOLLOWERS</text>
  <line x1="28" y1="155" x2="692" y2="155" stroke="#2e2e30" stroke-width="1.5"/>
  {"".join(bar)}
  {"".join(legend)}
  <text x="692" y="199" text-anchor="end" font-size="10" letter-spacing="1" fill="#5a5a5e">synced {today}</text>
</svg>
'''


def main():
    token = os.environ.get("GITHUB_TOKEN") or None
    today = dt.date.today().isoformat()
    with open(README, encoding="utf-8") as f:
        text = f.read()

    posts = latest_posts()
    if posts:
        block = "\n".join(f"- [{t}]({l})" for t, l in posts)
        text = re.sub(
            r"(<!-- AUTO:WRITING:START -->).*?(<!-- AUTO:WRITING:END -->)",
            lambda m: m.group(1) + "\n" + block + "\n" + m.group(2),
            text, flags=re.S,
        )
        print(f"  writing: {len(posts)} posts")
    else:
        print("  writing: nothing fetched, section left untouched")

    try:
        s = github_stats(token)
        with open(STATS, "w", encoding="utf-8") as f:
            f.write(stats_svg(s, today))
        print(f"  stats: {s['repos']} repos, {s['stars']} stars, {s['followers']} followers")
    except Exception as e:
        print(f"  stats: skipped ({e})", file=sys.stderr)

    # bumped every run so there is always a commit -> keeps the schedule alive
    stamp = f"<!-- synced: {today} -->"
    text = re.sub(r"<!-- synced:.*?-->", stamp, text) if "<!-- synced:" in text else text + "\n" + stamp + "\n"

    with open(README, "w", encoding="utf-8") as f:
        f.write(text)
    print("  README refreshed")


if __name__ == "__main__":
    main()
