"""Build scrubbed structural news-page fixtures (live evidence: 2026-08-30 probes).

Run with ``uv run python -m tests.fixtures.news._build`` from the repo root.
Emits hand-shaped HTML documents mirroring the verified public news structure.
No live HTML is copied; every value is synthetic.

Verified live structure (2026-08-30, one bounded request per route):

- ``/news.ashx`` answers 301 -> ``/news``; the canonical route serves the page.
- Global page: ``div#news`` > ``table.news_time-table``; its first row carries
  two ``span.news-calendar_heading`` headings (``News``, ``Blogs``); the second
  row nests exactly two category tables (``styled-table-new ... table-fixed``),
  News first, Blogs second, 90 ``tr.news_table-row`` items each. One item row:
  ``td.news_first-time-cell`` (svg ``use`` icon whose fragment names the
  publisher, ``#bloomberg-light``), ``td.news_date-cell`` (``09:20AM`` for
  today, ``Aug-29`` date-only for older items), ``td.news_link-cell`` with one
  ``a.nn-tab-link`` (external href + title). ``/news?v=2`` is the Source view;
  the canonical Time view is the parsed contract.
- Publisher page (``/news/<slug>``, 25 single-segment slugs listed in the
  sitemap child ``t=4``; evidence only, never enumerated by the client): one
  ``table-fixed`` table with 100 ``tr.news_table-row`` items. One item row:
  ``td.news_date-cell`` (``Aug-28`` date-only or relative ``46 min``),
  ``td.news_link-cell`` > ``div.news-badges-container`` holding the
  ``a.tab-link`` title anchor, decorative related-ticker badge anchors
  (ignored), and a ``span.news_date-cell`` publisher display.
- Unknown slugs answer 404. ``robots.txt`` does not disallow ``/news``.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).parent

_ROW_CLASS = (
    "styled-row is-hoverable is-bordered is-rounded is-border-top"
    " is-hover-borders has-color-text news_table-row"
)


def _global_row(publisher: str, when: str, title: str, url: str) -> str:
    icon = (
        f'<td width="30" class="news_first-time-cell"><svg width="22" height="22">'
        f'<rect width="22" height="22" />'
        f'<use href="/assets/dist-icons/icons_news.svg?rev=1#{publisher}-light" />'
        f"</svg></td>"
    )
    when_cell = f'<td width="60" class="text-right news_date-cell color-text is-muted">{when}</td>'
    link_cell = (
        f'<td class="news_link-cell" data-boxover-text="{title}">'
        f'<a href="{url}" target="_blank" class="nn-tab-link" rel="nofollow">{title}</a></td>'
    )
    return f'<tr class="{_ROW_CLASS}">{icon}{when_cell}{link_cell}</tr>'


def _publisher_row(when: str, title: str, url: str, display: str, *, badges: bool = False) -> str:
    badge = (
        ' <a href="/stock?t=PBF" data-boxover-ticker="PBF" '
        'class="fv-label stock-news-label is-opaque is-neutral">'
        '<span class="select-none font-semibold">PBF</span></a>'
        if badges
        else ""
    )
    when_cell = f'<td width="70" class="news_date-cell color-text is-muted text-right">{when}</td>'
    link_cell = (
        f'<td class="news_link-cell"><div class="news-badges-container">'
        f'<a href="{url}" class="tab-link">{title}</a>{badge}'
        f'<span class="news_date-cell color-text is-muted text-right">{display}</span>'
        f"</div></td>"
    )
    return f'<tr class="{_ROW_CLASS}" style="cursor:default">{when_cell}{link_cell}</tr>'


def global_page() -> str:
    """Time-view global page: one News table and one Blogs table."""
    news_rows = "".join(
        [
            _global_row(
                "bloomberg",
                "09:20AM",
                "Markets brace for a quiet week",
                "https://www.bloomberg.com/news/articles/2026-08-30/markets-brace-quiet-week",
            ),
            _global_row(
                "wsj",
                "12:21AM",
                "Sample headline number two",
                "https://www.wsj.com/articles/sample-two",
            ),
            _global_row(
                "reuters",
                "Aug-29",
                "Wire item from yesterday",
                "https://www.reuters.com/article/sample-three",
            ),
        ]
    )
    blog_rows = "".join(
        [
            _global_row(
                "zero-hedge",
                "10:30AM",
                "Blog post one",
                "https://www.zerohedge.com/markets/blog-one",
            ),
            _global_row(
                "the-bear-cave",
                "Aug-27",
                "Blog digest thirty four",
                "https://substack.com/p/the-bear-cave-341",
            ),
        ]
    )

    def category_table(rows: str) -> str:
        return (
            '<table border="0" cellpadding="2" cellspacing="0" width="100%" '
            f'class="styled-table-new is-rounded table-fixed">{rows}</table>'
        )

    return (
        "<!DOCTYPE html><html><head><title>Stock Market News & Blogs</title></head><body>"
        '<div id="news"><div class="news">'
        '<table class="news_time-table" width="100%" cellpadding="0" cellspacing="0" border="0">'
        "<tr>"
        '<td class="text-left align-middle" width="49%">'
        '<span class="news-calendar_heading">News</span></td>'
        '<td width="2%"></td>'
        '<td align="center" valign="top" width="49%">'
        '<span class="news-calendar_heading mb-0">Blogs</span>'
        '<div class="news_view-switch"><p class="news_view-switch-label">View by</p>'
        '<a class="fv-button is-border is-chip-transparent is-small is-active" href="news">Time</a>'
        '<a class="fv-button is-border is-chip-transparent is-small" href="news?v=2">Source</a>'
        "</div></td></tr>"
        '<tr><td align="center" valign="top">'
        + category_table(news_rows)
        + "</td>"
        + '<td align="center" valign="top">'
        + category_table(blog_rows)
        + "</td></tr></table></div></div></body></html>"
    )


def publisher_page() -> str:
    """Explicit publisher page (slug ``zacks``): one table, three items."""
    rows = "".join(
        [
            _publisher_row(
                "46 min",
                "Fresh wire item",
                "/news/386695/fresh-wire-item",
                "Zacks",
                badges=True,
            ),
            _publisher_row(
                "Aug-28", "Value stocks for your watch list", "/news/386695/value-stocks", "Zacks"
            ),
            _publisher_row("Aug-28", "Second date-only item", "/news/386694/second-item", "Zacks"),
        ]
    )
    return (
        "<!DOCTYPE html><html><head><title>Latest News From Zacks</title></head><body>"
        '<div class="news-content">'
        '<table border="0" cellpadding="2" cellspacing="0" width="100%" '
        f'class="styled-table-new is-rounded table-fixed">{rows}</table>'
        "</div></body></html>"
    )


def empty_global_page() -> str:
    """Recognized empty: both category structures present, zero items."""
    empty = (
        '<table border="0" cellpadding="2" cellspacing="0" width="100%" '
        'class="styled-table-new is-rounded table-fixed"></table>'
    )
    return (
        "<!DOCTYPE html><html><head><title>Stock Market News & Blogs</title></head><body>"
        '<div id="news"><table class="news_time-table" width="100%">'
        '<tr><td><span class="news-calendar_heading">News</span></td>'
        '<td><span class="news-calendar_heading mb-0">Blogs</span></td></tr>'
        f"<tr><td>{empty}</td><td>{empty}</td></tr></table></div></body></html>"
    )


def empty_publisher_page() -> str:
    """Recognized empty: the publisher table is present with zero items."""
    return (
        "<!DOCTYPE html><html><head><title>Latest News From Zacks</title></head><body>"
        '<table border="0" cellpadding="2" cellspacing="0" width="100%" '
        'class="styled-table-new is-rounded table-fixed"></table></body></html>'
    )


def write_fixtures() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for name, html in (
        ("global.html", global_page()),
        ("publisher.html", publisher_page()),
    ):
        path = HERE / name
        path.write_text(html, "utf-8")
        out[name] = path
    return out


if __name__ == "__main__":
    for path in write_fixtures().values():
        print(path)
