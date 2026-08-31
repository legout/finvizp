# Core API operations

These operations cover symbols, financial statements, and stock quotes.

## Symbols

::: finvizp.symbols
    options:
      show_source: false
      members:
        - symbols
        - symbols_async
        - search_symbols
        - search_symbols_async

## Statements

::: finvizp.statements
    options:
      show_source: false
      members:
        - statements
        - statements_async
        - statements_batch
        - statements_batch_async

## Quotes and projections

::: finvizp.quote
    options:
      show_source: false
      members:
        - quote
        - quote_async
        - snapshot
        - snapshot_async
        - ratings
        - ratings_async
        - news
        - news_async
        - insider
        - insider_async
        - peers
        - peers_async
        - etf_holders
        - etf_holders_async
