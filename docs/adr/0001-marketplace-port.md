# 0001 One marketplace port, two adapters

**Decision.** All marketplace access goes through `MarketplacePort` behind the Marketplace MCP server;
`MARKETPLACE_MODE` selects `GigSimAdapter` or `UpworkLiveAdapter`.
**Why.** The graph must not know which marketplace it uses (R1); live mode then differs only in
configuration and in what Upwork allows (drafts the user confirms on the platform).
**Consequence.** Upwork tool names are mapped in one table; unmapped capabilities raise instead of guessing.
