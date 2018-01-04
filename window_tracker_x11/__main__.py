#!/usr/bin/python

# Python 2/3 compatibility.
from __future__ import print_function

import sys
import os
import threading

from copy import deepcopy

from contextlib import contextmanager

import Xlib
from Xlib import X, XK, display
from Xlib.ext import record
from Xlib.protocol import rq

import datetime
import time
from pathlib import Path
import subprocess

config_dir = Path().home() / '.config' / 'window-track-x11'

min_duration = datetime.timedelta(seconds=5)
max_idle_time = datetime.timedelta(seconds=30)
reporting_interval = datetime.timedelta(seconds=30)

if (config_dir / 'config.py').exists():
    # Change path so we find config
    sys.path.append(str(config_dir))
    from config import min_duration, max_idle_time, reporting_interval

log_file = config_dir / 'log.csv'


def now():
    return datetime.datetime.utcnow()


class WindowInfo:
    def __init__(self):
        self.id = None
        self.cl = None
        self.title = None


def decode_string_property(obj, atom, default, error):
    try:
        window_name = obj.get_full_property(atom, 0)
    except UnicodeDecodeError:  # Apparently a Debian distro package bug
        return error
    else:
        if window_name:
            win_name = window_name.value
            if isinstance(win_name, bytes):
                ss = [win_name]
                if b'\x00' in win_name:
                    ss = win_name.split(b'\x00')
                    
                # Apparently COMPOUND_TEXT is so arcane that this is how
                # tools like xprop deal with receiving it these days
                win_name = [s.decode('latin1', 'replace') for s in ss if s]
            if not isinstance(win_name, list) or len(win_name) > 1:
                return win_name
            else:
                return win_name[0] if win_name else default
        else:
            return default


class GetWindowInfo:
    def __init__(self, disp, root):
        self.disp = disp
        self.root = root

        # Prepare the property names we use so they can be fed into X11 APIs
        self.NET_ACTIVE_WINDOW = disp.intern_atom('_NET_ACTIVE_WINDOW')
        self.NET_WM_NAME = disp.intern_atom('_NET_WM_NAME')  # UTF-8
        self.WM_NAME = disp.intern_atom('WM_NAME')           # Legacy encoding
        self.WM_CLASS = disp.intern_atom('WM_CLASS')           # Legacy encoding

    @contextmanager
    def window_obj(self, win_id):
        """Simplify dealing with BadWindow (make it either valid or None)"""
        window_obj = None
        if win_id:
            try:
                window_obj = self.disp.create_resource_object('window', win_id)
            except Xlib.error.XError:
                pass
        yield window_obj

    def get_active_window_id(self):
        """Return a window_obj for the active window."""
        win = self.root.get_full_property(self.NET_ACTIVE_WINDOW, Xlib.X.AnyPropertyType)
        if win:
            return win.value[0]
        return None

    def _get_window_name_inner(self, win_obj):
        """Simplify dealing with _NET_WM_NAME (UTF-8) vs. WM_NAME (legacy)"""
        for atom in (self.NET_WM_NAME, self.WM_NAME):
            title = decode_string_property(win_obj, atom, '<unnamed window>', "<decoding error>")
            if title is not None:
                return title

        return None

    def get_window_class(self, wobj):
        cl = decode_string_property(wobj, self.WM_CLASS, '<no class>', '<decoding error>')
        return cl

    def get_window_name(self, win_id):
        """Look up the window name for a given X11 window ID"""

        cl = None
        title = None
        with self.window_obj(win_id) as wobj:
            if wobj:
                cl = self.get_window_class(wobj)
                title = self._get_window_name_inner(wobj)
        return cl, title

    def change_attr(self, win_id, **attrs):
        with self.window_obj(win_id) as win:
            win.change_attributes(**attrs)



class WindowTracker(threading.Thread):
    def __init__(self, disp, listener):
        threading.Thread.__init__(self)
        self.disp = disp
        # get the root window
        self.root = disp.screen().root
        self.last = WindowInfo()
        self.window_info = GetWindowInfo(self.disp, self.root)
        self.listener = listener

    def run(self):
        # Listen for _NET_ACTIVE_WINDOW changes
        self.root.change_attributes(event_mask=X.PropertyChangeMask)

        # Prime last_seen with whatever window was active when we started this
        self.last.id = self.window_info.get_active_window_id()
        self.window_info.change_attr(self.last.id, event_mask=Xlib.X.PropertyChangeMask)
        self.last.cl, self.last.title = self.window_info.get_window_name(self.last.id)
        self.listener(self.last)

        while True:  # next_event() sleeps until we get an event
            self.handle_xevent(self.disp.next_event())

    def handle_xevent(self, event):
        # Loop through, ignoring events until we're notified of focus/title change
        if event.type != Xlib.X.PropertyNotify:
            return

        id = None
        title = None
        if event.atom in (self.window_info.NET_ACTIVE_WINDOW, self.window_info.NET_WM_NAME, self.window_info.WM_NAME):
            id = self.window_info.get_active_window_id()
            cl, title = self.window_info.get_window_name(id)

        if id and (id != self.last.id or title != self.last.title):
            if id != self.last.id:
                #listen to props of active window for title changes
                self.window_info.change_attr(self.last.id, event_mask=Xlib.X.NoEventMask)
                self.window_info.change_attr(id, event_mask=Xlib.X.PropertyChangeMask)

            self.last.id = id
            self.last.cl = cl
            self.last.title = title
            self.listener(deepcopy(self.last))




def lookup_keysym(keysym):
    for name in dir(XK):
        if name[:3] == "XK_" and getattr(XK, name) == keysym:
            return name[3:]
    return "[%d]" % keysym


BLOCK_SIZE = 1024


def tail(f, window=1):
    """
    Returns the last `window` lines of file `f` as a list of bytes.
    """
    if window == 0:
        return b''
    BUFSIZE = 1024
    f.seek(0, 2)
    end = f.tell()
    nlines = window + 1
    data = []
    while nlines > 0 and end > 0:
        i = max(0, end - BUFSIZE)
        nread = min(end, BUFSIZE)

        f.seek(i)
        chunk = f.read(nread)
        data.append(chunk)
        nlines -= chunk.count(b'\n')
        end -= nread
    return b'\n'.join(b''.join(reversed(data)).splitlines()[-window:])
    
log_file.parent.mkdir(exist_ok=True)
with log_file.open('a') as f:
    pass


class Reporter:
    def __init__(self):
        self.last_write = now()
        self.last_action = now()
        self.last_idle_start = None
        self.last_idle_end = now()
        self.return_action = now()
        self.windows = []

        def loop():
            while True:
                self.log()
                time.sleep(reporting_interval.total_seconds())
        t = threading.Thread(target=loop)
        t.start()

    @property
    def action_end(self):
        return self.last_action + max_idle_time

    def log(self):
        tnow = now()

        is_idle = tnow > self.action_end
        # set idle start time if just gone idle
        if is_idle and not self.last_idle_start:
            self.last_idle_start = self.action_end

        # check if we're returning from idle
        was_idle = False
        if not is_idle and self.last_idle_start:
            was_idle = True

        # clear windows so other thread can write
        wins = self.windows
        if not wins:
            return
        self.windows = [wins[-1]]

        # set maximum end time
        tend = tnow
        if is_idle:
            tend = self.action_end
        # create time ranges  for windows
        range_wins = []
        for (tstart, win) in reversed(wins):
            w = (tstart, tend, win)
            range_wins.append(w)
            tend = tstart
        range_wins.reverse()

        SEP = ','

        with log_file.open('rb') as f:
            last_written_line = tail(f)
        last_writte_line_nbytes = len(last_written_line)
        last_written_line = last_written_line.decode('utf-8')
        last_written_win = last_written_line.split(SEP)
        
        window_changed = False
        lines = []
        for i, (tstart, tend, win) in enumerate(range_wins):
            # if returning from idle time, set earlier start times to now
            if was_idle and tstart < self.last_idle_end:
                print('--- adjusting tstart of %s (%s--%s) to %s: resuming from idle' % (win.cl[0], tstart, tend, self.last_idle_end))
                tstart = self.last_idle_end
                
            if tstart > self.action_end:
                print('--- filtering %s (%s--%s): started after idle' % (win.cl[0], tstart, tend))
                continue
            if tend < self.last_write:
                print('--- filtering %s (%s--%s): ended before last write' % (win.cl[0], tstart, tend))
                continue
            if tend - tstart < min_duration:
                print('--- filtering %s (%s--%s): less than minimum duration' % (win.cl[0], tstart, tend))
                continue

            s = tstart, tend, *win.cl, win.title
            s = [str(c).replace('"', r'"') for c in s]
            s = ['"%s"' % c for c in s]

            if i == 0:
                for j in (j for j in range(len(s)) if j != 1):
                    if s[j] != last_written_win[j]:
                        window_changed = True
                        break

            s = SEP.join(s)
            print('---', s)
            lines.append(s)

        if not lines:
            return
        lines = '\n'.join(lines) + '\n'
        with log_file.open('ab') as f:
            f.seek(0, 2)
            if not window_changed:
                # amend last line
                f.seek(-1 - last_writte_line_nbytes, 2)
                f.truncate()
            f.write(lines.encode(encoding='utf-8'))

        if was_idle:
            self.last_idle_start = None
        self.last_write = tnow

    def window_changed(self, window):
        n = now()
        print('EVENT:', window.cl[0])
        self.windows.append((n, window))

    def user_activity(self, e):
        n = now()
        is_idle = n > self.action_end
        self.last_action = n
        if is_idle:
            self.last_idle_end = n
            

reporter = Reporter()

local_dpy = display.Display()
record_dpy = display.Display()

def record_callback(reply):
    if reply.category != record.FromServer:
        return
    if reply.client_swapped:
        print("* received swapped protocol data, cowardly ignored")
        return
    if not len(reply.data) or reply.data[0] < 2:
        # not an event
        return

    data = reply.data
    while len(data):
        event, data = rq.EventField(None).parse_binary_value(data, record_dpy.display, None, None)

        if event.type in [X.KeyPress, X.KeyRelease, X.ButtonPress, X.ButtonRelease, X.MotionNotify]:
            reporter.user_activity(event)


w = WindowTracker(local_dpy, reporter.window_changed)
w.start()


# Check if the extension is present
if not record_dpy.has_extension("RECORD"):
    print("RECORD extension not found")
    sys.exit(1)
r = record_dpy.record_get_version(0, 0)
print("RECORD extension version %d.%d" % (r.major_version, r.minor_version))

# Create a recording context; we only want key and mouse events
ctx = record_dpy.record_create_context(
        0,
        [record.AllClients],
        [{
                'core_requests': (0, 0),
                'core_replies': (0, 0),
                'ext_requests': (0, 0, 0, 0),
                'ext_replies': (0, 0, 0, 0),
                'delivered_events': (0, 0),
                'device_events': (X.KeyPress, X.MotionNotify),
                'errors': (0, 0),
                'client_started': False,
                'client_died': False,
        }])
# Enable the context; this only returns after a call to record_disable_context,
# while calling the callback function in the meantime
record_dpy.record_enable_context(ctx, record_callback)
# Finally free the context
record_dpy.record_free_context(ctx)


def disable_context(disp, ctx):
    disp.record_disable_context(ctx)
    disp.flush()
    
