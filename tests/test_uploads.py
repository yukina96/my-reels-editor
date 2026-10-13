import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from reels.editor import Options
from reels.uploads import render_upload, MAX_UPLOAD


class UploadTests(unittest.TestCase):
    def test_isolation_and_cleanup(self):
        roots = []
        def fake_edit(source, output, *args):
            roots.append(source.parent)
            self.assertEqual(source.parent, output.parent)
            output.write_bytes(source.read_bytes())
        with patch('reels.uploads.probe', return_value={'format': {'duration': '2'}}), patch('reels.uploads.edit', side_effect=fake_edit):
            self.assertEqual(render_upload(b'user-a', '.mp4', Options()), b'user-a')
            self.assertEqual(render_upload(b'user-b', '.mp4', Options()), b'user-b')
        self.assertNotEqual(roots[0], roots[1])
        self.assertTrue(all(not p.exists() for p in roots))

    def test_failure_removes_files(self):
        roots = []
        def fail(source, *args):
            roots.append(source.parent)
            raise RuntimeError('failed')
        with patch('reels.uploads.probe', return_value={'format': {'duration': '2'}}), patch('reels.uploads.edit', side_effect=fail):
            with self.assertRaises(RuntimeError):
                render_upload(b'private', '.mp4', Options())
        self.assertFalse(roots[0].exists())

    def test_duration_limit(self):
        with patch('reels.uploads.probe', return_value={'format': {'duration': '61'}}), patch('reels.uploads.edit') as edit:
            with self.assertRaises(ValueError):
                render_upload(b'private', '.mp4', Options())
            edit.assert_not_called()
