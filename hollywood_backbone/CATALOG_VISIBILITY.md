# Federated human agent visibility

Human catalogs include active agents in currently readable rooms even when the paired human is not an administrator. This projection exposes identity metadata only; administrative inventory remains administrator-only. Unroomed, inaccessible and inactive agents stay absent. Federation retains only identities canonical at each responding node, excluding foreign references. Room permission revocation applies on the next read.

Qualified through real HTTP/PostgreSQL in tests/integration_visible_room_agents.py, including hidden identities, foreign authority, reference filtering and immediate viewer/peer revocation. Catalog reads create no model work or permission changes.

Room event streams observe an application shutdown event and finish before HTTP request draining. Idle streams wake directly; reconnect retains the original Last-Event-ID. The same real HTTP/PostgreSQL journey now qualifies an open stream, graceful cleanup and reopen without message replay. This does not cancel or reissue commands or shorten unsettled RPC ownership. Service stop budgets must still cover pending RPC settlement.
