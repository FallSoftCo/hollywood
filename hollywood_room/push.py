"""Durable, per-device push attempts; provider delivery is not an attention receipt."""

import asyncio
import logging
import time

import google.auth
from firebase_admin import credentials as firebase_credentials

LOGGER = logging.getLogger(__name__)


class ScopedCredentials(firebase_credentials.Base):
    """Use ADC, federation or impersonation files without requiring a service-account key."""

    def __init__(self, path):
        self.value, _ = google.auth.load_credentials_from_file(
            str(path), scopes=["https://www.googleapis.com/auth/firebase.messaging"]
        )

    def get_credential(self):
        return self.value


class Push:
    def __init__(self, store, credentials, project):
        from firebase_admin import initialize_app, messaging

        self.store, self.messaging = store, messaging
        self.app = initialize_app(
            ScopedCredentials(credentials), options={"projectId": project}, name="hollywood-room"
        )
        self.task = None

    def start(self):
        self.task = asyncio.create_task(self.run())

    async def close(self):
        from firebase_admin import delete_app

        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        delete_app(self.app)

    async def run(self):
        from firebase_admin import exceptions, messaging

        while True:
            with self.store.db:
                self.store.db.execute("""INSERT OR IGNORE INTO push_delivery(event,device)
                    SELECT o.event,d.id FROM push_outbox o CROSS JOIN device d
                    WHERE d.revoked=0 AND o.event>d.after_event""")
            pending = self.store.rows(
                """SELECT p.*,d.token FROM push_delivery p JOIN device d ON d.id=p.device
                WHERE p.state='pending' AND p.next_at<=? AND d.revoked=0 LIMIT 10""",
                (int(time.time()),),
            )
            for item in pending:
                event = self.store.get_event(item["event"])
                attention = self.store.rows(
                    "SELECT state FROM attention WHERE id=?", (item["event"],)
                )
                if int(time.time()) - event["created_at"] > 3600 or (
                    attention and attention[0]["state"] != "open"
                ):
                    with self.store.db:
                        self.store.db.execute(
                            "UPDATE push_delivery SET state='expired' WHERE event=? AND device=?",
                            (item["event"], item["device"]),
                        )
                    continue
                payload = messaging.Message(
                    fid=item["token"],
                    data={"attentionId": str(item["event"]), "deviceId": item["device"]},
                    android=messaging.AndroidConfig(
                        priority="high" if event["kind"] in ("attention", "approval") else "normal",
                        ttl=3600,
                    ),
                )
                try:
                    await asyncio.to_thread(messaging.send, payload, app=self.app)
                except messaging.UnregisteredError:
                    with self.store.db:
                        self.store.db.execute(
                            "UPDATE device SET revoked=1 WHERE id=?", (item["device"],)
                        )
                except (
                    exceptions.FirebaseError,
                    google.auth.exceptions.GoogleAuthError,
                    ValueError,
                    OSError,
                ):
                    LOGGER.warning(
                        "Push provider attempt failed for event %s; retry scheduled", item["event"]
                    )
                    with self.store.db:
                        self.store.db.execute(
                            "UPDATE push_delivery SET attempts=attempts+1,next_at=? WHERE event=? AND device=?",
                            (
                                int(time.time()) + min(3600, 2 ** min(item["attempts"] + 1, 12)),
                                item["event"],
                                item["device"],
                            ),
                        )
                else:
                    with self.store.db:
                        self.store.db.execute(
                            "UPDATE push_delivery SET state='sent',attempts=attempts+1 WHERE event=? AND device=?",
                            (item["event"], item["device"]),
                        )
            await asyncio.sleep(1)
