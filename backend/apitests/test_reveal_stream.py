"""RevealStream: a token split across streamed chunks is still revealed whole."""

import unittest

from . import _REPO  # noqa: F401  (sys.path fixup)
from app.privacy.pseudonymizer import Pseudonymizer
from app.privacy.reveal_stream import RevealStream


class RevealStreamTests(unittest.TestCase):
    def setUp(self):
        self.p = Pseudonymizer(b"k" * 32, [{"resource_id": "payments-api-handler"}])
        self.token = self.p.mask_text("payments-api-handler")
        self.reply = f"The function {self.token} is stale. RESULT: remove {self.token}."

    def stream(self, chunks):
        stream = RevealStream(self.p)
        return "".join(stream.feed(c) for c in chunks) + stream.flush()

    def test_every_split_point_reveals_the_same_text(self):
        expected = "The function payments-api-handler is stale. RESULT: remove payments-api-handler."
        for cut in range(len(self.reply) + 1):
            with self.subTest(cut=cut):
                self.assertEqual(self.stream([self.reply[:cut], self.reply[cut:]]), expected)

    def test_one_character_at_a_time(self):
        self.assertEqual(self.stream(list(self.reply)), self.p.reveal(self.reply))

    def test_text_with_no_tokens_streams_without_waiting(self):
        stream = RevealStream(self.p)
        self.assertEqual(stream.feed("Two stale buckets."), "Two stale buckets.")


if __name__ == "__main__":
    unittest.main()
