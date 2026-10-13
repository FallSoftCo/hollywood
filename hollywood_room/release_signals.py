"""Publication hints: the authenticated manifest remains the source of update truth."""


class ReleaseSignals:
    def __init__(self, store, updates):
        self.store, self.updates = store, updates
        with store.db:
            store.db.execute("""CREATE TABLE IF NOT EXISTS release_signal (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                version INTEGER NOT NULL, digest TEXT
            )""")

    def poll(self):
        release = self.updates.manifest()
        available = release.get("available", False)
        version = release["versionCode"] if available else 0
        digest = release["sha256"] if available else None
        with self.store.db:
            previous = self.store.rows(
                "SELECT version,digest FROM release_signal WHERE singleton=1"
            )
            if not previous:
                # Initial deployment learns the existing release without notifying everyone.
                self.store.db.execute("INSERT INTO release_signal VALUES(1,?,?)", (version, digest))
                return None
            if not available or version <= previous[0]["version"]:
                return None
            event = self.store.event(
                "appUpdate",
                "lobby",
                "hollywood",
                f"Losangelex {release['versionName']} update available",
                {
                    "releaseSha256": digest,
                    "versionCode": version,
                    "versionName": release["versionName"],
                },
            )
            self.store.db.execute("INSERT INTO push_outbox(event) VALUES(?)", (event,))
            self.store.db.execute(
                "UPDATE release_signal SET version=?,digest=? WHERE singleton=1", (version, digest)
            )
            return event
