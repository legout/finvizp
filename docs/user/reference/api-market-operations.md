# Market API operations

These operations cover news, insider feeds, the economic calendar, markets,
artifacts, and the capability manifest.

## News and insider feeds

::: finvizp.news
    options:
      show_source: false
      members:
        - global_news
        - global_news_async
        - publisher_news
        - publisher_news_async

::: finvizp.insider
    options:
      show_source: false
      members:
        - global_insider
        - global_insider_async
        - fund_insider
        - fund_insider_async
        - manager_insider
        - manager_insider_async

## Calendar

::: finvizp.calendar
    options:
      show_source: false
      members:
        - calendar
        - calendar_async
        - calendar_detail
        - calendar_detail_async

## Forex, crypto, and futures

::: finvizp.forex
    options:
      show_source: false

::: finvizp.crypto
    options:
      show_source: false

::: finvizp.futures
    options:
      show_source: false

## Artifacts and capabilities

::: finvizp.artifacts
    options:
      show_source: false
      members:
        - chart_descriptor
        - download_artifact
        - download_artifact_async

::: finvizp.capabilities
    options:
      show_source: false
      members:
        - capabilities
        - capability
        - provisional_defaults
