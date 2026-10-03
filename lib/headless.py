"""Open a page in headless Chrome and wait for its title to report a result, collecting the console meanwhile.

Chrome is driven over the DevTools protocol through --remote-debugging-pipe (JSON messages separated by NUL bytes on
file descriptors 3 and 4), so nothing beyond the standard library is needed and no debugging port is opened. Each run
uses a throwaway profile folder, deleted afterwards: the user's own Chrome profile is never read or written.
"""
import json
import os
import select
import shutil
import subprocess
import tempfile
import time

CHROME_CANDIDATES = [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
]

# WebGL needs a GL implementation; in headless mode that is SwiftShader, which recent Chrome only falls back to
# when allowed explicitly.
CHROME_FLAGS = ['--headless=new', '--remote-debugging-pipe', '--no-first-run', '--no-default-browser-check',
                '--disable-extensions', '--disable-background-networking', '--disable-sync',
                '--disable-component-update', '--disable-default-apps', '--mute-audio', '--use-angle=swiftshader',
                '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--disable-background-timer-throttling',
                '--disable-renderer-backgrounding', '--window-size=960,600']


def find_chrome(explicit=None):
    for path in [explicit, os.environ.get('CHROME')] + CHROME_CANDIDATES + [
            shutil.which('google-chrome'), shutil.which('chromium'), shutil.which('chromium-browser')]:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


class Browser(object):
    """A headless Chrome process and its DevTools pipe."""

    def __init__(self, chrome):
        self.profile = tempfile.mkdtemp(prefix='upm-tools-chrome-')
        to_chrome_r, self._w = os.pipe()        # Chrome reads commands on fd 3
        self._r, from_chrome_w = os.pipe()      # and writes replies and events on fd 4

        def wire_fds():
            # Move both ends out of the way first, so dup2 onto 3 and 4 cannot clobber either of them.
            import fcntl
            a = fcntl.fcntl(to_chrome_r, fcntl.F_DUPFD, 10)
            b = fcntl.fcntl(from_chrome_w, fcntl.F_DUPFD, 10)
            os.dup2(a, 3)
            os.dup2(b, 4)

        self.proc = subprocess.Popen([chrome] + CHROME_FLAGS + ['--user-data-dir=' + self.profile, 'about:blank'],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, close_fds=False, preexec_fn=wire_fds,
                                     start_new_session=True)
        os.close(to_chrome_r)
        os.close(from_chrome_w)
        self._buffer = b''
        self._next_id = 0
        self.events = []

    def send(self, method, params=None, session=None):
        self._next_id += 1
        message = {'id': self._next_id, 'method': method, 'params': params or {}}
        if session:
            message['sessionId'] = session
        os.write(self._w, json.dumps(message).encode('utf-8') + b'\0')
        return self._next_id

    def _read(self, timeout):
        """One message, or None after `timeout` seconds without a complete one."""
        deadline = time.time() + timeout
        while b'\0' not in self._buffer:
            left = deadline - time.time()
            if left <= 0:
                return None
            ready, _, _ = select.select([self._r], [], [], left)
            if not ready:
                return None
            chunk = os.read(self._r, 1 << 16)
            if not chunk:
                raise RuntimeError('Chrome closed the DevTools pipe (exit %s)' % self.proc.poll())
            self._buffer += chunk
        raw, self._buffer = self._buffer.split(b'\0', 1)
        return json.loads(raw.decode('utf-8'))

    def call(self, method, params=None, session=None, timeout=30):
        """Send a command and wait for its reply; events read meanwhile are kept in self.events."""
        wanted = self.send(method, params, session)
        deadline = time.time() + timeout
        while True:
            message = self._read(max(0.0, deadline - time.time()))
            if message is None:
                raise RuntimeError('no reply to %s within %d s' % (method, timeout))
            if message.get('id') == wanted:
                if 'error' in message:
                    raise RuntimeError('%s failed: %s' % (method, message['error'].get('message')))
                return message.get('result', {})
            if 'method' in message:
                self.events.append(message)

    def pump(self, seconds):
        """Collect events for up to `seconds`."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            message = self._read(max(0.0, deadline - time.time()))
            if message is None:
                return
            if 'method' in message:
                self.events.append(message)

    def close(self):
        try:
            if self.proc.poll() is None:
                try:
                    self.send('Browser.close')
                    self.proc.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait(timeout=10)
        finally:
            for fd in (self._w, self._r):
                try:
                    os.close(fd)
                except OSError:
                    pass
            shutil.rmtree(self.profile, ignore_errors=True)


def _console_text(event):
    """The text of a Runtime.consoleAPICalled event, its arguments joined as the console shows them."""
    parts = []
    for arg in event['params'].get('args', []):
        if 'value' in arg:
            parts.append(arg['value'] if isinstance(arg['value'], str) else json.dumps(arg['value']))
        else:
            parts.append(arg.get('description') or arg.get('type', ''))
    return ' '.join(parts)


def open_and_wait(url, chrome, title_prefix, timeout=180, poll=1.0):
    """Load `url` and poll document.title until it starts with `title_prefix` or `timeout` passes.

    Returns {'title', 'reached' (bool), 'seconds', 'console' (list of (level, text)), 'exceptions' (list of text)}.
    Raises RuntimeError when Chrome cannot be driven at all.
    """
    browser = Browser(chrome)
    t0 = time.time()
    try:
        target = browser.call('Target.createTarget', {'url': 'about:blank'})['targetId']
        session = browser.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
        browser.call('Runtime.enable', session=session)
        browser.call('Log.enable', session=session)
        browser.call('Page.enable', session=session)
        browser.call('Page.navigate', {'url': url}, session=session)
        title, reached = '', False
        while time.time() - t0 < timeout:
            browser.pump(poll)
            reply = browser.call('Runtime.evaluate', {'expression': 'document.title', 'returnByValue': True},
                                 session=session)
            title = reply.get('result', {}).get('value') or ''
            if title.startswith(title_prefix):
                reached = True
                browser.pump(0.5)       # the summary's own console line may arrive just after the title
                break
        console, exceptions = [], []
        for e in browser.events:
            if e.get('sessionId') not in (None, session):
                continue
            if e['method'] == 'Runtime.consoleAPICalled':
                console.append((e['params'].get('type', 'log'), _console_text(e)))
            elif e['method'] == 'Runtime.exceptionThrown':
                d = e['params'].get('exceptionDetails', {})
                exceptions.append((d.get('exception') or {}).get('description') or d.get('text', 'exception'))
            elif e['method'] == 'Log.entryAdded':
                entry = e['params'].get('entry', {})
                if entry.get('level') in ('error', 'warning'):
                    console.append((entry.get('level'), '[%s] %s %s' % (entry.get('source', ''), entry.get('text', ''),
                                                                       entry.get('url', '') or '')))
        return {'title': title, 'reached': reached, 'seconds': round(time.time() - t0, 1), 'console': console,
                'exceptions': exceptions}
    finally:
        browser.close()
