"""Run with: streamlit run app.py"""
import base64
from pathlib import Path
import threading
import time

import streamlit as st
import streamlit.components.v1 as components
from reels.editor import Options
from reels.uploads import render_upload

RESULT_TTL = 15 * 60


@st.cache_resource
def processing_lock():
    # No user media or transcription is cached/shared.
    return threading.Lock()


def clear_media():
    st.session_state.pop('result', None)
    st.session_state.pop('result_time', None)
    st.session_state['upload_generation'] = st.session_state.get('upload_generation', 0) + 1


st.set_page_config(page_title='リール動画エディター', page_icon='🎬')
st.title('🎬 リール動画エディター')
st.write('動画を選ぶだけで、無音カット・日本語字幕・縦型 MP4 を自動作成します。')
st.caption('クラウド版では素材をサーバーへ送信します。横長動画は中央で切り抜きます。')
st.info('無料クラウド向け: 動画は60秒・50MB以下、BGMは50MB以下。文字起こしは軽量な tiny を使います。')
if st.button('素材と完成動画を消去', on_click=clear_media):
    st.rerun()
generation = st.session_state.get('upload_generation', 0)
video = st.file_uploader('動画素材', type=['mp4', 'mov', 'mkv', 'webm', 'avi'], key=f'video_{generation}')
bgm = st.file_uploader('BGM（任意）', type=['mp3', 'wav', 'm4a', 'ogg', 'flac'], key=f'bgm_{generation}')
rights = st.checkbox('BGM の著作権・Instagram 投稿への利用条件を確認しました') if bgm else True
with st.expander('編集設定'):
    subtitles = st.checkbox('日本語字幕を入れる', value=True)
    threshold = st.slider('無音判定（dB）', -60, -10, -35)
    silence = st.slider('カット対象の無音の長さ（秒）', 0.2, 5.0, 0.7, 0.1)
    volume = st.slider('BGM の音量', 0.0, 0.5, 0.12, 0.01)
st.caption('初回は無料モデルを取得します。API料金は不要です。完成動画はこのセッションのみで保持し、15分でダウンロード表示を終了します。')
if st.button('動画を作成', type='primary', disabled=not video or not rights):
    lock = processing_lock()
    if not lock.acquire(blocking=False):
        st.warning('現在ほかの処理を実行中です。しばらく待って再実行してください。')
    else:
        st.session_state.pop('result', None)
        try:
            with st.status('編集中', expanded=True) as status:
                result = render_upload(video.getvalue(), Path(video.name).suffix.lower(),
                    Options(model='tiny', subtitles=subtitles, silence_db=threshold,
                            min_silence=silence, bgm_volume=volume),
                    bgm.getvalue() if bgm else None,
                    Path(bgm.name).suffix.lower() if bgm else '.wav', st.write)
                clear_media()
                st.session_state['result'] = result
                st.session_state['result_time'] = time.monotonic()
                status.update(label='完成しました', state='complete')
            st.rerun()
        except ValueError as exc:
            clear_media()
            st.error(str(exc))
        except Exception:
            clear_media()
            # FFmpeg stderr/transcripts/paths are never shown or logged.
            st.error('編集に失敗しました。音声のある短い動画か確認してください。字幕オフで成功する場合はモデル取得・メモリ不足の可能性があります。')
        finally:
            lock.release()


@st.fragment(run_every=60)
def result_panel():
    if 'result' not in st.session_state:
        return
    if time.monotonic() - st.session_state['result_time'] >= RESULT_TTL:
        st.session_state.pop('result', None)
        st.session_state.pop('result_time', None)
        st.info('保存期間が終了しました。必要ならもう一度作成してください。')
        return
    # Native Streamlit media/download URLs are server-wide bearer URLs.
    # Embed bytes in this session's HTML instead; no shared media endpoint.
    encoded = base64.b64encode(st.session_state['result']).decode('ascii')
    components.html(f'<a download="reel.mp4" href="data:video/mp4;base64,{encoded}">📥 完成動画を保存</a>', height=50)
    st.caption('保存した動画をPCで再生し、字幕・音量・切り抜きを確認してください。')


result_panel()
