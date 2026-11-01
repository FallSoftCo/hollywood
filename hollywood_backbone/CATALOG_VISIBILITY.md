# Federated human agent visibility

Human catalogs include active agents in currently readable rooms even when the paired human is not an administrator. This projection exposes identity metadata only; administrative inventory remains administrator-only. Unroomed, inaccessible and inactive agents stay absent. Federation retains only identities canonical at each responding node, excluding foreign references. Room permission revocation applies on the next read.

Qualified through real HTTP/PostgreSQL in tests/integration_visible_room_agents.py, including hidden identities, foreign authority, reference filtering and immediate viewer/peer revocation. Catalog reads create no model work or permission changes.
