"""Local blind annotation: explicit public projection, durable SQLite revisions.

Private source links never pass through HTTP. No dependencies beyond the approved
project environment and Python standard library; no inference or service writes.
"""
import argparse
import hashlib
import hmac
import json
import random
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit
import annotation_ui

from .import_traces import strict_json
from .schemas import Trace

ROOT = Path(__file__).resolve().parents[1]
ERROR_TYPES = ['wrong_route', 'missed_action', 'wrong_tool', 'wrong_arguments',
               'ignored_context', 'clarification_policy', 'unsupported_claim', 'other']
VERDICTS = ['correct', 'incorrect', 'unassessable']


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@contextmanager
def connect(db):
    connection = sqlite3.connect(db, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA synchronous=FULL')
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize(db):
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    with connect(db) as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cases(
          case_id TEXT PRIMARY KEY, position INTEGER UNIQUE NOT NULL,
          source_id TEXT UNIQUE NOT NULL, source_hash TEXT NOT NULL,
          source_json TEXT NOT NULL, step_map TEXT NOT NULL, public_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS revisions(
          case_id TEXT NOT NULL REFERENCES cases(case_id), revision INTEGER NOT NULL,
          label_json TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(case_id,revision));
        ''')


def text_content(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(v, dict) and v.get('type') == 'text'
                                      and isinstance(v.get('text'), str) for v in value):
        # Only the actual text, never arbitrary fields from a rich-content object.
        return '\n'.join(v['text'] for v in value)
    raise ValueError('annotation currently supports textual messages/reasoning only')


def project_trace(trace):
    """Allowlist each public field; raw source IDs and metadata remain private."""
    sources = trace.metadata.get('message_sources')
    if not isinstance(sources, dict) or not all(m.step_id in sources for m in trace.messages):
        raise ValueError('explicit message_sources required to distinguish authored context')
    if any(v not in [None, 'input', 'generate'] for v in sources.values()):
        raise ValueError('unsupported message origin')
    ticket_ids = [ticket for message in trace.messages if message.role == 'system'
                  for ticket in re.findall(r'^Current ticket: ([A-Za-z0-9_-]+)$', text_content(message.content), re.M)]
    if len(ticket_ids) > 1:
        raise ValueError('at most one trusted Current ticket instruction supported')
    messages = []
    step_map = {}
    calls = {}
    started = False
    for message in trace.messages:
        if message.role in ['system', 'developer']:
            continue  # Controlled common rubric, not a model/version-bearing prompt.
        if message.role == 'assistant' and sources[message.step_id] != 'input':
            started = True
        if message.role == 'tool' and not started:
            raise ValueError('tool result in authored prelude needs explicit supported origin')
        mid = f'm{len(messages) + 1:02d}'
        step_map[mid] = message.step_id
        item = {'id': mid, 'role': message.role, 'phase': 'continuation' if started else 'context',
                'authored': sources[message.step_id] == 'input' or message.role == 'user',
                'text': text_content(message.content), 'reasoning': text_content(message.reasoning),
                'calls': [], 'result_for': None, 'tool_name': None}
        for call in message.tool_calls:
            cid = f'c{len(calls) + 1:02d}'
            calls[call.id] = (cid, call.name)
            item['calls'].append({'id': cid, 'name': call.name, 'arguments': call.arguments})
        if message.role == 'tool':
            item['result_for'], item['tool_name'] = calls[message.tool_call_id]
        messages.append(item)
    if not started:
        raise ValueError('no evaluated assistant continuation in this trace')
    public = {'messages': messages}
    capture = trace.metadata.get('annotation_capture_status')
    if capture is not None:
        if capture not in ('complete', 'partial', 'missing'):
            raise ValueError('unsupported annotation capture status')
        public['capture_status'] = capture
    if ticket_ids:
        public['current_ticket'] = ticket_ids[0]
    return public, step_map


def prepare(db, traces, rubric):
    """Refresh is additive/idempotent, never relinks annotations to changed data."""
    initialize(db)
    inputs = []
    for line in Path(traces).read_text().splitlines():
        if line.strip():
            trace = Trace.model_validate(strict_json(line))
            public, step_map = project_trace(trace)
            inputs.append((trace, public, step_map))
    if not inputs or len({t.id for t, _, _ in inputs}) != len(inputs):
        raise ValueError('nonempty input with unique trace IDs required')
    rules = Path(rubric).read_text()
    rubric_hash = hashlib.sha256(rules.encode()).hexdigest()
    # Queue order fixed once; source order/run grouping is not an annotation cue.
    random.SystemRandom().shuffle(inputs)
    with connect(db) as c:
        c.execute('BEGIN IMMEDIATE')
        old = c.execute("SELECT value FROM settings WHERE key='rubric_hash'").fetchone()
        if old and old['value'] != rubric_hash:
            raise ValueError('rubric changed; create a separate workspace, labels retained')
        c.execute("INSERT OR IGNORE INTO settings VALUES('rubric_hash',?)", (rubric_hash,))
        c.execute("INSERT OR IGNORE INTO settings VALUES('rubric',?)", (rules,))
        position = c.execute('SELECT COALESCE(MAX(position),0) FROM cases').fetchone()[0]
        for trace, public, step_map in inputs:
            source = trace.model_dump(mode='json')
            fingerprint = digest(source)
            old_case = c.execute('SELECT source_hash,public_json,step_map FROM cases WHERE source_id=?', (trace.id,)).fetchone()
            if old_case:
                if old_case['source_hash'] != fingerprint:
                    raise ValueError('existing trace identity changed; create a new workspace, labels retained')
                stored_public = strict_json(old_case['public_json'])
                if {k: v for k, v in stored_public.items() if k not in ['id', 'number']} != public or strict_json(old_case['step_map']) != step_map:
                    raise ValueError('public projection changed; migrate labels explicitly in a new workspace')
                continue
            position += 1
            cid = secrets.token_hex(16)
            public.update({'id': cid, 'number': position})
            c.execute('INSERT INTO cases VALUES(?,?,?,?,?,?,?)',
                      (cid, position, trace.id, fingerprint, canonical(source),
                       canonical(step_map), canonical(public)))
    return queue(db)


def latest(c, cid):
    row = c.execute('SELECT revision,label_json,created_at FROM revisions WHERE case_id=? ORDER BY revision DESC LIMIT 1', (cid,)).fetchone()
    return dict(strict_json(row['label_json']), revision=row['revision'], saved_at=row['created_at']) if row else None


def queue(db):
    with connect(db) as c:
        cases = []
        for row in c.execute('SELECT case_id,position FROM cases ORDER BY position').fetchall():
            label = latest(c, row['case_id'])
            cases.append({'id': row['case_id'], 'number': row['position'],
                          'reviewed': label is not None, 'verdict': label['verdict'] if label else None})
        return {'cases': cases, 'total': len(cases), 'reviewed': sum(x['reviewed'] for x in cases)}


def case_data(db, cid):
    with connect(db) as c:
        row = c.execute('SELECT public_json FROM cases WHERE case_id=?', (cid,)).fetchone()
        if not row:
            raise KeyError('unknown case')
        return dict(strict_json(row['public_json']), annotation=latest(c, cid))


class Conflict(ValueError):
    pass


def validate_label(data, public):
    fields = {'verdict', 'annotator', 'evidence_steps', 'error_types', 'comment',
              'scenario_problem', 'scenario_evidence', 'scenario_comment', 'expected_revision'}
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError('annotation fields missing or unknown')
    if data['verdict'] not in VERDICTS:
        raise ValueError('choose a verdict')
    for key, limit in [('annotator', 80), ('comment', 8000), ('scenario_comment', 8000)]:
        if not isinstance(data[key], str) or len(data[key]) > limit:
            raise ValueError(f'invalid {key}')
    if not data['annotator'].strip():
        raise ValueError('annotator name required')
    if type(data['scenario_problem']) is not bool or type(data['expected_revision']) is not int or data['expected_revision'] < 0:
        raise ValueError('invalid revision/scenario flag')
    all_steps = {m['id'] for m in public['messages']}
    error_steps = {m['id'] for m in public['messages'] if m['role'] == 'user' or (m['phase'] == 'continuation' and m['role'] in ['assistant', 'tool'] and not m['authored'])}
    assistant_steps = {m['id'] for m in public['messages'] if m['phase'] == 'continuation' and m['role'] == 'assistant' and not m['authored']}
    for key, allowed in [('evidence_steps', error_steps), ('scenario_evidence', all_steps), ('error_types', set(ERROR_TYPES))]:
        value = data[key]
        if not isinstance(value, list) or any(not isinstance(x, str) for x in value) or len(value) != len(set(value)) or not set(value) <= allowed:
            raise ValueError(f'invalid {key}; use visible eligible steps/types')
    if data['verdict'] == 'incorrect' and not set(data['evidence_steps']) & assistant_steps:
        raise ValueError('incorrect verdict needs at least one evaluated assistant step')
    if data['verdict'] == 'unassessable' and not data['comment'].strip():
        raise ValueError('explain why this trace cannot be assessed')
    if data['verdict'] != 'incorrect' and data['error_types']:
        raise ValueError('only an incorrect verdict may contain assistant error types')
    if data['scenario_problem'] and not data['scenario_comment'].strip():
        raise ValueError('explain the scenario problem separately')
    if not data['scenario_problem'] and (data['scenario_evidence'] or data['scenario_comment'].strip()):
        raise ValueError('enable scenario problem or clear its fields')
    return {k: data[k] for k in fields if k != 'expected_revision'}


def save(db, cid, data):
    with connect(db) as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT public_json FROM cases WHERE case_id=?', (cid,)).fetchone()
        if not row:
            raise KeyError('unknown case')
        label = validate_label(data, strict_json(row['public_json']))
        previous = latest(c, cid)
        revision = previous['revision'] if previous else 0
        if data['expected_revision'] != revision:
            raise Conflict('a newer annotation exists; reload it before saving')
        saved_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        c.execute('INSERT INTO revisions VALUES(?,?,?,?)', (cid, revision + 1, canonical(label), saved_at))
        # Transaction commits before HTTP acknowledges success.
    return dict(label, revision=revision + 1, saved_at=saved_at)


def public_export(db):
    return {'format': 'annotation-public-v1', 'cases': [case_data(db, x['id']) for x in queue(db)['cases']]}


def export_private(db):
    """Analyst CLI only: recover trace/step linkage, never served by HTTP."""
    with connect(db) as c:
        output = []
        for row in c.execute('SELECT * FROM cases ORDER BY position').fetchall():
            label = latest(c, row['case_id'])
            if label:
                step_map = strict_json(row['step_map'])
                source = strict_json(row['source_json'])
                output.append({'case_id': row['case_id'], 'trace_id': row['source_id'],
                               'task_id': source.get('task_id'), 'family_id': source.get('family_id'),
                               'split': source.get('split'), 'source_sha256': row['source_hash'],
                               'rubric_sha256': c.execute("SELECT value FROM settings WHERE key='rubric_hash'").fetchone()[0],
                               'assessor_kind': 'human', 'annotation': label,
                               'original_evidence_steps': [step_map[x] for x in label['evidence_steps']],
                               'original_scenario_evidence': [step_map[x] for x in label['scenario_evidence']]})
        return output


def handler_for(db, port):
    mutation_token = secrets.token_urlsafe(32)
    expected_host = f'127.0.0.1:{port}'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # No labels/source evidence in terminal logs.

        def reply(self, status, data, mime='application/json; charset=utf-8'):
            payload = data if isinstance(data, bytes) else canonical(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(payload)

        def allowed(self):
            return (self.headers.get('Host') == expected_host
                    and self.headers.get('Sec-Fetch-Site') not in ['cross-site']
                    and self.headers.get('Origin') in [None, f'http://{expected_host}'])

        def do_GET(self):
            if not self.allowed():
                return self.reply(403, {'error': 'local same-origin workspace only'})
            path = urlsplit(self.path).path
            try:
                assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                          '/style.css': ('style.css', 'text/css; charset=utf-8')}
                if path in assets:
                    name, mime = assets[path]
                    return self.reply(200, files(annotation_ui).joinpath(name).read_bytes(), mime)
                if path == '/api/session':
                    with connect(db) as c:
                        rules = c.execute("SELECT value FROM settings WHERE key='rubric'").fetchone()[0]
                    return self.reply(200, {'token': mutation_token, 'rubric': rules})
                if path == '/api/queue':
                    return self.reply(200, queue(db))
                if path == '/api/export':
                    return self.reply(200, public_export(db))
                if path.startswith('/api/cases/') and len(path.split('/')) == 4:
                    return self.reply(200, case_data(db, path.split('/')[-1]))
                return self.reply(404, {'error': 'not found'})
            except KeyError:
                return self.reply(404, {'error': 'unknown case'})

        def do_POST(self):
            if (not self.allowed() or not hmac.compare_digest(self.headers.get('X-Annotation-Token', ''), mutation_token)
                    or self.headers.get('Content-Type') != 'application/json'):
                return self.reply(403, {'error': 'local same-origin save required'})
            path = urlsplit(self.path).path
            if not path.startswith('/api/cases/') or len(path.split('/')) != 5 or not path.endswith('/annotation'):
                return self.reply(404, {'error': 'not found'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 65536:
                    raise ValueError('annotation body exceeds limit')
                data = strict_json(self.rfile.read(length).decode('utf-8'))
                return self.reply(200, {'annotation': save(db, path.split('/')[3], data)})
            except Conflict as error:
                return self.reply(409, {'error': str(error)})
            except (ValueError, UnicodeError) as error:
                return self.reply(400, {'error': str(error)})
            except KeyError:
                return self.reply(404, {'error': 'unknown case'})
            except sqlite3.Error:
                return self.reply(503, {'error': 'save failed; keep your draft and retry'})

        def do_OPTIONS(self):
            self.reply(403, {'error': 'cross-origin access disabled'})

    return Handler


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    prepare_parser = sub.add_parser('prepare')
    prepare_parser.add_argument('--db', required=True)
    prepare_parser.add_argument('--traces', required=True)
    prepare_parser.add_argument('--rubric', required=True)
    server_parser = sub.add_parser('serve')
    server_parser.add_argument('--db', required=True)
    server_parser.add_argument('--port', type=int, default=8765)
    private_parser = sub.add_parser('export-private')
    private_parser.add_argument('--db', required=True)
    private_parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        print(canonical(prepare(args.db, args.traces, args.rubric)))
    elif args.command == 'export-private':
        Path(args.output).write_text(canonical(export_private(args.db)) + '\n')
    else:
        if not Path(args.db).exists() or not queue(args.db)['total']:
            raise ValueError('prepare a nonempty workspace first')
        server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(args.db, args.port))
        print(f'Annotation workspace: http://127.0.0.1:{args.port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
