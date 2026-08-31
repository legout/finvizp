# Python API

This page covers the objects shared by all operations. The operation reference
is split by family so you can find a function without scrolling through the
entire package.

## Transport

::: finvizp.FinvizClient
    options:
      show_source: false
      members_order: source

## Result and access contracts

::: finvizp.FetchResult
    options:
      show_source: false
      members_order: source

::: finvizp.ResultMetadata
    options:
      show_source: false
      members_order: source

::: finvizp.AccessTier
    options:
      show_source: false

::: finvizp.ResultStatus
    options:
      show_source: false

## Compound and artifact results

::: finvizp.QuoteBundle
    options:
      show_source: false

::: finvizp.MapBundle
    options:
      show_source: false

::: finvizp.Artifact
    options:
      show_source: false

## Errors

All public exceptions derive from `FinvizError`. See
[results and errors](results.md) for the practical error table.

::: finvizp.FinvizError
    options:
      show_source: false
      members: false

::: finvizp.FinvizPartialError
    options:
      show_source: false
      members: false

::: finvizp.FinvizQueryError
    options:
      show_source: false
      members: false
