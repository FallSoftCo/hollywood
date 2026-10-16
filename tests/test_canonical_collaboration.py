"""Pure syntax/scope boundaries; not native collaboration evidence."""
import unittest
from uuid import uuid4

from pydantic import ValidationError

from hollywood_backbone.collaboration import canonical_path, overlaps
from hollywood_backbone.collaboration_models import Acquire


class ScopeTests(unittest.TestCase):
    def test_paths_preserve_literal_scope_and_reject_ambiguous_spellings(self):
        self.assertEqual(canonical_path('src/a_b%.py'), 'src/a_b%.py')
        for value in ('', '/', '/tmp/a', '.', '..', 'a/../b', 'a//b', './a', 'a/',
                      'C:/a', 'a\\b', 'a\x00b', 'a\nb', 'e\u0301.py'):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                canonical_path(value)

    def test_directory_boundaries_do_not_confuse_prefixes_or_sql_wildcards(self):
        directory = {'kind': 'directory', 'relative_path': 'src/a_%'}
        self.assertTrue(overlaps(directory, {'kind': 'file', 'relative_path': 'src/a_%/x'}))
        self.assertFalse(overlaps(directory, {'kind': 'file', 'relative_path': 'src/a_zz/x'}))
        self.assertFalse(overlaps({'kind': 'directory', 'relative_path': 'src/a'},
                                  {'kind': 'file', 'relative_path': 'src/ab/x'}))

    def test_model_arguments_cannot_add_physical_identity_or_boolean_cas(self):
        value = dict(action='acquire', workId=str(uuid4()), workRevision=2, claimId=str(uuid4()),
                     claimRevision=0, briefingSequence=0, note='Actual scope', relativePath='src/x',
                     kind='file', sourceRevision='a' * 40, contentSha256='b' * 64, leaseSeconds=60)
        Acquire.model_validate(value)
        for changed in ({**value, 'hostId': str(uuid4())}, {**value, 'claimRevision': False},
                        {**value, 'sourceRevision': 'main'}, {**value, 'leaseSeconds': True}):
            with self.assertRaises(ValidationError):
                Acquire.model_validate(changed)


if __name__ == '__main__':
    unittest.main()
