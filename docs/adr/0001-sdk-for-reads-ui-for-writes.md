# Read through the QuickBooks SDK; leave writes and attachments to on-screen control

Lookups go through the QuickBooks Desktop SDK (qbXML over COM) from a local MCP server on Jeri's PC, because reading lists and bills off the screen is slow and error-prone. Writes and Attachments stay with the stone-matching plugin's on-screen control: the SDK cannot attach files, and keeping every write behind a human-visible screen keeps Jeri's approval step real.

Read-only is enforced by the server: it rejects any request that is not a query, before anything reaches QuickBooks. A QuickBooks user with a view-only role does **not** enforce it in our setup. Verified on QuickBooks Enterprise 2024 (#4): while QuickBooks and the Company File are open, SDK requests run as whoever is logged into the QuickBooks window, and the integration's "Sign in as" user applies only when QuickBooks is not running (unattended access). A throwaway VendorAdd succeeded with Admin in the window in both modes. So v1 (QuickBooks always open) relies on the server's guard alone; a dedicated view-only user would only matter if unattended access were ever enabled, which is out of scope. The guard limits *what kind* of request goes out; the window user's role limits *what a query can read*, and with Admin in the window that is everything the Company File holds, so field-level stripping in the server is the only thing narrowing reads (see `run_query`).

## Consequences

- The stone-matching plugin currently forbids "an SDK bridge". Letting it use this server for lookups (planned v2) requires revising that rule deliberately, not silently.
