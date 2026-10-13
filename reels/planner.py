"""Bounded, local heuristic planning. No semantic video model or paid API."""
import math
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .editor import Options, edit, probe, run

MAX_TOTAL = 100 * 1024 * 1024
TARGETS = (30, 45, 60)
MODES = ('トーク', 'Vlog', 'おまかせ')


@contextmanager
def staged(files, bgm=None):
    if not 1 <= len(files) <= 5:
        raise ValueError('動画は1〜5本にしてください')
    sizes = [f.size for f in files]
    if any(s <= 0 or s > 50 * 1024 * 1024 for s in sizes):
        raise ValueError('各動画は50MB以下にしてください')
    if bgm and bgm.size > 10 * 1024 * 1024:
        raise ValueError('複数編集のBGMは10MB以下です')
    if sum(sizes) + (bgm.size if bgm else 0) > MAX_TOTAL:
        raise ValueError('動画とBGMの合計は100MB以下です')
    with tempfile.TemporaryDirectory(prefix='reels-plan-') as directory:
        root = Path(directory)
        paths, infos = [], []
        for i, file in enumerate(files):
            suffix = Path(file.name).suffix.lower()
            if suffix not in {'.mp4', '.mov', '.webm', '.mkv', '.avi'}:
                raise ValueError('未対応の動画形式です')
            path = root / f'input{i}{suffix}'
            file.seek(0)
            with path.open('wb') as dst:
                shutil.copyfileobj(file, dst, 1024 * 1024)
            file.seek(0)
            info = probe(path)
            duration = float(info['format']['duration'])
            if not math.isfinite(duration) or not 0 < duration <= 60:
                raise ValueError('各動画は60秒以下です')
            video = next((s for s in info['streams'] if s['codec_type'] == 'video'), None)
            if video is None or video['width'] * video['height'] > 3840 * 2160:
                raise ValueError('動画トラックが必要です。解像度は4K以下にしてください')
            paths.append(path)
            infos.append(info)
        if sum(float(i['format']['duration']) for i in infos) > 180:
            raise ValueError('動画の合計時間は180秒以下です')
        music = None
        if bgm:
            suffix = Path(bgm.name).suffix.lower()
            if suffix not in {'.wav', '.mp3', '.m4a', '.ogg', '.flac'}:
                raise ValueError('未対応のBGM形式です')
            music = root / ('music' + suffix)
            bgm.seek(0)
            with music.open('wb') as dst:
                shutil.copyfileobj(bgm, dst, 1024 * 1024)
            bgm.seek(0)
        yield root, paths, infos, music


def speech_score(text):
    keywords = ('大切', 'おすすめ', 'ポイント', '理由', '結論', 'まず', 'コツ', '注意')
    return 1 + sum(word in text for word in keywords) + bool(re.search(r'\d', text))


def choose(candidates, target, mode):
    selected, used = [], 0.0
    for item in sorted(candidates, key=lambda x: (-x['score'], x['素材番号'], x['開始秒'])):
        if used >= target - 0.1:
            break
        a, b = item['開始秒'], item['終了秒']
        if any(s['素材番号'] == item['素材番号'] and max(a, s['開始秒']) < min(b, s['終了秒']) for s in selected):
            continue
        length = min(b - a, target - used)
        if length < 0.5:
            continue
        selected.append({**item, '終了秒': round(a + length, 3)})
        used += length
    # Preserve chronology for speech; visual proposals favor higher scores first.
    if mode != 'Vlog':
        selected.sort(key=lambda x: (x['素材番号'], x['開始秒']))
    return [{'採用': True, '順番': i + 1, **{k: v for k, v in row.items() if k != 'score'}} for i, row in enumerate(selected)]


def propose(files, mode, target, progress=lambda _: None):
    if mode not in MODES or target not in TARGETS:
        raise ValueError('編集モード・完成尺が不正です')
    candidates = []
    model = None
    with staged(files) as (root, paths, infos, _):
        for index, (path, info) in enumerate(zip(paths, infos), 1):
            progress(f'素材{index}を分析しています')
            duration = float(info['format']['duration'])
            if mode != 'トーク':
                # 2fps, 160px proxy: frame differences, not object/scene understanding.
                import subprocess
                result = subprocess.run(['ffmpeg', '-hide_banner', '-threads', '2', '-i', str(path), '-an',
                    '-vf', "scale=160:-2,fps=2,select='gt(scene,0.12)',showinfo", '-f', 'null', '-'], capture_output=True, text=True)
                if result.returncode:
                    raise RuntimeError('映像分析に失敗しました')
                changes = [float(t) for t in re.findall(r'pts_time:([\d.]+)', result.stderr)]
                for a in range(0, math.ceil(duration), 4):
                    b = min(duration, a + 4)
                    if b - a < 0.5:
                        continue
                    score = 1 + sum(a <= t < b for t in changes)
                    candidates.append({'素材番号': index, '開始秒': float(a), '終了秒': b,
                        'score': score, '理由': '映像変化・短いカット（意味理解なし）', '発言': ''})
            if mode != 'Vlog' and any(s['codec_type'] == 'audio' for s in info['streams']):
                if model is None:
                    from faster_whisper import WhisperModel
                    model = WhisperModel('tiny', device='cpu', compute_type='int8', cpu_threads=2, num_workers=1)
                wav = root / 'speech.wav'
                run(['ffmpeg', '-v', 'error', '-y', '-i', str(path), '-vn', '-ar', '16000', '-ac', '1', str(wav)])
                segments, _ = model.transcribe(str(wav), language='ja', vad_filter=True, beam_size=1)
                for s in segments:
                    a, b = max(0, s.start), min(duration, s.end)
                    if b - a < 0.5 or not s.text.strip():
                        continue
                    candidates.append({'素材番号': index, '開始秒': round(a, 3), '終了秒': round(b, 3),
                        'score': speech_score(s.text) + (1 if mode == 'おまかせ' else 0),
                        '理由': '発話・キーワード・数字による採点', '発言': s.text.strip()})
                wav.unlink(missing_ok=True)
        rows = choose(candidates, target, mode)
        if not rows:
            raise ValueError('使用区間を選べませんでした。音声を確認するかVlogを選んでください')
        return rows, [float(i['format']['duration']) for i in infos]


def validate(rows, durations, target):
    if target not in TARGETS:
        raise ValueError('完成尺が不正です')
    selected, orders = [], set()
    for row in rows:
        if not row['採用']:
            continue
        try:
            number, order = int(row['素材番号']), int(row['順番'])
            a, b = float(row['開始秒']), float(row['終了秒'])
        except (ValueError, TypeError, OverflowError):
            raise ValueError('使用区間・順番は数値で指定してください') from None
        if number != row['素材番号'] or order != row['順番'] or not 1 <= number <= len(durations):
            raise ValueError('素材番号・順番が不正です')
        if order <= 0 or order in orders or not math.isfinite(a + b) or not 0 <= a < b <= durations[number - 1]:
            raise ValueError('範囲外の使用区間、または重複した順番があります')
        orders.add(order)
        selected.append((order, number - 1, a, b))
    if not selected or len(selected) > 60 or sum(b - a for _, _, a, b in selected) > target + 0.01:
        raise ValueError('採用区間は1〜60個、合計は選んだ完成尺以下にしてください')
    return sorted(selected)


def render_plan(files, rows, target, options, bgm=None, cut_silence=True, progress=lambda _: None):
    with staged(files, bgm) as (root, paths, infos, music):
        selected = validate(rows, [float(i['format']['duration']) for i in infos], target)
        parts = []
        for i, (_, index, a, b) in enumerate(selected):
            progress(f'区間{i + 1}を合成しています')
            part = root / f'part{i}.mp4'
            args = ['ffmpeg', '-y', '-v', 'error', '-ss', str(a), '-i', str(paths[index])]
            audio = any(s['codec_type'] == 'audio' for s in infos[index]['streams'])
            if not audio:
                args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
            args += ['-t', str(b - a), '-map', '0:v:0', '-map', '0:a:0' if audio else '1:a:0',
                '-vf', 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30',
                '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-threads', '2', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-ar', '48000', '-ac', '2', str(part)]
            run(args)
            parts.append(f"file '{part.name}'\n")
        (root / 'parts.txt').write_text(''.join(parts))
        combined = root / 'combined.mp4'
        run(['ffmpeg', '-y', '-v', 'error', '-f', 'concat', '-safe', '1', '-i', str(root / 'parts.txt'), '-c', 'copy', str(combined)])
        output = root / 'result.mp4'
        edit(combined, output, options, music, progress, cut_silence=cut_silence, allow_empty_captions=True, max_duration=target)
        if output.stat().st_size > 50 * 1024 * 1024:
            raise ValueError('出力が50MBを超えました。使用区間を減らしてください')
        return output.read_bytes()
