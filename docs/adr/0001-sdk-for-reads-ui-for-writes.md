# Read through the QuickBooks SDK; leave writes and attachments to on-screen control

Lookups go through the QuickBooks Desktop SDK (qbXML over COM) from a local MCP server on Jeri's PC, because reading lists and bills off the screen is slow and error-prone. Writes and Attachments stay with the stone-matching plugin's on-screen control: the SDK cannot attach files, and keeping every write behind a human-visible screen keeps Jeri's approval step real.

Read-only is enforced twice: the MCP logs into QuickBooks as a dedicated user with a view-only role, and the server rejects any request that is not a query.

## Consequences

- The stone-matching plugin currently forbids "an SDK bridge". Letting it use this server for lookups (planned v2) requires revising that rule deliberately, not silently.
