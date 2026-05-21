#!/usr/bin/env python3
"""Chaos / adversarial test suite for refresh.py.

Run:  python3 tools/test_refresh.py

Exercises hostile feeds, content-injection attempts, GitHub API failures
and malformed data, and asserts the weekly refresh always degrades safely
and never corrupts README.md. Standard library only, on purpose — same as
refresh.py, so there is nothing to install and nothing to rot.
"""
import contextlib
import io
import os
import re
import socket
import sys
import tempfile
import urllib.error
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import refresh  # noqa: E402

START = "<!-- AUTO:WRITING:START -->"
END = "<!-- AUTO:WRITING:END -->"
SEED_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>'

# A synthetic profile README so the suite does not depend on the live one.
FIXTURE = f"""# Daniel Iwugo
[![badge](https://img.shields.io/badge/-x-000)](https://x.example)
---
### `> cat ./about`
Just your friendly neighbourhood hacker.
### `> tail -f ./writing.log`
{START}
- [seed post on malware analysis](https://seed.example/1)
{END}
---
footer line
<!-- synced: 2020-01-01 -->
"""

VALID_USER = b'{"public_repos":9,"followers":21}'
VALID_REPOS = (b'[{"fork":false,"stargazers_count":4,"language":"Rust"},'
               b'{"fork":false,"stargazers_count":2,"language":"Python"},'
               b'{"fork":true,"stargazers_count":99,"language":"C"}]')


# --------------------------------------------------------------------------
# fixtures and helpers
# --------------------------------------------------------------------------
def rss(items, *, channel_extra=""):
    """Build an RSS 2.0 document. items: list of dicts with title/link/date/cats."""
    body = []
    for it in items:
        cats = "".join(f"<category>{escape(c)}</category>"
                        for c in it.get("cats", ["security"]))
        date = it.get("date", "Mon, 01 Jan 2024 12:00:00 +0000")
        body.append(
            f"<item><title>{escape(it.get('title',''))}</title>"
            f"<link>{escape(it.get('link',''))}</link>"
            f"<pubDate>{escape(date)}</pubDate>{cats}</item>")
    return (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0">'
            f'<channel>{channel_extra}{"".join(body)}</channel></rss>').encode()


def atom(items):
    body = "".join(
        f"<entry><title>{escape(it['title'])}</title>"
        f"<link href=\"{escape(it['link'])}\"/></entry>" for it in items)
    return (f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
            f'{body}</feed>').encode()


class Fetcher:
    """Stand-in for refresh.fetch, dispatching on URL."""

    def __init__(self, feed=None, per_feed=None, feed_exc=None,
                 user=VALID_USER, repos=VALID_REPOS, api_exc=None):
        self.feed, self.per_feed, self.feed_exc = feed, per_feed, feed_exc
        self.user, self.repos, self.api_exc = user, repos, api_exc

    def __call__(self, url, token=None):
        if "api.github.com" in url:
            if self.api_exc:
                raise self.api_exc
            return self.repos if "/repos" in url else self.user
        if self.per_feed is not None:
            val = self.per_feed.get(url)
            if isinstance(val, BaseException):
                raise val
            return val
        if self.feed_exc:
            raise self.feed_exc
        return self.feed


class Result:
    def __init__(self, readme, stats, log, err):
        self.readme, self.stats, self.log, self.err = readme, stats, log, err


def run(fetch, readme=FIXTURE, stats=SEED_SVG):
    """Run refresh.main() in a throwaway sandbox; never raises."""
    d = Path(tempfile.mkdtemp())
    (d / "README.md").write_text(readme, encoding="utf-8")
    (d / "assets").mkdir()
    (d / "assets" / "stats.svg").write_text(stats, encoding="utf-8")
    cwd = os.getcwd()
    os.chdir(d)
    refresh.fetch = fetch
    buf = io.StringIO()
    err = None
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            refresh.main()
    except Exception as e:  # noqa: BLE001 - the whole point is to catch it
        err = e
    finally:
        os.chdir(cwd)
    return Result((d / "README.md").read_text(encoding="utf-8"),
                  (d / "assets" / "stats.svg").read_text(encoding="utf-8"),
                  buf.getvalue(), err)


def block_of(text):
    m = re.search(re.escape(START) + r"(.*?)" + re.escape(END), text, re.S)
    return m.group(1) if m else None


def assert_no_crash(res):
    assert res.err is None, f"main() raised {type(res.err).__name__}: {res.err}"


def assert_healthy_block(text):
    """Exactly one marker pair, and every block line is a clean list item."""
    s, e = text.count(START), text.count(END)
    assert s == 1 and e == 1, f"marker count START={s} END={e}, want 1/1"
    blk = block_of(text)
    assert blk is not None, "writing block not extractable"
    line = re.compile(r"- \[[^\[\]\n]*\]\(https?://[^\s()<>]+\)")
    for ln in blk.splitlines():
        if ln.strip():
            assert line.fullmatch(ln), f"malformed block line: {ln!r}"


def outside(text):
    """README with the writing-block body and synced date neutralised, so
    two values can be compared for 'everything else stayed identical'."""
    t = re.sub(re.escape(START) + r".*?" + re.escape(END), "<BLOCK>", text, flags=re.S)
    return re.sub(r"<!-- synced:.*?-->", "<SYNCED>", t)


def links_in(text):
    return re.findall(r"\]\((https?://[^\s()<>]+)\)", block_of(text) or "")


# --------------------------------------------------------------------------
# test registry
# --------------------------------------------------------------------------
TESTS = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# ---- unit: clean_title ---------------------------------------------------
@test("clean_title collapses newlines and tabs to single spaces")
def _():
    assert refresh.clean_title("a\nb\tc\r\n  d") == "a b c d"


@test("clean_title drops control characters")
def _():
    assert refresh.clean_title("a\x00\x07\x1bb") == "ab"


@test("clean_title strips angle brackets (kills HTML and AUTO markers)")
def _():
    out = refresh.clean_title(f"x {END} y {START} z")
    assert "<" not in out and ">" not in out
    assert START not in out and END not in out


@test("clean_title neutralises markdown link brackets")
def _():
    assert refresh.clean_title("a [b] c") == "a (b) c"


@test("clean_title caps length around 100 chars")
def _():
    assert len(refresh.clean_title("A" * 9000)) <= 100


@test("clean_title handles empty / None / whitespace-only")
def _():
    assert refresh.clean_title(None) == ""
    assert refresh.clean_title("") == ""
    assert refresh.clean_title("  \n\t  ") == ""


@test("clean_title preserves unicode and ampersands")
def _():
    out = refresh.clean_title("Café & 中文 résumé 🎉")
    assert "&" in out and "中文" in out and "🎉" in out


# ---- unit: clean_link ----------------------------------------------------
@test("clean_link keeps a plain https URL")
def _():
    assert refresh.clean_link("https://x.example/a-b_c") == "https://x.example/a-b_c"


@test("clean_link strips query string and fragment")
def _():
    assert refresh.clean_link("https://x.example/a?b=c#d") == "https://x.example/a"


@test("clean_link rejects URLs with parens / spaces / angle brackets")
def _():
    for bad in ("https://x.example/a)b", "https://x.example/a b",
                "https://x.example/<a>", "https://x.example/(a)"):
        assert refresh.clean_link(bad) == "", bad


@test("clean_link rejects non-http schemes and relative links")
def _():
    for bad in ("javascript:alert(1)", "ftp://x.example/a", "/relative",
                "mailto:a@b.c", "https://", "", None):
        assert refresh.clean_link(bad) == "", repr(bad)


# ---- unit: parse_date ----------------------------------------------------
@test("parse_date returns datetime.min for garbage and empty input")
def _():
    import datetime as dt
    assert refresh.parse_date("not a date") == dt.datetime.min
    assert refresh.parse_date("") == dt.datetime.min
    assert refresh.parse_date(None) == dt.datetime.min


@test("parse_date parses a valid RFC-822 date")
def _():
    assert refresh.parse_date("Mon, 01 Jan 2024 12:00:00 +0000").year == 2024


# ---- unit: stats_svg -----------------------------------------------------
@test("stats_svg output is well-formed XML for normal input")
def _():
    s = {"repos": 9, "stars": 6, "followers": 21, "langs": [("Rust", 2), ("Python", 1)]}
    ET.fromstring(refresh.stats_svg(s, "2026-05-21"))


@test("stats_svg escapes language names containing < > & \" '")
def _():
    s = {"repos": 1, "stars": 0, "followers": 0, "langs": [('<b>&"evil\'', 1)]}
    ET.fromstring(refresh.stats_svg(s, "2026-05-21"))  # must not raise


@test("stats_svg survives huge numbers and the default language")
def _():
    s = {"repos": 999999, "stars": 888888, "followers": 777777, "langs": [("—", 1)]}
    ET.fromstring(refresh.stats_svg(s, "2026-05-21"))


# ---- integration: transport / feed failures ------------------------------
@test("both feeds and the API unreachable -> no crash, block preserved")
def _():
    res = run(Fetcher(feed_exc=urllib.error.URLError("down"),
                      api_exc=urllib.error.URLError("down")))
    assert_no_crash(res)
    assert_healthy_block(res.readme)
    assert "seed.example/1" in res.readme  # last-known-good kept
    assert res.stats == SEED_SVG           # old card untouched


@test("feed returns an HTML error page -> degrades, block preserved")
def _():
    res = run(Fetcher(feed=b"<html><body>Just a moment...</body></html>"))
    assert_no_crash(res)
    assert_healthy_block(res.readme)
    assert "seed.example/1" in res.readme


@test("feed returns binary junk -> degrades cleanly")
def _():
    res = run(Fetcher(feed=b"\x00\x01\xff\xfe not xml"))
    assert_no_crash(res)
    assert_healthy_block(res.readme)


@test("feed valid XML but empty -> block preserved")
def _():
    res = run(Fetcher(feed=rss([])))
    assert_no_crash(res)
    assert_healthy_block(res.readme)
    assert "seed.example/1" in res.readme


@test("feed times out -> degrades cleanly")
def _():
    res = run(Fetcher(feed_exc=socket.timeout("timed out")))
    assert_no_crash(res)
    assert_healthy_block(res.readme)


@test("one feed up, one feed down -> posts from the healthy feed still land")
def _():
    good = rss([{"title": "live security writeup", "link": "https://good.example/p"}])
    per = {refresh.FEEDS[0]: good,
           refresh.FEEDS[1]: urllib.error.URLError("down")}
    res = run(Fetcher(per_feed=per))
    assert_no_crash(res)
    assert_healthy_block(res.readme)
    assert "good.example/p" in res.readme


@test("items with no category are filtered out (on_topic gate)")
def _():
    feed = rss([{"title": "no category here", "link": "https://x.example/p", "cats": []}])
    res = run(Fetcher(feed=feed))
    assert_no_crash(res)
    assert "x.example/p" not in res.readme  # filtered
    assert "seed.example/1" in res.readme   # seed kept


@test("off-topic items are filtered out")
def _():
    feed = rss([{"title": "my sourdough recipe", "link": "https://x.example/bread",
                 "cats": ["cooking", "lifestyle"]}])
    res = run(Fetcher(feed=feed))
    assert_no_crash(res)
    assert "x.example/bread" not in res.readme


@test("Atom-format feed (<entry>) -> no crash, block preserved")
def _():
    res = run(Fetcher(feed=atom([{"title": "atom post", "link": "https://x.example/a"}])))
    assert_no_crash(res)
    assert_healthy_block(res.readme)


@test("feed with 1000 items -> at most MAX_POSTS land in the block")
def _():
    feed = rss([{"title": f"security item {i}", "link": f"https://x.example/{i}"}
                for i in range(1000)])
    res = run(Fetcher(feed=feed))
    assert_no_crash(res)
    lines = [ln for ln in block_of(res.readme).splitlines() if ln.strip()]
    assert len(lines) <= refresh.MAX_POSTS, f"{len(lines)} lines"


# ---- integration: adversarial post content -------------------------------
@test("title carrying AUTO:WRITING markers cannot corrupt README (run x2)")
def _():
    poison = f"Pwned {END} tail {START} more"
    feed = rss([{"title": poison, "link": "https://x.example/p"}])
    res1 = run(Fetcher(feed=feed))
    assert_healthy_block(res1.readme)
    # feed the already-refreshed README straight back in
    res2 = run(Fetcher(feed=feed), readme=res1.readme)
    assert_no_crash(res2)
    assert_healthy_block(res2.readme)


@test("title with newlines / tabs stays on a single list line")
def _():
    feed = rss([{"title": "line one\nline two\tand more\r\nend",
                 "link": "https://x.example/p"}])
    res = run(Fetcher(feed=feed))
    assert_no_crash(res)
    assert_healthy_block(res.readme)


@test("title with HTML / script tags is neutralised")
def _():
    feed = rss([{"title": "<script>alert(1)</script><img src=x onerror=y>",
                 "link": "https://x.example/p"}])
    res = run(Fetcher(feed=feed))
    assert_no_crash(res)
    assert_healthy_block(res.readme)
    assert "<script" not in block_of(res.readme)


@test("8000-character title cannot bloat the block")
def _():
    feed = rss([{"title": "security " + "A" * 8000, "link": "https://x.example/p"}])
    res = run(Fetcher(feed=feed))
    assert_no_crash(res)
    assert_healthy_block(res.readme)
    longest = max(len(ln) for ln in block_of(res.readme).splitlines())
    assert longest < 200, f"line length {longest}"


@test("link with ) ( < > or spaces -> that post is skipped")
def _():
    feed = rss([{"title": "security bad link", "link": "https://x.example/a)(b c<d>"},
                {"title": "security good link", "link": "https://x.example/ok"}])
    res = run(Fetcher(feed=feed))
    assert_no_crash(res)
    assert_healthy_block(res.readme)
    assert "x.example/ok" in res.readme
    assert "a)(b" not in res.readme


@test("javascript: link -> that post is skipped")
def _():
    feed = rss([{"title": "security js link", "link": "javascript:alert(document.cookie)"}])
    res = run(Fetcher(feed=feed))
    assert_no_crash(res)
    assert_healthy_block(res.readme)
    assert "javascript:" not in res.readme


@test("markdown-formatting characters in a title do not break the block")
def _():
    feed = rss([{"title": "security `code` *bold* _it_ #h | pipe",
                 "link": "https://x.example/p"}])
    res = run(Fetcher(feed=feed))
    assert_no_crash(res)
    assert_healthy_block(res.readme)


@test("link-breakout attempt in a title is neutralised")
def _():
    feed = rss([{"title": "security a](javascript:evil)[b", "link": "https://x.example/p"}])
    res = run(Fetcher(feed=feed))
    assert_no_crash(res)
    assert_healthy_block(res.readme)
    assert "javascript:evil" in block_of(res.readme)  # inert text, not a link
    assert "](javascript" not in block_of(res.readme)


@test("whitespace-only title -> post skipped")
def _():
    feed = rss([{"title": "   \n\t  ", "link": "https://x.example/p"}])
    res = run(Fetcher(feed=feed))
    assert_no_crash(res)
    assert "x.example/p" not in res.readme


@test("duplicate links across feeds are de-duplicated")
def _():
    dup = rss([{"title": "security same", "link": "https://x.example/dup"}])
    res = run(Fetcher(per_feed={refresh.FEEDS[0]: dup, refresh.FEEDS[1]: dup}))
    assert_no_crash(res)
    assert links_in(res.readme).count("https://x.example/dup") <= 1


@test("malformed pubDate still yields a usable post")
def _():
    feed = rss([{"title": "security bad date", "link": "https://x.example/p",
                 "date": "not a real date"}])
    res = run(Fetcher(feed=feed))
    assert_no_crash(res)
    assert_healthy_block(res.readme)
    assert "x.example/p" in res.readme


# ---- integration: GitHub API failures ------------------------------------
@test("GitHub API down -> stats card kept, README still refreshed")
def _():
    res = run(Fetcher(feed=rss([{"title": "security x", "link": "https://x.example/p"}]),
                      api_exc=urllib.error.URLError("down")))
    assert_no_crash(res)
    assert res.stats == SEED_SVG          # last-known-good card kept
    assert "synced" in res.readme.lower()


@test("API returns an error object for /repos -> stats skipped, not zeroed")
def _():
    res = run(Fetcher(feed=rss([]), repos=b'{"message":"API rate limit exceeded"}'))
    assert_no_crash(res)
    assert res.stats == SEED_SVG          # not overwritten with zeros


@test("API returns an error object for /users -> stats skipped")
def _():
    res = run(Fetcher(feed=rss([]), user=b'{"message":"Not Found"}'))
    assert_no_crash(res)
    assert res.stats == SEED_SVG


@test("repos payload containing non-dict junk is tolerated")
def _():
    res = run(Fetcher(feed=rss([]), repos=b'[null,"oops",{"fork":false,"language":"Rust"}]'))
    assert_no_crash(res)
    ET.fromstring(res.stats)              # a valid card was still produced


@test("user payload lacking public_repos is treated as an error -> card kept")
def _():
    res = run(Fetcher(feed=rss([]), user=b"{}"))
    assert_no_crash(res)
    assert res.stats == SEED_SVG


@test("valid API response -> stats card regenerated and well-formed")
def _():
    res = run(Fetcher(feed=rss([])))
    assert_no_crash(res)
    ET.fromstring(res.stats)
    assert res.stats != SEED_SVG
    assert ">9<" in res.stats             # public_repos surfaced


# ---- integration: README structure / idempotency -------------------------
@test("running twice on the same day is idempotent")
def _():
    feed = rss([{"title": "security stable", "link": "https://x.example/s"}])
    res1 = run(Fetcher(feed=feed))
    res2 = run(Fetcher(feed=feed), readme=res1.readme)
    assert res1.readme == res2.readme, "output not stable across runs"


@test("refresh only ever touches the writing block and the synced stamp")
def _():
    feed = rss([{"title": "security scoped", "link": "https://x.example/s"}])
    res = run(Fetcher(feed=feed))
    assert_no_crash(res)
    assert outside(FIXTURE) == outside(res.readme), "content outside the block changed"


@test("README with no AUTO markers -> no crash, body left intact")
def _():
    plain = "# profile\n\nno markers here\n\n<!-- synced: 2020-01-01 -->\n"
    res = run(Fetcher(feed=rss([{"title": "security x", "link": "https://x.example/p"}])),
              readme=plain)
    assert_no_crash(res)
    assert "no markers here" in res.readme


@test("README with no synced stamp -> stamp gets appended")
def _():
    nostamp = FIXTURE.replace("<!-- synced: 2020-01-01 -->", "")
    res = run(Fetcher(feed=rss([])), readme=nostamp)
    assert_no_crash(res)
    assert "<!-- synced:" in res.readme


@test("START marker without END -> no crash, block left alone")
def _():
    broken = FIXTURE.replace(END, "")
    res = run(Fetcher(feed=rss([{"title": "security x", "link": "https://x.example/p"}])),
              readme=broken)
    assert_no_crash(res)


@test("empty README -> no crash, synced stamp added")
def _():
    res = run(Fetcher(feed=rss([])), readme="")
    assert_no_crash(res)
    assert "<!-- synced:" in res.readme


@test("feeds going down after a good run keeps the last good posts")
def _():
    good = rss([{"title": "security fresh post", "link": "https://x.example/fresh"}])
    res1 = run(Fetcher(feed=good))
    assert "x.example/fresh" in res1.readme
    # next week: every feed is down
    res2 = run(Fetcher(feed_exc=urllib.error.URLError("down")), readme=res1.readme)
    assert_no_crash(res2)
    assert "x.example/fresh" in res2.readme, "last-known-good posts were wiped"


# --------------------------------------------------------------------------
def main():
    print("=" * 70)
    print(f"  refresh.py chaos suite — {len(TESTS)} tests")
    print("=" * 70)
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            passed += 1
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}\n          {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {name}\n          {type(e).__name__}: {e}")
    print("-" * 70)
    print(f"  {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
