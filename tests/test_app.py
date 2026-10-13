import unittest
import time
from pathlib import Path
from streamlit.testing.v1 import AppTest


class AppTests(unittest.TestCase):
    def test_initial_screen(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, '🎬 リール動画エディター')
        self.assertTrue(app.button[1].disabled)
        self.assertFalse(app.selectbox)

    def test_session_results_do_not_cross_sessions_and_expire(self):
        path = str(Path(__file__).resolve().parents[1] / 'app.py')
        first = AppTest.from_file(path)
        first.session_state['result'] = b'private-session-a'
        first.session_state['result_time'] = time.monotonic()
        first.run()
        second = AppTest.from_file(path).run()
        self.assertNotIn('result', second.session_state)
        self.assertEqual(first.session_state['result'], b'private-session-a')
        first.session_state['result_time'] = time.monotonic() - 901
        first.run()
        self.assertNotIn('result', first.session_state)
        self.assertFalse(first.exception)


if __name__ == '__main__':
    unittest.main()
