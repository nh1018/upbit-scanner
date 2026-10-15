"""Unauthenticated NTP bounds; never adjusts the OS clock (RFC 5905 packet fields)."""
import math
import hashlib
import json
import socket
import struct
import time

def dumps(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()

VERSION = 'ntp-clock-bound-1'
SERVERS = ('time.windows.com', 'time.cloudflare.com')
POLICY = {'max_bound_seconds': 1.0, 'max_delay_seconds': 0.5,
          'clock_jump_seconds': 0.05, 'max_bracket_seconds': 120,
          'fresh_write_seconds': 10, 'drift_ppm': 100, 'min_distinct_ips': 2,
          'local_timestamp_uncertainty_seconds': 0.016}
EPOCH = 2208988800


def stamp(t):
    x = t + EPOCH
    return struct.pack('!II', int(x) & 0xffffffff, int((x % 1) * 2**32))


def decode(b, near):
    sec, frac = struct.unpack('!II', b)
    value = sec + frac / 2**32 - EPOCH
    return value + round((near - value) / 2**32) * 2**32


def parse(packet, nonce, t1, t4, elapsed):
    if len(packet) != 48 or packet[24:32] != nonce:
        raise ValueError('packet length/originate mismatch')
    li, vn, mode = packet[0] >> 6, (packet[0] >> 3) & 7, packet[0] & 7
    precision = struct.unpack('!b', packet[3:4])[0]
    if li == 3 or vn not in (3, 4) or mode != 4 or not 1 <= packet[1] <= 15 or not -30 <= precision <= 0:
        raise ValueError('unsynchronized/invalid server response')
    if not all(math.isfinite(x) for x in (t1, t4, elapsed)) or elapsed < 0 or t4 < t1 or abs(t4-t1-elapsed) > POLICY['clock_jump_seconds']:
        raise ValueError('wall clock regression/jump')
    if nonce != stamp(t1): raise ValueError('request timestamp evidence mismatch')
    if packet[32:40] == bytes(8) or packet[40:48] == bytes(8):
        raise ValueError('missing server timestamps')
    t2, t3 = decode(packet[32:40], t1), decode(packet[40:48], t1)
    delay = (t4-t1) - (t3-t2)
    if t3 < t2 or delay < 0 or delay > POLICY['max_delay_seconds']:
        raise ValueError('negative/excessive network delay')
    root_delay = struct.unpack('!i', packet[4:8])[0] / 65536
    dispersion = struct.unpack('!I', packet[8:12])[0] / 65536
    if root_delay < 0:
        raise ValueError('invalid root delay')
    offset = ((t2-t1)+(t3-t4))/2
    radius = delay/2 + root_delay/2 + dispersion + 2**precision + POLICY['local_timestamp_uncertainty_seconds']
    return {'t1_unix': t1, 't2_unix': t2, 't3_unix': t3, 't4_unix': t4,
        'monotonic_elapsed_seconds': elapsed, 'offset_seconds': offset,
        'delay_seconds': delay, 'root_delay_seconds': root_delay,
        'root_dispersion_seconds': dispersion, 'precision_seconds': 2**precision,
        'uncertainty_seconds': radius, 'absolute_error_bound_seconds': abs(offset)+radius,
        'response_hex': packet.hex(), 'request_transmit_hex': nonce.hex()}


def query(server, timeout=3):
    address = socket.getaddrinfo(server, 123, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout); sock.connect(address)
        packet = bytearray(48); packet[0] = 0x23
        t1 = time.time(); mono = time.monotonic(); packet[40:48] = stamp(t1)
        sock.send(packet); response = sock.recv(512)
        t4 = time.time(); elapsed = time.monotonic()-mono
    return dict(parse(response, bytes(packet[40:48]), t1, t4, elapsed),
        server=server, ip=address[0], monotonic_started=mono, monotonic_ended=mono+elapsed)


def verify(e):
    if e.get('version') != VERSION or e.get('policy') != POLICY or e.get('evidence_id') != digest({k:v for k,v in e.items() if k!='evidence_id'}):
        raise ValueError('time evidence contract/hash mismatch')
    samples = e['samples']
    if e['errors'] or len(samples) < 2 or len({s['server'] for s in samples}) < 2 or len({s['ip'] for s in samples}) < POLICY['min_distinct_ips']:
        raise ValueError('insufficient independent valid NTP responses')
    for s in samples:
        calc = parse(bytes.fromhex(s['response_hex']), bytes.fromhex(s['request_transmit_hex']),
                     s['t1_unix'], s['t4_unix'], s['monotonic_elapsed_seconds'])
        if any(calc[k] != s[k] for k in calc): raise ValueError('altered sample calculation')
        if not all(math.isfinite(s[k]) for k in ('monotonic_started','monotonic_ended')) or abs(s['monotonic_ended']-s['monotonic_started']-s['monotonic_elapsed_seconds']) > 0.000001:
            raise ValueError('monotonic sample interval mismatch')
    if any(b['t1_unix'] < a['t4_unix'] for a,b in zip(samples,samples[1:])):
        raise ValueError('clock regression between server requests')
    lo = max(s['offset_seconds']-s['uncertainty_seconds'] for s in samples)
    hi = min(s['offset_seconds']+s['uncertainty_seconds'] for s in samples)
    bound = max(s['absolute_error_bound_seconds'] for s in samples)
    if lo > hi or bound > POLICY['max_bound_seconds']:
        raise ValueError('server disagreement or uncertainty/offset exceeds bound')
    if e['bound_seconds'] != bound or e['status'] != 'PASS':
        raise ValueError('invalid summary')
    return e


def measure(probe=query):
    samples, errors = [], []
    for server in SERVERS:
        try: samples.append(probe(server))
        except (OSError, ValueError) as exc:
            errors.append({'server':server, 'error_type':type(exc).__name__, 'reason':str(exc)})
    e = {'version':VERSION, 'policy':POLICY, 'samples':samples, 'errors':errors,
        'bound_seconds': max((s['absolute_error_bound_seconds'] for s in samples), default=None),
        'status':'PASS', 'authentication':'UNAUTHENTICATED_NTP_NOT_CRYPTOGRAPHIC_PROOF'}
    e['evidence_id'] = digest(e)
    try: verify(e)
    except ValueError as exc:
        e.pop('evidence_id'); e.update(status='FAIL', reason=str(exc)); e['evidence_id']=digest(e)
    return e


def bracket(pre, post, start_ms, end_ms, elapsed):
    verify(pre); verify(post)
    first = min(s['t1_unix'] for s in pre['samples'])
    pre_end = max(s['t4_unix'] for s in pre['samples'])
    post_start = min(s['t1_unix'] for s in post['samples'])
    last = max(s['t4_unix'] for s in post['samples'])
    if not first <= pre_end <= start_ms/1000 <= end_ms/1000 <= post_start <= last:
        raise ValueError('clock evidence does not bracket observation')
    duration = last-first
    measured = max(s['monotonic_ended'] for s in post['samples']) - min(s['monotonic_started'] for s in pre['samples'])
    if not math.isfinite(elapsed) or abs(measured-elapsed) > 0.000001:
        raise ValueError('bracket monotonic evidence mismatch')
    if not 0 <= duration <= POLICY['max_bracket_seconds'] or abs(duration-elapsed) > POLICY['clock_jump_seconds']:
        raise ValueError('expired evidence or bracket clock jump')
    bound = max(pre['bound_seconds'],post['bound_seconds']) + duration*POLICY['drift_ppm']/1000000 + POLICY['clock_jump_seconds']
    if bound > 1: raise ValueError('bracket uncertainty exceeds one second')
    return bound
