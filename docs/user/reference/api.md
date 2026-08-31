# API: client & operations

The public surface is one client class plus operation functions that take it.

## Client

::: finvizp.FinvizClient
    options:
      members:
        - "__init__"

## Module operations

Each operation exists as an async function and (where applicable) a sync
twin without the `_async` suffix.

::: finvizp
    options:
      show_root_heading: true
      filters:
        - "!^_"
