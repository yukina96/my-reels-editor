"""Session-owned multi-clip workflow."""
from pathlib import Path
import time
import streamlit as st
from .editor import Options
from .planner import MODES, TARGETS, propose, render_plan, staged, validate


def draw(lock, clear_media):
    st.info('最大5本・各60秒/50MB、動画合計180秒、BGM10MB、全体100MB以下。完成尺は上限の目標です。')
    st.caption('選定は発話のキーワード・数字と映像変化による推定です。物語・見どころの意味理解は行いません。')
    generation = st.session_state.get('upload_generation', 0)
    files = st.file_uploader('動画素材（最大5本）', type=['mp4', 'mov', 'mkv', 'avi', 'webm'],
        accept_multiple_files=True, key=f'multi_{generation}')
    bgm = st.file_uploader('BGM（任意・10MB以下）', type=['mp3', 'wav', 'm4a', 'ogg', 'flac'], key=f'music_{generation}')
    rights = st.checkbox('BGMのInstagram利用条件を確認しました') if bgm else True
    mode = st.selectbox('編集モード', MODES)
    target = st.selectbox('完成尺の目標（秒）', TARGETS)
    subtitles = st.checkbox('日本語字幕を入れる', value=True)
    cut = st.checkbox('選択区間の無音をカット', value=mode != 'Vlog', key=f'cut_{mode}')
    volume = st.slider('BGM音量', 0.0, 0.5, 0.12, 0.01)
    signature = (tuple((f.file_id, f.size) for f in files), mode, target)
    if st.session_state.get('plan_signature') != signature:
        for key in ('plan', 'durations', 'plan_time', 'plan_signature'):
            st.session_state.pop(key, None)
    if st.button('編集案を作成', disabled=not files):
        if not lock.acquire(blocking=False):
            st.warning('処理中です。後ほど再実行してください。')
        else:
            try:
                with staged(files, bgm):
                    pass
                with st.status('分析しています', expanded=True):
                    rows, durations = propose(files, mode, target, st.write)
                st.session_state.update(plan=rows, durations=durations, plan_signature=signature, plan_time=time.monotonic())
                st.session_state.pop('result', None)
            except ValueError as exc:
                st.error(str(exc))
            except Exception:
                st.error('分析に失敗しました。短い動画・Vlogモードで試してください。モデル取得やメモリ制限の可能性があります。')
            finally:
                lock.release()
    if 'plan' in st.session_state:
        st.write('使用区間と順番を修正し、内容を確認してから生成してください。')
        for i, f in enumerate(files, 1):
            st.write(f'素材{i}: {f.name}')
        rows = st.data_editor(st.session_state['plan'], num_rows='fixed',
            disabled=['素材番号', '理由', '発言'], hide_index=True, key=f'plan_editor_{st.session_state["plan_time"]}')
        valid = False
        try:
            selected = validate(rows, st.session_state['durations'], target)
            length = sum(b - a for _, _, a, b in selected)
            st.caption(f'選択区間の合計: {length:.1f}秒。無音カットでさらに短くなる場合があります。')
            valid = True
        except ValueError as exc:
            st.warning(str(exc))
        confirmed = st.checkbox('使用区間と順番を確認しました')
        if st.button('確認した編集案で動画を生成', disabled=not valid or not confirmed or not rights):
            if not lock.acquire(blocking=False):
                st.warning('処理中です。後ほど再実行してください。')
            else:
                try:
                    with st.status('動画を生成しています', expanded=True):
                        result = render_plan(files, rows, target, Options(model='tiny', subtitles=subtitles,
                            bgm_volume=volume), bgm, cut, st.write)
                    clear_media()
                    st.session_state['result'] = result
                    st.session_state['result_time'] = time.monotonic()
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
                except Exception:
                    st.error('生成に失敗しました。使用区間を減らすか字幕オフで試してください。')
                finally:
                    lock.release()

    @st.fragment(run_every=60)
    def expiry():
        if 'plan_time' in st.session_state and time.monotonic() - st.session_state['plan_time'] >= 900:
            clear_media()
            st.rerun()
    expiry()
