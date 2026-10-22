"""Real native capability probes. Failure cannot waive any KAS prerequisite."""
import datetime
import re
import secrets


def components(text):
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r'^(\s*)components:\s*(.*)$', line)
        if not match:
            continue
        indent = len(match[1])
        value = match[2].strip()
        for continuation in lines[index + 1:]:
            if not continuation.strip():
                continue
            if len(continuation) - len(continuation.lstrip()) <= indent:
                break
            if ':' in continuation:
                break
            value += continuation.strip()
        return set(value.split(','))
    raise ValueError('native components field unavailable')


def verify(guest, evidence):
    available = components(guest.command('/bin/ndmc -c "show version"'))
    evidence.capability('ndmc', 'SUPPORTED', 'native_command_verified')
    required = {'dns_filter': {'dns-filter'}, 'opkg_kmod_netfilter': {'opkg-kmod-netfilter'},
                'dns_tls_or_https': {'dns-tls', 'dns-https'}, 'proxy_client': {'proxy'}}
    admitted = True
    for name, alternatives in required.items():
        present = bool(available & alternatives)
        if name == 'proxy_client' and present:
            evidence.capability(name, 'NOT_TESTED', 'unattempted')
        else:
            evidence.capability(name, 'SUPPORTED' if present else 'UNSUPPORTED',
                                'native_command_verified' if present else 'component_absent')
        admitted &= present
    if not {'ext', 'ext-utils'}.issubset(available):
        evidence.capability('ext4_opt', 'UNSUPPORTED', 'component_absent')
        admitted = False
    else:
        guest.entware()
        evidence.capability('ext4_opt', 'SUPPORTED', 'native_command_verified')

    def probe(name, command, validator=lambda value: True):
        nonlocal admitted
        try:
            value = guest.command(command, timeout=40)
            if not validator(value):
                raise ValueError('native capability mismatch')
            evidence.capability(name, 'SUPPORTED', 'native_command_verified')
            return True
        except Exception:
            evidence.capability(name, 'UNSUPPORTED', 'execution_failed')
            admitted = False
            return False

    probe('ram', 'cat /proc/meminfo', lambda s: bool(re.search(r'MemTotal:\s+(?:4[5-9]\d{4}|[5-7]\d{5}) kB', s)))
    swaps = guest.command('cat /proc/swaps').splitlines()[1:]
    zram = [line for line in swaps if line.split()[0].startswith('/dev/zram')]
    external = [line for line in swaps if not line.split()[0].startswith('/dev/zram')]
    if zram and not external:
        evidence.capability('zram', 'SUPPORTED', 'native_command_verified')
    else:
        evidence.capability('zram', 'UNSUPPORTED', 'execution_failed')
        admitted = False
    if any(len(line.split()) < 5 or not line.split()[2].isdigit() or int(line.split()[2]) <= 0 for line in swaps):
        evidence.capability('zram', 'UNSUPPORTED', 'execution_failed')
        admitted = False
    # No external swap is fabricated or enabled to satisfy the 512 MiB policy.
    if external:
        evidence.capability('external_swap', 'UNSUPPORTED', 'execution_failed')
        admitted = False
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    probe('clock', "date -s '" + stamp + "' >/dev/null; date +%s", lambda s: s.isdigit() and int(s) > 1704067200)
    suffix = secrets.token_hex(4)
    chain = 'KAS_LAB_' + suffix
    try:
        guest.command('iptables -t mangle -N ' + chain)
        probe('netfilter_mark', 'iptables -t mangle -A ' + chain + ' -j MARK --set-mark 0x1234')
        probe('netfilter_connmark', 'iptables -t mangle -A ' + chain + ' -j CONNMARK --save-mark')
    except Exception:
        for name in ('netfilter_mark', 'netfilter_connmark'):
            evidence.capability(name, 'UNSUPPORTED', 'execution_failed')
        admitted = False
    finally:
        guest.command('iptables -t mangle -F ' + chain + ' 2>/dev/null; iptables -t mangle -X ' + chain + ' 2>/dev/null || true')
    tun = 'kaslab' + suffix[:4]
    try:
        created = probe('tun', 'ip tuntap add dev ' + tun + ' mode tun')
        if created:
            probe('routing', 'ip link set ' + tun + ' up && ip route add 198.18.0.0/15 dev ' + tun + ' table 200001 && ip route show table 200001', lambda s: tun in s)
    finally:
        guest.command('ip route del 198.18.0.0/15 dev ' + tun + ' table 200001 2>/dev/null; ip link del ' + tun + ' 2>/dev/null || true')
    probe('local_http', 'curl -fSs --max-time 10 http://192.168.1.100/lab/health', lambda s: s == 'KEENETIC_LAB_HTTP_OK')
    probe('package_mirror', 'opkg update')
    # Exercise creation and deletion, without occupying KAS's Proxy0 candidate.
    existing = guest.command('/bin/ndmc -c "show running-config"')
    if re.search(r'(?m)^interface Proxy7\s*$', existing):
        evidence.capability('proxy_client', 'BLOCKED', 'execution_failed')
        admitted = False
    else:
        try:
            probe('proxy_client', '/bin/ndmc -c "interface Proxy7"; /bin/ndmc -c "show interface Proxy7"',
                  lambda s: bool(re.search(r'\btype:\s+Proxy\b', s)))
        finally:
            try:
                guest.command('/bin/ndmc -c "no interface Proxy7"')
            except Exception:
                current = guest.command('/bin/ndmc -c "show running-config"')
                if re.search(r'(?m)^interface Proxy7\s*$', current):
                    evidence.capability('proxy_client', 'BLOCKED', 'execution_failed')
                    admitted = False
    probe('dns', '/bin/ndmc -c "service dns-proxy" && /bin/ndmc -c "ip host lab.invalid 192.168.1.100" && /opt/bin/busybox nslookup lab.invalid 127.0.0.1', lambda s: '192.168.1.100' in s)
    evidence.set('KAS_PREFLIGHT', 'PASS' if admitted else 'BLOCKED',
                 'runtime_prerequisites_passed' if admitted else 'runtime_capability_unavailable')
    return admitted
