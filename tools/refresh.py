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
from xml.sax.saxutils import escape

USER = "elementmerc"
# Orgs whose public repos count as "mine" for the stats card. The work in
# The-Malware-Files (dokima, model-security-core, …) is genuinely co-owned;
# adding it here folds it into REPOSITORIES / STARS / language pie.
ORGS = ("The-Malware-Files",)
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


def fetch_graphql(query, token):
    """POST a GraphQL query and return the raw response bytes."""
    body = json.dumps({"query": query}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        method="POST",
        headers={"User-Agent": "mercury-profile-refresh",
                 "Content-Type": "application/json"},
    )
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


def clean_title(raw):
    """Make a feed title safe to drop verbatim into a markdown list item:
    one line, no control characters, no angle brackets (which would let a
    title smuggle in HTML or an AUTO:WRITING marker), no link-breaking
    brackets, and a sane length."""
    t = re.sub(r"\s+", " ", raw or "")
    t = "".join(c for c in t if c >= " ")
    t = t.replace("<", "").replace(">", "")
    t = t.replace("[", "(").replace("]", ")").strip()
    return t[:99].rstrip() + "…" if len(t) > 100 else t


def clean_link(raw):
    """Return the URL only if it is a plain http(s) link with no characters
    that would break out of a markdown ( ) target."""
    link = (raw or "").strip().split("?")[0].split("#")[0]
    return link if re.match(r"https?://[^\s<>()]+\Z", link) else ""


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
            title = clean_title(item.findtext("title"))
            link = clean_link(item.findtext("link"))
            if title and link and on_topic(item):
                found.append((parse_date(item.findtext("pubDate")), title, link))
    seen, posts = set(), []
    for _, title, link in sorted(found, key=lambda x: x[0], reverse=True):
        if link in seen:
            continue
        seen.add(link)
        posts.append((title, link))
    return posts[:MAX_POSTS]


def github_stats(token):
    user = json.loads(fetch(f"https://api.github.com/users/{USER}", token))
    repos = json.loads(
        fetch(f"https://api.github.com/users/{USER}/repos?per_page=100&type=owner", token)
    )
    # an error payload (rate limit, outage, 404) is shaped wrong or lacks the
    # fields we need — raise so main() keeps the last good stats card rather
    # than regenerating it with zeros
    if (not isinstance(repos, list) or not isinstance(user, dict)
            or "public_repos" not in user):
        raise ValueError("unexpected GitHub API response")

    # Org public repos count as the user's too. An individual org being
    # unreachable degrades to "skip that org" rather than wiping the card.
    owned_nonforks = [r for r in repos if isinstance(r, dict) and not r.get("fork")]
    org_nonforks = []
    for org in ORGS:
        try:
            org_repos = json.loads(
                fetch(f"https://api.github.com/orgs/{org}/repos?per_page=100&type=public", token)
            )
            if isinstance(org_repos, list):
                org_nonforks.extend(
                    r for r in org_repos if isinstance(r, dict) and not r.get("fork"))
            else:
                print(f"  org repos skipped: {org} (unexpected shape)", file=sys.stderr)
        except Exception as e:
            print(f"  org repos skipped: {org} ({e})", file=sys.stderr)

    own = owned_nonforks + org_nonforks  # the set stars and the language pie sum over

    # Public contributions across all of GitHub over the trailing year, by
    # type. Sum, deliberately, instead of the calendar's totalContributions:
    # the calendar number folds in private/restricted contributions, which
    # don't belong on a public profile card. Captures upstream work (e.g.
    # PRs into MalChela) that the REST repos list cannot see.
    gql = json.loads(fetch_graphql(
        'query { user(login: "' + USER + '") { contributionsCollection { '
        'totalCommitContributions totalIssueContributions '
        'totalPullRequestContributions totalPullRequestReviewContributions } } }',
        token))
    if "errors" in gql or not isinstance(gql.get("data"), dict):
        raise ValueError(f"GraphQL contributions query failed: {gql.get('errors', gql)}")
    c = gql["data"]["user"]["contributionsCollection"]
    contributions = (
        c["totalCommitContributions"] + c["totalIssueContributions"]
        + c["totalPullRequestContributions"] + c["totalPullRequestReviewContributions"]
    )

    # Languages by bytes across every counted repo, not by primary-language
    # count. The primary-language field is one-per-repo, so a language that
    # is second-place everywhere (e.g. TypeScript when Rust always wins by
    # bytes) never shows. The /languages endpoint returns the byte map.
    langs = {}
    for r in own:
        full = r.get("full_name")
        if not full:
            continue
        try:
            payload = json.loads(
                fetch(f"https://api.github.com/repos/{full}/languages", token))
            if isinstance(payload, dict):
                for lng, n in payload.items():
                    if isinstance(n, int):
                        langs[lng] = langs.get(lng, 0) + n
        except Exception as e:
            print(f"  languages skipped: {full} ({e})", file=sys.stderr)
    top = sorted(langs.items(), key=lambda kv: -kv[1])[:4]
    return {
        # match the github.com profile page count (user's own public repos,
        # forks included) and add org public non-forks on top so the number
        # is always a superset of what the profile shows
        "repos": user["public_repos"] + len(org_nonforks),
        "stars": sum(r.get("stargazers_count", 0) for r in own),
        "contributions": contributions,
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
            f'<text x="{lx + 15:.1f}" y="199" font-size="11" fill="#8a8a8e">{escape(name)}</text>'
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
  <text x="570" y="113" text-anchor="middle" font-size="44" fill="#f4f4f5">{s["contributions"]}</text>
  <text x="570" y="137" text-anchor="middle" font-size="11" letter-spacing="2" fill="#8a8a8e">CONTRIBUTIONS</text>
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
