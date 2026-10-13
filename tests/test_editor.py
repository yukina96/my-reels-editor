import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from reels.editor import Options, ass_time, edit, keep_ranges, probe, run, subtitle_text


class EditingTests(unittest.TestCase):
    def test_silence_ranges(self):
        log = 'silence_start: 1\nsilence_end: 3\nsilence_start: 5'
        self.assertEqual(keep_ranges(log, 7, 0.15), [(0.0, 1.15), (2.85, 5.15), (6.85, 7)])
        self.assertEqual(keep_ranges('', 2, 0.15), [(0, 2)])
        self.assertEqual(keep_ranges('silence_start: 0', 2, 0), [])

    def test_caption_safety_and_time(self):
        self.assertEqual(ass_time(61.25), '0:01:01.25')
        self.assertEqual(ass_time(59.999), '0:01:00.00')
        self.assertNotIn('{', subtitle_text(r'{\pos(0,0)}'))
        self.assertIn(r'\N', subtitle_text('あ' * 19))

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required')
    def test_real_render_with_captions_and_bgm(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, music, output = root / 'input.mp4', root / 'music.wav', root / 'output.mp4'
            run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30:duration=3',
                 '-f', 'lavfi', '-i', "aevalsrc=if(lt(t\\,1)\\,0.3*sin(2*PI*440*t)\\,if(lt(t\\,2)\\,0\\,0.3*sin(2*PI*440*t))):s=48000:d=3",
                 '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)])
            run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=220:duration=1', str(music)])
            fake = SimpleNamespace(transcribe=lambda *a, **k: (iter([SimpleNamespace(start=0.1, end=1.5, text='日本語字幕テスト')]), None))
            with patch('faster_whisper.WhisperModel', return_value=fake):
                edit(source, output, Options(), music)
            info = probe(output)
            video = next(s for s in info['streams'] if s['codec_type'] == 'video')
            self.assertEqual((video['width'], video['height']), (1080, 1920))
            self.assertEqual(video['codec_name'], 'h264')
            self.assertEqual(video['pix_fmt'], 'yuv420p')
            self.assertTrue(any(s['codec_name'] == 'aac' for s in info['streams']))
            self.assertGreater(float(info['format']['duration']), 2)
            self.assertLess(float(info['format']['duration']), 2.6)
            # Decode complete result, not just headers.
            run(['ffmpeg', '-v', 'error', '-i', str(output), '-f', 'null', '-'])
            with self.assertRaises(ValueError):
                edit(source, output)


if __name__ == '__main__':
    unittest.main()
