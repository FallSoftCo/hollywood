"""Read-only old/new native preflight metadata contract tests."""
import unittest
from hollywood_backbone.context_native_idle import validate_preflight

class NativePreflightContractTests(unittest.TestCase):
    def old(self):
        return {"state": {"threadId": "thread", "contextId": "context", "purpose": "writer",
            "windowId": "397477fd-549e-46fa-a90b-e33528cbb7cc", "historyVersion": 1,
            "lastStartedTurnId": "original", "activeTurnId": None, "idle": True,
            "heldOperationId": None}, "operationId": "op", "packetSha256": "a" * 64,
            "replacementSha256": "b" * 64, "prefixItemCount": 1,
            "replacementItemCount": 4, "replacementWireBytes": 2000}

    def test_original_and_complete_producer_receipts_are_accepted(self):
        original = self.old()
        validate_preflight(original)
        complete = {**original, "nextWindowId": "497477fd-549e-86fa-a90b-e33528cbb7cc",
                    "producerSha256": "c" * 64}
        complete["state"] = {**original["state"], "producerSha256": "c" * 64}
        validate_preflight(complete)
        validate_preflight({**original, "state": {**original["state"], "producerSha256": None}})

    def test_partial_or_unreviewed_target_is_refused(self):
        for extension in [{"producerSha256": "c" * 64},
                          {"nextWindowId": "497477fd-549e-86fa-a90b-e33528cbb7cc"},
                          {"producerSha256": "c" * 64,
                           "nextWindowId": "497477fd-549e-46fa-a90b-e33528cbb7cc"},
                          {"producerSha256": "c" * 64,
                           "nextWindowId": "497477fd-549e-86fa-a90b-e33528cbb7cc",
                           "state": {**self.old()["state"], "producerSha256": "d" * 64}},
                          {"state": {**self.old()["state"], "producerSha256": "c" * 64}}]:
            with self.subTest(extension=extension), self.assertRaises(ValueError):
                validate_preflight({**self.old(), **extension})

if __name__ == "__main__":
    unittest.main()
