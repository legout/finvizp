---
title: finvizp
description: Async-first, Arrow-native Finviz for Python.
---

# finvizp

**Finviz for Python** — an unofficial, async-first, Arrow-native Finviz
client built on [fastreq](https://pypi.org/project/fastreq/), direct lxml,
and PyArrow.

```bash
uv add finvizp  # or: pip install finvizp
```

```python
import finvizp

bundle = finvizp.quote("AAPL")  # sync twin; async: quote_async
print(bundle.data.snapshot.num_rows)
```

## Documentation

<div class="grid cards" markdown>

-   :material-rocket-launch: **Tutorials**

    ---

    [Get started](tutorials/get-started.md) ·
    [Symbols & quotes](tutorials/symbols-and-quotes.md)

-   :material-wrench: **How-to guides**

    ---

    [Sync API](how-to/sync-api.md) ·
    [Proxies & cache](how-to/proxies-and-cache.md) ·
    [Your own history](how-to/caller-owned-history.md) ·
    [Migrate from finvizfinance](how-to/migrate-from-finvizfinance.md) ·
    [Live smokes](how-to/live-smokes-and-drift.md)

-   :material-book-open-variant: **Reference**

    ---

    [API](reference/api.md) ·
    [Results](reference/results.md) ·
    [Arrow schemas](reference/schemas-0.1.md) ·
    [Screener](reference/screener.md) ·
    [Groups/maps/events](reference/groups-maps-events.md) ·
    [Markets/artifacts](reference/markets-and-artifacts.md) ·
    [Capabilities](reference/capability-matrix.md)

-   :material-lightbulb-outline: **Explanation**

    ---

    [Access & responsible use](explanation/access-and-responsible-use.md)

</div>

Unofficial project — not affiliated with or endorsed by Finviz. No login
automation, no crawler, no entitlement bypass. See
[access & responsible use](explanation/access-and-responsible-use.md).
