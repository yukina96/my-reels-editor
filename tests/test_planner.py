import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from reels.editor import Options, probe, run
from reels.planner import choose, validate, staged, propose, render_plan, speech_score


class Upload(io.BytesIO):
    def __init__(self, data, name='clip.mp4'):
        super().__init__(data)
        self.name, self.size = name, len(data)
        self.file_id = name


class PlannerTests(unittest.TestCase):
    def test_selection_and_talk_order(self):
        candidates = [dict(素材番号=1, 開始秒=10., 終了秒=15., score=4, 理由='', 発言=''),
                      dict(素材番号=1, 開始秒=0., 終了秒=5., score=1, 理由='', 発言='')]
        rows = choose(candidates, 30, 'トーク')
        self.assertEqual([r['開始秒'] for r in rows], [0, 10])
        rows = choose(candidates, 30, 'Vlog')
        self.assertEqual(rows[0]['開始秒'], 10)
        self.assertGreater(speech_score('大切なポイントは3つ'), speech_score('こんにちは'))

    def test_targets_and_temporary_cleanup_on_error(self):
        candidates = [dict(素材番号=1, 開始秒=0., 終了秒=60., score=1, 理由='', 発言='')]
        for target in (30, 45, 60):
            rows = choose(candidates, target, 'トーク')
            self.assertEqual(sum(b-a for _, _, a, b in validate(rows, [60], target)), target)
        original = tempfile.TemporaryDirectory
        roots = []
        def temporary(*args, **kwargs):
            result = original(*args, **kwargs)
            roots.append(Path(result.name))
            return result
        with patch('reels.planner.tempfile.TemporaryDirectory', side_effect=temporary), patch('reels.planner.probe', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                with staged([Upload(b'private')]):
                    pass
        self.assertTrue(roots)
        self.assertTrue(all(not p.exists() for p in roots))

    def test_invalid_edits(self):
        row = dict(採用=True, 順番=1, 素材番号=1, 開始秒=0, 終了秒=2)
        for changed in ({'開始秒': -1}, {'終了秒': 61}, {'開始秒': float('nan')}, {'順番': 0}, {'素材番号': 2}):
            with self.assertRaises(ValueError):
                validate([{**row, **changed}], [60], 30)
        with self.assertRaises(ValueError):
            validate([row, row], [60], 30)
        with self.assertRaises(ValueError):
            validate([{**row, '終了秒': 31}], [60], 30)

    def test_input_limits(self):
        with self.assertRaises(ValueError):
            with staged([Upload(b'x') for _ in range(6)]):
                pass
        files = [Upload(b'x') for _ in range(3)]
        for f in files:
            f.size = 40 * 1024 * 1024
        with self.assertRaises(ValueError):
            with staged(files):
                pass
        info = {'format': {'duration': '60'}, 'streams': [{'codec_type': 'video', 'width': 320, 'height': 180}]}
        with patch('reels.planner.probe', return_value=info):
            with self.assertRaises(ValueError):
                with staged([Upload(b'x') for _ in range(4)]):
                    pass

    def test_real_multi_render_and_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = []
            for name, color in [('red', 'red'), ('blue', 'blue')]:
                path = root / (name + '.mp4')
                run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', f'color={color}:size=320x180:duration=2:rate=30',
                     '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(path)])
                files.append(Upload(path.read_bytes(), path.name))
            fake = SimpleNamespace(transcribe=lambda *a, **k: (iter([SimpleNamespace(start=0.1, end=1.8, text='大切なポイントは3つ')]), None))
            with patch('faster_whisper.WhisperModel', return_value=fake):
                for mode in ('トーク', 'Vlog', 'おまかせ'):
                    rows, durations = propose(files, mode, 30)
                    self.assertTrue(rows)
                    self.assertEqual(len(durations), 2)
                    validate(rows, durations, 30)
                rows = [dict(採用=True, 順番=2, 素材番号=1, 開始秒=0, 終了秒=2),
                        dict(採用=True, 順番=1, 素材番号=2, 開始秒=0, 終了秒=2)]
                music = root / 'music.wav'
                run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=220:duration=1', str(music)])
                result = render_plan(files, rows, 30, Options(model='tiny'), Upload(music.read_bytes(), 'music.wav'), False)
            output = root / 'out.mp4'
            output.write_bytes(result)
            info = probe(output)
            video = next(s for s in info['streams'] if s['codec_type'] == 'video')
            self.assertEqual((video['width'], video['height']), (1080, 1920))
            self.assertLessEqual(float(info['format']['duration']), 4.2)
            run(['ffmpeg', '-v', 'error', '-i', str(output), '-f', 'null', '-'])
            import subprocess
            rgb = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(output), '-frames:v', '1', '-vf', 'scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
            self.assertGreater(rgb[2], rgb[0])  # blue source was reordered to first

    def test_video_without_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'silent.mp4'
            run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=green:size=160x90:duration=1', '-c:v', 'libx264', str(path)])
            files = [Upload(path.read_bytes())]
            rows, _ = propose(files, 'Vlog', 30)
            result = render_plan(files, rows, 30, Options(subtitles=False), cut_silence=False)
            self.assertTrue(result)
