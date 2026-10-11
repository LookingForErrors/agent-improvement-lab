"""Fixed Polza transport with durable, process-safe cost reservations; no retries."""
import copy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import multiprocessing
import queue
import threading
import os
from pathlib import Path
import stat
import time
import urllib.error
import urllib.request
import uuid

from .import_traces import strict_json

ENDPOINT = 'https://polza.ai/api/v1/chat/completions'
MODEL = 'mistralai/ministral-14b-2512'
PROVIDER = 'mistral'
PRICE = 25.0
CATALOG_PATH = '/api/v1/models/catalog?search=ministral-14b-2512&type=chat&page=1&limit=10'
START_INTERVAL_S = .5
RATE_COOLDOWN_S = 5.0


class PaidStop(RuntimeError):
    pass


class PaidSampleError(RuntimeError):
    """An observed provider rejection of this output; reservation remains held."""
    pass


class PaidRateLimit(PaidSampleError):
    """Exact observed provider429; no retry and no release of unknown cost."""
    pass


class PaidTransportError(PaidSampleError):
    """Bounded network failure; unknown cost stays reserved, no ordinal retry."""
    pass


def _http_child(channel, url, data, key, timeout):
    """Network only: no ledger writes, retries, redirects or proxy discovery."""
    try:
        request = urllib.request.Request(url, data=data,
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key,
                     'Accept-Language': 'en'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            stream = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            stream = error
        with stream:
            parts = []; size = 0
            while True:
                block = stream.read1(65536)
                if not block:
                    break
                size += len(block)
                if size > 4 * 1024 * 1024:
                    raise PaidStop('provider response size limit reached')
                parts.append(block)
            # Redact before any IPC or exception text reaches the owner.
            value = strict_json(b''.join(parts).decode())
            text = json.dumps(value).replace(key, '[REDACTED]')
            channel.send(('ok', strict_json(text), stream.status))
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
        channel.send(('transport', str(error).replace(key, '[REDACTED]')))
    except Exception as error:
        channel.send(('fatal', str(error).replace(key, '[REDACTED]')))
    finally:
        channel.close()


def _bounded_http(url, data, key, timeout):
    """One total monotonic deadline includes spawn, DNS, TLS, headers and body."""
    end = time.monotonic() + timeout
    ctx = multiprocessing.get_context('spawn')
    owner, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_http_child, args=(child, url, data, key, timeout), daemon=True)
    replies = queue.Queue(maxsize=1)
    reader = None
    def receive():
        try: replies.put(('reply', owner.recv()))
        except (EOFError, OSError): replies.put(('closed', None))
    try:
        process.start(); child.close()
        # recv itself may wait for an entire IPC frame. Bound that wait too.
        reader = threading.Thread(target=receive, daemon=True); reader.start()
        try:
            kind, reply = replies.get(timeout=max(0, end - time.monotonic()))
        except queue.Empty:
            raise PaidTransportError('total request wall deadline reached')
        if kind == 'closed':
            raise PaidTransportError('network child ended without a response') from None
        if time.monotonic() > end:
            raise PaidTransportError('total request wall deadline reached')
        if reply[0] == 'transport':
            raise PaidTransportError(reply[1])
        if reply[0] != 'ok':
            raise PaidStop(reply[1])
        return reply[1], reply[2]
    finally:
        child.close()
        if process.pid is not None:
            if process.is_alive():
                process.kill()
            process.join(timeout=1)
            if process.is_alive():
                raise PaidStop('network child cleanup failed')
            process.close()
        if reader is not None:
            reader.join(timeout=1)
            if reader.is_alive():
                raise PaidStop('network IPC cleanup failed')
        owner.close()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with tmp.open('xb') as stream:
        stream.write((json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode())
        stream.flush(); os.fsync(stream.fileno())
    os.replace(tmp, path)


def secret(path):
    path = Path(path); info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PaidStop('credential file must be an owner-only regular project file')
    values = [line.split('=', 1)[1].strip().strip('\"\'') for line in path.read_text().splitlines()
              if line.startswith('POLZA_API_KEY=')]
    if len(values) != 1 or not 16 <= len(values[0]) <= 512 or not values[0].isascii() or any(c.isspace() for c in values[0]):
        raise PaidStop('invalid local credential format')
    return values[0]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def exposure(ledger):
    return math.fsum(call['charged_rub'] if call.get('charged_rub') is not None else call['reserved_rub']
                     for call in ledger['calls'])


class Client:
    def __init__(self, workspace, settings, deadline, key=None, transport=None):
        self.workspace = Path(workspace); self.folder = self.workspace / 'polza'
        self.settings = settings; self.deadline = deadline
        self.key = key if key is not None else secret(Path(__file__).resolve().parents[1] / '.env.local')
        self.transport = transport or self.http
        self.policy = getattr(settings, 'polza_transport_policy', 'legacy-v1')
        if self.policy not in ('legacy-v1', 'paced-catalog-v2'):
            raise PaidStop('unknown frozen transport policy')

    def redact(self, value):
        if isinstance(value, str):
            return value.replace(self.key, '[REDACTED]')
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        if isinstance(value, dict):
            return {self.redact(k): self.redact(v) for k, v in value.items()}
        return value

    @contextmanager
    def ledger(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        with (self.folder / 'ledger.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.folder / 'ledger.json'
            data = strict_json(path.read_text()) if path.exists() else {
                'version': 'polza-ledger-v1', 'calls': [], 'fatal': None,
                'cap_rub': self.settings.polza_cap_rub,
                'starts_cap': self.settings.limits.http_starts,
                'concurrency_cap': self.settings.parallel_workers, 'transport_policy': self.policy,
                'next_start_unix': 0, 'pause_until_unix': 0}
            if data.get('transport_policy', 'legacy-v1') != self.policy:
                raise PaidStop('ledger transport policy differs from frozen config')
            yield data
            data['exposure_rub'] = exposure(data)
            write(path, data)

    def http(self, path, body=None):
        if path not in ('/api/v1/chat/completions', '/api/v1/models?type=chat&include_providers=true', '/api/v2/balance', CATALOG_PATH):
            raise PaidStop('network path outside the authorized Polza surface')
        remaining = self.deadline - time.time()
        if remaining <= 0:
            raise PaidStop('original wall deadline reached')
        return _bounded_http('https://polza.ai' + path,
            encode(body) if body is not None else None, self.key,
            min(60, remaining, self.settings.limits.request_s))

    def metadata(self, path):
        value, status = self.transport(path)
        if status != 200:
            raise PaidStop('Polza metadata HTTP ' + str(status))
        return self.redact(value)

    def preflight(self):
        balance = self.metadata('/api/v2/balance')
        available = float(balance['available'])
        if not math.isfinite(available) or available <= 5:
            raise PaidStop('available balance does not cover reserve')
        path = CATALOG_PATH if self.policy == 'paced-catalog-v2' else '/api/v1/models?type=chat&include_providers=true'
        catalog = self.metadata(path)
        models = catalog.get('data')
        if not isinstance(models, list) or any(not isinstance(m, dict) for m in models):
            raise PaidStop('invalid model metadata shape')
        if self.policy == 'paced-catalog-v2':
            meta = catalog.get('meta', {})
            if (type(meta.get('totalPages')) is not int or meta['totalPages'] < 1
                    or type(meta.get('page')) is not int or meta['page'] != 1
                    or type(meta.get('total')) is not int or meta['total'] < 1
                    or meta.get('limit') != 10 or meta['totalPages'] != math.ceil(meta['total'] / 10)
                    or len(models) != min(10, meta['total'])):
                raise PaidStop('bounded model catalog page is incomplete')
        matches = [m for m in models if m.get('id') == MODEL]
        if len(matches) != 1:
            raise PaidStop('exact approved model must occur once in metadata')
        model = matches[0]
        if not model:
            raise PaidStop('exact approved model absent from metadata')
        provider_source = 'providers'
        providers = model.get('providers', [])
        if self.policy == 'paced-catalog-v2' and 'providers' not in model:
            provider_source = 'top_provider'
            providers = [model.get('top_provider')]
        if not isinstance(providers, list):
            raise PaidStop('invalid provider metadata shape')
        matches = [p for p in providers if isinstance(p, dict) and isinstance(p.get('name'), str) and p['name'].casefold() == PROVIDER]
        if len(matches) != 1:
            raise PaidStop('pinned provider must occur once in metadata')
        provider = matches[0]
        if not provider:
            raise PaidStop('pinned provider absent from metadata')
        price = provider.get('pricing', {})
        if price.get('currency') != 'RUB' or any(not 0 <= float(price.get(k, 'inf')) <= PRICE for k in ('prompt_per_million', 'completion_per_million')):
            raise PaidStop('provider pricing outside approved RUB ceilings')
        if not {'max_tokens', 'temperature', 'seed', 'tools', 'tool_choice'} <= set(provider.get('supported_parameters', [])):
            raise PaidStop('provider lacks required parameters')
        result = {'retrieved_unix': time.time(), 'balance': balance, 'model': model, 'selected_provider': provider,
                  'request_pin': PROVIDER, 'returned_provider_verification_required': False,
                  'catalog_path': path, 'catalog_fresh': True, 'transport_policy': self.policy,
                  'provider_metadata_source': provider_source,
                  'catalog_selection_scope': 'exact model and pinned provider within returned page'
                    if self.policy == 'paced-catalog-v2' else 'exact model within full list',
                  'start_interval_s': START_INTERVAL_S if self.policy == 'paced-catalog-v2' else 0,
                  'rate_cooldown_s': RATE_COOLDOWN_S if self.policy == 'paced-catalog-v2' else 0}
        write(self.folder / 'preflight.json', result)
        with self.ledger() as ledger:
            ledger['cap_rub'] = min(ledger['cap_rub'], available - 5)
        return result

    def body(self, original):
        if self.key in encode(original).decode() or self.redact(original) != original:
            raise PaidStop('credential detected in request; nothing persisted or sent')
        if original.get('model') != MODEL or original.get('stream') is not False or original.get('max_tokens') != self.settings.limits.max_output_tokens:
            raise PaidStop('model/output/stream differs from approved request')
        body = copy.deepcopy(original)
        body['provider'] = {'only': [PROVIDER], 'allow_fallbacks': False,
                            'max_price': {'prompt': PRICE, 'completion': PRICE}}
        # Fixed point includes the price field itself; byte count is a conservative token bound.
        for _ in range(3):
            size = len(encode(body))
            body['provider']['max_price']['request'] = (size + 4096 + self.settings.limits.max_output_tokens) * PRICE / 1_000_000
        size = len(encode(body))
        if size > 20000:
            raise PaidStop('request exceeds frozen body size ceiling')
        bound = (size + 4096 + self.settings.limits.max_output_tokens) * PRICE / 1_000_000
        body['provider']['max_price']['request'] = max(bound, body['provider']['max_price']['request'])
        return body

    def generate(self, body, run_id, ordinal):
        reserve = body['provider']['max_price']['request']
        while True:
            problem = None; wait_s = 0
            with self.ledger() as ledger:
                now = time.time()
                if ledger['fatal'] or (self.workspace / 'stop.json').exists() or now >= self.deadline:
                    problem = 'paid execution stopped or deadline reached'
                elif any(c.get('run_id') == run_id and c.get('ordinal') == ordinal for c in ledger['calls']):
                    problem = 'generation ordinal already started; never replayed'
                elif len(ledger['calls']) >= ledger['starts_cap'] or exposure(ledger) + reserve > ledger['cap_rub']:
                    problem = 'generation-start or cost-exposure cap reached'
                elif self.policy == 'paced-catalog-v2':
                    wait_s = max(ledger.get('next_start_unix', 0), ledger.get('pause_until_unix', 0)) - now
                if not problem and wait_s <= 0:
                    if sum(c['status'] == 'inflight' for c in ledger['calls']) >= ledger['concurrency_cap']:
                        problem = 'paid in-flight concurrency cap reached'
                    else:
                        index = len(ledger['calls']) + 1
                        ledger['calls'].append({'index': index, 'run_id': run_id, 'ordinal': ordinal, 'status': 'inflight',
                            'reserved_rub': reserve, 'charged_rub': None, 'started_unix': now,
                            'request_sha256': hashlib.sha256(encode(body)).hexdigest()})
                        if self.policy == 'paced-catalog-v2':
                            ledger['next_start_unix'] = now + START_INTERVAL_S
                if problem:
                    ledger['fatal'] = ledger['fatal'] or problem
            if problem:
                raise PaidStop(problem)
            if wait_s <= 0:
                break
            # Waiting holds no ledger lock, reservation or new generation start.
            time.sleep(min(.1, wait_s, max(0, self.deadline - time.time())))
        # No network happens until the write-ahead ledger is durable.
        started = time.time()
        try:
            with (self.workspace / 'requests.jsonl').open('a') as stream:
                stream.write(json.dumps({'sequence': index, 'time': time.time(), 'run_id': run_id,
                    'ordinal': ordinal, 'request_sha256': hashlib.sha256(encode(body)).hexdigest()}) + '\n')
                stream.flush(); os.fsync(stream.fileno())
            response, status = self.transport('/api/v1/chat/completions', body)
            response = self.redact(response)
            folder = self.workspace / 'slots' / run_id / 'provider-calls'
            write(folder / f'{ordinal:04d}-response.json', response)
            error = response.get('error') if isinstance(response, dict) else None
            sample_rejection = (status == 502 and isinstance(error, dict) and error.get('code') == 'BAD_GATEWAY'
                and error.get('message') == 'The provider returned an invalid tool call. Retry the request or pick another model.')
            rate_rejection = (self.policy == 'paced-catalog-v2' and status == 429 and isinstance(error, dict)
                and error.get('code') == 'TOO_MANY_REQUESTS'
                and error.get('message') == 'The model provider is rate-limiting requests. Retry in a few seconds or pick another model.'
                and isinstance(error.get('metadata'), dict) and error['metadata'].get('provider_name') == 'Mistral')
            usage = response.get('usage') if isinstance(response, dict) else None
            charge = usage.get('cost_rub') if isinstance(usage, dict) else None
            if isinstance(charge, (int, float)) and not isinstance(charge, bool) and math.isfinite(charge) and charge >= 0:
                with self.ledger() as ledger:
                    ledger['calls'][index - 1].update(charged_rub=charge, usage=usage, http_status=status)
            else:
                if rate_rejection and (usage is None or isinstance(usage, dict) and 'cost_rub' not in usage):
                    raise PaidRateLimit('provider rate rejected this generation; no retry; unknown cost retained')
                if sample_rejection and (usage is None or isinstance(usage, dict) and 'cost_rub' not in usage):
                    raise PaidSampleError('provider rejected invalid tool call; raw actor call unavailable; no retry')
                raise PaidStop('missing or invalid billed cost_rub')
            if charge > reserve + 1e-8:
                raise PaidStop('billed cost exceeds conservative reservation')
            if rate_rejection:
                raise PaidRateLimit('provider rate rejected this generation; no retry')
            if sample_rejection:
                raise PaidSampleError('provider rejected invalid tool call; raw actor call unavailable; no retry')
            if status != 200:
                raise PaidStop('Polza generation HTTP ' + str(status))
            if response.get('model') != MODEL:
                raise PaidStop('returned model missing or mismatched')
            provider = response.get('provider')
            if provider is not None and (not isinstance(provider, str) or provider.casefold() != PROVIDER):
                raise PaidStop('returned provider mismatched')
            if not isinstance(response.get('choices'), list) or len(response['choices']) != 1:
                raise PaidStop('response choice shape differs')
            for field in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                if type(usage.get(field)) is not int or usage[field] < 0:
                    raise PaidStop('invalid token usage')
            if usage['prompt_tokens'] > self.settings.limits.context or usage['completion_tokens'] > self.settings.limits.max_output_tokens:
                raise PaidStop('returned token usage exceeds frozen bounds')
            with self.ledger() as ledger:
                ledger['calls'][index - 1].update(status='complete', seconds=time.time() - started,
                    model=response['model'], provider=provider,
                    response_sha256=hashlib.sha256((folder / f'{ordinal:04d}-response.json').read_bytes()).hexdigest())
            return response
        except PaidTransportError as error:
            with self.ledger() as ledger:
                ledger['calls'][index - 1].update(status='transport_unknown',
                    error=self.redact(str(error)), seconds=time.time() - started)
                ledger['pause_until_unix'] = max(ledger.get('pause_until_unix', 0), time.time() + RATE_COOLDOWN_S)
            raise
        except PaidRateLimit as error:
            with self.ledger() as ledger:
                ledger['calls'][index - 1].update(status='provider_rate_rejected', error=str(error),
                    http_status=status, seconds=time.time() - started,
                    response_sha256=hashlib.sha256((folder / f'{ordinal:04d}-response.json').read_bytes()).hexdigest())
                ledger['pause_until_unix'] = max(ledger.get('pause_until_unix', 0), time.time() + RATE_COOLDOWN_S)
            raise
        except PaidSampleError as error:
            with self.ledger() as ledger:
                ledger['calls'][index - 1].update(status='provider_rejected_output', error=str(error),
                    http_status=status, seconds=time.time() - started)
            raise
        except Exception as error:
            with self.ledger() as ledger:
                ledger['calls'][index - 1].update(status='unknown_or_error', error=self.redact(str(error)), seconds=time.time() - started)
                ledger['fatal'] = 'provider or accounting failure; no new starts'
            raise PaidStop(self.redact(str(error))) from None


def interrupted(workspace, run_id=None):
    folder = Path(workspace) / 'polza'
    if not (folder / 'ledger.json').exists():
        return
    with (folder / 'ledger.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = strict_json((folder / 'ledger.json').read_text())
        changed = False
        for call in ledger['calls']:
            if call['status'] == 'inflight' and (run_id is None or call['run_id'] == run_id):
                call['status'] = 'interrupted_unknown'
                changed = True
        if changed:
            ledger['exposure_rub'] = exposure(ledger)
            write(folder / 'ledger.json', ledger)
