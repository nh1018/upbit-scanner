import base64
import hashlib
import unittest
from freshness_watchdog.reader import decode_file, ObservationError


class LargeBlobTests(unittest.TestCase):
    def fixture(self):
        raw = b'journal fixture\n' * 80000
        sha = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        obj = dict(type='file', encoding='none', size=len(raw), sha=sha, content='')
        blob = dict(encoding='base64', size=len(raw), sha=sha,
                    content=base64.b64encode(raw).decode())
        return raw, obj, blob

    def test_over_one_megabyte_pinned_blob_exact_bytes(self):
        raw, obj, blob = self.fixture()
        class API:
            def get(self, path):
                self.path = path
                return blob
        api = API()
        self.assertEqual(decode_file(api, obj), raw)
        self.assertEqual(api.path, '/git/blobs/' + obj['sha'])

    def test_corruption_size_and_reference_mismatch_rejected(self):
        raw, obj, blob = self.fixture()
        for changed in [dict(blob, content=base64.b64encode(b'x'*len(raw)).decode()),
                        dict(blob, sha='0'*40), dict(blob, size=1),
                        dict(blob, content='!!!')]:
            class API:
                def get(self, path): return changed
            with self.assertRaises(ObservationError): decode_file(API(), obj)

    def test_oversized_file_rejected_before_extra_request(self):
        raw, obj, blob = self.fixture()
        class API:
            def get(self, path): raise AssertionError('must not request')
        with self.assertRaises(ObservationError):
            decode_file(API(), dict(obj, size=2_500_001))

    def test_unavailable_api_fails_closed(self):
        raw, obj, blob = self.fixture()
        class API:
            def get(self, path): raise ObservationError('HTTP 403')
        with self.assertRaises(ObservationError): decode_file(API(), obj)
