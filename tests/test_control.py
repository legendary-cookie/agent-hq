import io
import json
import shlex
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hq.control.audit import ControlLog
from hq.control.pve import ControlError, Pve, login_of, vmid_of
from hq.control.terminal import accept_key, frame, read_frame

HOSTS = [SimpleNamespace(name='ct306-assistant', ssh='zylos@192.0.2.66'), SimpleNamespace(name='ct203-manager', ssh='agentuser@192.0.2.53'),
         SimpleNamespace(name='ct500-secret', ssh='root@1.2.3.4'), SimpleNamespace(name='odd-name', ssh='zylos@x')]


class Runner:
    """Stands in for ssh: records the remote command and answers from a script."""
    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    def __call__(self, args, **kwargs):
        remote = args[-1]
        self.calls.append(remote)
        for needle, (code, out) in self.answers.items():
            if needle in remote:
                return subprocess.CompletedProcess(args, code, out, '' if code == 0 else out)
        return subprocess.CompletedProcess(args, 0, '', '')


class PveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.key, self.known = Path(tmp.name) / 'k', Path(tmp.name) / 'kh'
        self.key.write_text('x')
        self.known.write_text('x')

    def pve(self, runner):
        return Pve(self.key, self.known, HOSTS, runner, host="root@pve.example.invalid")

    def test_proxmox_requires_an_explicit_target(self):
        pve = Pve(self.key, self.known, HOSTS, Runner(), host='')
        self.assertFalse(pve.configured())
        with self.assertRaises(ControlError):
            pve.run(['true'])

    def test_proxmox_uses_configured_target_and_rejects_options(self):
        pve = self.pve(Runner())
        self.assertTrue(pve.configured())
        self.assertEqual(pve.ssh_args()[-1], 'root@pve.example.invalid')
        for target in ('-oProxyCommand=bad', 'root@host bad', 'root@host;bad'):
            self.assertFalse(Pve(self.key, self.known, HOSTS, Runner(), host=target).configured())

    def test_names(self):
        self.assertEqual((vmid_of('ct306-assistant'), vmid_of('odd'), login_of('zylos@x'), login_of('bad user@x')), (306, None, 'zylos', None))

    def test_only_watched_agents_and_never_ct500(self):
        pve = self.pve(Runner())
        self.assertEqual(sorted(pve.agents), ['ct203-manager', 'ct306-assistant'])
        with self.assertRaisesRegex(ControlError, 'only control'):
            pve.power('ct500-secret', 'stop')

    def test_power_runs_one_quoted_pct_command(self):
        runner = Runner()
        self.pve(runner).power('ct306-assistant', 'reboot')
        self.assertEqual(runner.calls, ['pct reboot 306'])
        with self.assertRaises(ControlError):
            self.pve(runner).power('ct306-assistant', 'destroy')

    def test_not_configured_without_a_key(self):
        self.key.unlink()
        with self.assertRaisesRegex(ControlError, 'no Proxmox access'):
            self.pve(Runner()).power('ct306-assistant', 'start')

    def test_containers_report_only_agents(self):
        rows = [{'type': 'lxc', 'vmid': 306, 'status': 'running', 'uptime': 5}, {'type': 'lxc', 'vmid': 500, 'status': 'running'}, {'type': 'qemu', 'vmid': 203}]
        found = self.pve(Runner({'pvesh': (0, json.dumps(rows))})).containers()
        self.assertEqual(list(found), ['ct306-assistant'])
        self.assertEqual(found['ct306-assistant']['status'], 'running')

    def test_session_info_and_restart(self):
        runner = Runner({'tmux ls': (0, 'other\nclaude-alex\n--\nruntime\n')})
        pve = self.pve(runner)
        self.assertEqual(pve.session_info('ct306-assistant'), {'session': 'claude-alex', 'sessions': ['other', 'claude-alex'], 'restartable': True})
        pve.restart_claude('ct306-assistant')
        self.assertIn('pct exec 306 -- sh -c', runner.calls[-1])
        self.assertIn('systemctl restart zylos-claude-runtime', runner.calls[-1])

    def test_session_lookup_never_fails_just_because_parts_are_missing(self):
        runner = Runner()
        self.pve(runner).session_info('ct203-manager')
        self.assertTrue(runner.calls[-1].rstrip("'").endswith('true'))

    def test_upgrade_answers_as_http_1_1(self):
        import io
        from hq.control import terminal
        out = io.BytesIO()
        handler = SimpleNamespace(headers={'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ==', 'Upgrade': 'websocket'}, wfile=out, rfile=io.BytesIO(b''), close_connection=False)
        if not hasattr(__import__('os'), 'openpty'):
            self.skipTest('needs a Unix pseudo-terminal')
        terminal.bridge(handler, ['true'])
        self.assertTrue(out.getvalue().startswith(b'HTTP/1.1 101 Switching Protocols\r\n'))

    def test_no_restart_without_the_runtime_service(self):
        with self.assertRaisesRegex(ControlError, 'Restart the container'):
            self.pve(Runner({'tmux ls': (0, 'claude-main\n--\n')})).restart_claude('ct203-manager')

    def test_message_text_cannot_escape_into_the_shell(self):
        runner = Runner({'tmux ls': (0, 'claude-main\n--\n')})
        self.pve(runner).send_text('ct203-manager', "hi'; rm -rf / #\nsecond line")
        remote = shlex.split(runner.calls[-1])
        self.assertEqual(remote[:7], ['pct', 'exec', '203', '--', 'runuser', '-u', 'agentuser'])
        inner = shlex.split(remote[-1])
        self.assertIn("hi'; rm -rf / # second line", inner)  # Kept as one literal argument, joined onto one line.
        for bad in ('', ' ', 'x' * 4001):
            with self.assertRaises(ValueError):
                self.pve(runner).send_text('ct203-manager', bad)

    def test_terminal_commands(self):
        runner = Runner({'tmux ls': (0, 'claude-alex\n--\n')})
        pve = self.pve(runner)
        watch = pve.terminal_command('ct306-assistant', 'watch')
        self.assertIn('-tt', watch)
        self.assertTrue(watch[-1].endswith('tmux attach -t claude-alex -r'))
        self.assertTrue(pve.terminal_command('ct306-assistant', 'control')[-1].endswith('tmux attach -t claude-alex'))
        self.assertEqual(pve.terminal_command('ct306-assistant', 'root')[-1], 'pct enter 306')

    def test_failures_are_reported(self):
        with self.assertRaisesRegex(ControlError, 'CT is locked'):
            self.pve(Runner({'pct start': (2, 'CT is locked')})).power('ct306-assistant', 'start')


class AuditTests(unittest.TestCase):
    def test_log_and_elevation_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [1000.0]
            log = ControlLog(Path(tmp) / 'log.jsonl', clock=lambda: now[0])
            log.log({'action': 'power_start', 'host': 'a'})
            log.log({'action': 'message_sent', 'host': 'b'})
            self.assertEqual([e['action'] for e in log.recent()], ['message_sent', 'power_start'])
            self.assertFalse(log.elevated('tok'))
            log.elevate('tok')
            self.assertTrue(log.elevated('tok'))
            self.assertFalse(log.elevated('other'))
            now[0] += 15 * 60 + 1
            self.assertTrue(log.elevated('tok'))  # a working day, not 15 minutes
            now[0] += 8 * 3600
            self.assertFalse(log.elevated('tok'))
            with self.assertRaises(ValueError):
                log.elevate('')


class FrameTests(unittest.TestCase):
    def test_accept_key_matches_the_spec_example(self):
        self.assertEqual(accept_key('dGhlIHNhbXBsZSBub25jZQ=='), 's3pPLMBiTxaQ9kYGzzhZRbK+xOo=')

    def masked(self, payload, opcode=1):
        mask = b'\x01\x02\x03\x04'
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        n = len(payload)
        length = bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack('>H', n)
        return bytes([0x80 | opcode]) + length + mask + body

    def test_reads_masked_frames(self):
        message = json.dumps({'type': 'input', 'data': 'ls\r'}).encode()
        self.assertEqual(read_frame(io.BytesIO(self.masked(message))), (1, message))
        long = b'x' * 300
        self.assertEqual(read_frame(io.BytesIO(self.masked(long, 2))), (2, long))

    def test_refuses_unmasked_or_cut_frames(self):
        self.assertEqual(read_frame(io.BytesIO(b'\x81\x02hi')), (None, b''))
        self.assertEqual(read_frame(io.BytesIO(b'\x81')), (None, b''))
        self.assertEqual(read_frame(io.BytesIO(b'\x81\xfe\x01')), (None, b''))  # cut inside the extended length

    def test_writes_frames_of_each_size(self):
        self.assertEqual(frame(b'ab')[:2], b'\x82\x02')
        self.assertEqual(frame(b'a' * 200)[:4], b'\x82\x7e\x00\xc8')
        self.assertEqual(frame(b'a' * 70000)[1], 127)


if __name__ == '__main__':
    unittest.main()
