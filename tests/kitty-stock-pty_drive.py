#!/usr/bin/env python3
# Provenance: Copied from virtual-machines_dinas commit 863fe6c68d8b7dd26ae0f224099b41ffc8a97d44 tests/shell/pty_drive.py
# Adapted for darwin-is-not-a-snowflake stock Kitty shell testing under DINAS-KITTY1-STOCK-BASH1.
"""Drive a command on a pseudo-terminal from a step script on stdin.

Steps, one per line ('#' starts a comment line):
  send TEXT     write TEXT with Python string escapes (\\r, \\t, \\x1b[A)
  line TEXT     write TEXT, wait until the echo settles, then press Enter
                (a whole line arriving at once looks like a paste to ble.sh)
  wait PATH     read output until PATH exists
  sleep SECS    keep reading output for SECS seconds
  ready         wait for output enabling bracketed paste after the last input
                was written: a newly started line editor is accepting input
The raw transcript is written to --transcript. It answers the cursor-position,
status and device-attribute queries a real terminal would, and nothing else:
it is a byte-level terminal stand-in, not a renderer or a Kitty window.
Exit status: child exit status when all steps succeed; 3 on timeout.
"""
import argparse
import codecs
import fcntl
import os
import pty
import select
import struct
import sys
import termios
import time

REPLIES = [(b'\x1b[6n', b'\x1b[1;1R'), (b'\x1b[5n', b'\x1b[0n'),
           (b'\x1b[>c', b'\x1b[>1;10;0c'), (b'\x1b[c', b'\x1b[?62;22c')]


class Session:
    def __init__(self, fd, timeout):
        self.fd, self.timeout = fd, timeout
        self.out, self.scanned, self.alive = b'', 0, True
        self.written = 0  # output length when input was last written

    def pump(self, seconds, until=lambda: False):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if until():
                return True
            if not self.alive:
                time.sleep(0.05)  # closed terminal; the child may not be reaped yet
                continue
            ready, _, _ = select.select([self.fd], [], [], 0.05)
            if not ready:
                continue
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                data = b''
            if not data:
                self.alive = False
                continue
            self.out += data
            self.reply()
        return until()

    def settle(self, quiet=0.4, limit=10):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            before = len(self.out)
            self.pump(quiet)
            if len(self.out) == before:
                return

    def reply(self):
        # Answer each query once, including one split across two reads.
        start = max(0, self.scanned - 3)
        for query, answer in REPLIES:
            index = self.out.find(query, start)
            while index >= 0:
                if index + len(query) > self.scanned:
                    os.write(self.fd, answer)
                index = self.out.find(query, index + len(query))
        self.scanned = len(self.out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transcript', required=True)
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    steps = [line.rstrip('\n') for line in sys.stdin
             if line.strip() and not line.lstrip().startswith('#')]
    pid, fd = pty.fork()
    if pid == 0:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))
        os.execvp(command[0], command)
    session = Session(fd, args.timeout)
    ok = True
    for step in steps:
        verb, _, arg = step.partition(' ')
        if verb in ('send', 'line'):
            session.written = len(session.out)
        if verb == 'send':
            os.write(fd, codecs.decode(arg, 'unicode_escape').encode('latin-1'))
            session.pump(0.2)
        elif verb == 'line':
            os.write(fd, codecs.decode(arg, 'unicode_escape').encode('latin-1'))
            session.settle()
            session.written = len(session.out)
            os.write(fd, b'\r')
            session.pump(0.2)
        elif verb == 'wait':
            ok = session.pump(args.timeout, lambda: os.path.exists(arg))
        elif verb == 'sleep':
            session.pump(float(arg))
        elif verb == 'ready':
            start = session.written
            ok = session.pump(args.timeout, lambda: b'\x1b[?2004h' in session.out[start:])
            session.pump(0.5)
        else:
            raise SystemExit(f'unknown step: {step}')
        if not ok:
            print(f'pty_drive: step failed: {step}', file=sys.stderr)
            break
    reaped = []
    def exited():
        if not reaped:
            child, status = os.waitpid(pid, os.WNOHANG)
            if child:
                reaped.append(status)
        return bool(reaped)
    exited = session.pump(args.timeout if ok else 1, exited)
    with open(args.transcript, 'wb') as stream:
        stream.write(session.out)
    if not exited:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        ok = False
        print('pty_drive: child did not exit', file=sys.stderr)
    status = os.waitstatus_to_exitcode(reaped[0]) if ok else 3
    sys.exit(status if status >= 0 else 128 - status)


if __name__ == '__main__':
    main()
