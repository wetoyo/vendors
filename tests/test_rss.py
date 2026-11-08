"""Offline tests for the rss vendor: HTTP is monkeypatched, parsing is real."""

import json

import pytest

RSS2 = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel><title>Test RSS</title>
    <item>
      <title>Fed holds rates</title>
      <link>https://example.com/fed</link>
      <description>&lt;p&gt;The &lt;b&gt;FOMC&lt;/b&gt; held rates &amp;amp; signalled patience.&lt;/p&gt;</description>
      <pubDate>Tue, 03 Nov 2026 14:00:00 -0500</pubDate>
      <dc:creator>Jane Doe</dc:creator>
      <guid isPermaLink="false">fed-1</guid>
      <category>Rates</category><category>Fed</category>
    </item>
    <item>
      <title>Oil climbs</title>
      <guid>https://example.com/oil</guid>
      <pubDate>Wed, 04 Nov 2026 09:30:00 GMT</pubDate>
    </item>
    <item><title>No link at all</title></item>
  </channel>
</rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Test Atom</title>
  <entry>
    <title type="html">Bitcoin &lt;em&gt;rallies&lt;/em&gt;</title>
    <link rel="self" href="https://example.org/self/btc"/>
    <link rel="alternate" href="https://example.org/btc"/>
    <id>tag:example.org,2026:btc</id>
    <updated>2026-11-05T12:00:00Z</updated>
    <published>2026-11-05T08:00:00+02:00</published>
    <author><name>Satoshi</name></author>
    <summary type="html">&lt;div&gt;BTC up 5%&lt;/div&gt;</summary>
    <category term="crypto"/>
  </entry>
</feed>"""


@pytest.fixture
def rss(tmp_path, monkeypatch):
    from vendors.rss import RssVendor

    v = RssVendor(data_dir=tmp_path / "rss")
    pages = {}
    monkeypatch.setattr(v, "_get", lambda url: pages[url])
    return v, pages


@pytest.mark.vendor("rss")
def test_rss_parses_rss2_and_atom(rss):
    from vendors.rss import FEEDS

    v, pages = rss
    pages[FEEDS["fed_press"]["url"]] = RSS2
    pages[FEEDS["coindesk"]["url"]] = ATOM
    df = v.fetch("items", feed="fed_press,coindesk")
    assert df["link"].tolist() == ["https://example.com/fed", "https://example.com/oil", "https://example.org/btc"]

    fed = df.iloc[0]
    assert (fed.feed, fed.group, fed.title, fed.author, fed.guid) == (
        "fed_press", "central_banks", "Fed holds rates", "Jane Doe", "fed-1")
    assert fed.summary == "The FOMC held rates & signalled patience."
    assert fed.published == "2026-11-03T19:00:00+00:00"
    assert json.loads(fed.categories) == ["Rates", "Fed"]

    btc = df.iloc[2]
    assert (btc.title, btc.summary, btc.author) == ("Bitcoin rallies", "BTC up 5%", "Satoshi")
    assert btc.published == "2026-11-05T06:00:00+00:00"
    assert json.loads(btc.categories) == ["crypto"]


@pytest.mark.vendor("rss")
def test_rss_group_failing_feed_and_upsert(rss):
    from vendors.rss import GROUPS, FEEDS

    v, pages = rss
    first, *rest = GROUPS["crypto"]
    pages[FEEDS[first]["url"]] = ATOM  # the others raise KeyError -> logged and skipped
    df = v.collect("items", group="crypto")
    assert df["feed"].tolist() == [first]
    v.collect("items", group="crypto")
    assert len(v.load("items")) == 1  # keyed on link

    pages["https://custom.example/feed.xml"] = RSS2
    df = v.fetch("items", url="https://custom.example/feed.xml")
    assert set(df["feed"]) == {"custom.example"} and df["group"].isna().all()

    with pytest.raises(RuntimeError):
        v.fetch("items", feed=rest[0])  # every requested feed failed
    with pytest.raises(KeyError):
        v.fetch("items", feed="nope")


@pytest.mark.vendor("rss")
def test_rss_feeds_and_search(rss):
    from vendors.rss import FEEDS

    v, pages = rss
    feeds = v.collect("feeds")
    assert len(feeds) == len(FEEDS) and "fed_press" in set(feeds["name"])
    assert {"markets", "energy", "central_banks", "macro", "crypto", "general", "tech", "sec"} <= set(feeds["group"])

    for name in FEEDS:
        pages[FEEDS[name]["url"]] = RSS2 if FEEDS[name]["group"] == "central_banks" else ATOM
    hits = v.search("fomc", group="central_banks")
    assert hits["link"].tolist() == ["https://example.com/fed"]
    assert v.search("BITCOIN")["link"].tolist() == ["https://example.org/btc"]
