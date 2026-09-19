"""Hardware telemetry providers: truthful CPU always, GPU only when it exists.

    HardwareTelemetryProvider
    |-- CpuTelemetryProvider        always available, real psutil readings
    |-- NvidiaSmiProvider           active only when a real GPU answers nvidia-smi
    |-- JetsonTelemetryProvider     active only on a real Jetson (tegrastats)

A missing GPU is reported as `gpu_metrics_available: false` with `gpu: null`.
It is never rendered as 0% utilisation, 0 MiB, or 0 W, and callers must treat
absence as unknown rather than healthy.
"""
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

NVIDIA_SMI_QUERY = ('index,name,utilization.gpu,memory.used,memory.total,'
                    'temperature.gpu,power.draw,clocks.sm')
JETSON_RELEASE_FILES = ('/etc/nv_tegra_release', '/etc/nv_boot_control.conf')
TEGRASTATS_SAMPLE = re.compile(r'(?P<key>[A-Za-z0-9_]+)\s(?P<value>[0-9]+)(?:mW|%@?[0-9]+)?')
# A real tegrastats line carries far more than utilisation: thermal zones, power
# rails and CPU core frequencies. Each is parsed separately so a field that the
# running L4T version does not emit stays *absent* rather than becoming a zero.
TEGRASTATS_RAM = re.compile(r'RAM (?P<used>[0-9]+)/(?P<total>[0-9]+)MB')
TEGRASTATS_SWAP = re.compile(r'SWAP (?P<used>[0-9]+)/(?P<total>[0-9]+)MB')
TEGRASTATS_GR3D = re.compile(r'GR3D_FREQ (?P<value>[0-9]+)%')
TEGRASTATS_EMC = re.compile(r'EMC_FREQ (?P<value>[0-9]+)%')
TEGRASTATS_CPU = re.compile(r'CPU \[(?P<cores>[^\]]*)\]')
TEGRASTATS_TEMPERATURE = re.compile(r'(?P<key>[A-Za-z0-9_]+)@(?P<value>-?[0-9.]+)C')
TEGRASTATS_POWER = re.compile(r'(?P<key>[A-Za-z0-9_]+)\s+(?P<average>[0-9]+)mW/(?P<total>[0-9]+)mW')


class HardwareTelemetryProvider:
    name = 'abstract'
    kind = 'cpu'

    def available(self):
        raise NotImplementedError

    def sample(self):
        raise NotImplementedError


class CpuTelemetryProvider(HardwareTelemetryProvider):
    """Real host CPU/memory readings. Always available, never estimated."""

    name = 'cpu'
    kind = 'cpu'

    def __init__(self):
        import psutil
        self.psutil = psutil
        self.process = psutil.Process(os.getpid())
        # Prime the internal baseline so the first sample is a measurement.
        psutil.cpu_percent(interval=None)

    def available(self):
        return True

    def sample(self):
        memory = self.process.memory_info()
        return {'provider': self.name, 'cpu_percent': self.psutil.cpu_percent(interval=None),
                'cpu_count': self.psutil.cpu_count(),
                'rss_bytes': memory.rss, 'vms_bytes': memory.vms,
                'system_memory_percent': self.psutil.virtual_memory().percent,
                'load_average': (self.psutil.getloadavg() if hasattr(self.psutil, 'getloadavg') and platform.system() != 'Windows' else None)}


class NvidiaSmiProvider(HardwareTelemetryProvider):
    """Real GPU telemetry through nvidia-smi; unavailable without a GPU."""

    name = 'nvidia-smi'
    kind = 'gpu'

    def __init__(self):
        self.executable = shutil.which('nvidia-smi')
        self.reason = None if self.executable else 'nvidia-smi_not_found'
        self._probe = None

    def available(self):
        if not self.executable:
            return False
        if self._probe is None:
            try:
                result = subprocess.run([self.executable, f'--query-gpu={NVIDIA_SMI_QUERY}',
                                         '--format=csv,noheader,nounits'],
                                        capture_output=True, text=True, timeout=20)
            except Exception as exc:  # noqa: BLE001
                self.reason = type(exc).__name__
                self._probe = False
                return False
            if result.returncode != 0:
                self.reason = 'nvidia-smi_failed'
                self._probe = False
                return False
            self.reason = None
            self._probe = bool(result.stdout.strip())
        return self._probe

    def sample(self):
        if not self.available():
            return None
        result = subprocess.run([self.executable, f'--query-gpu={NVIDIA_SMI_QUERY}',
                                 '--format=csv,noheader,nounits'],
                                capture_output=True, text=True, timeout=20)
        gpus = []
        for line in result.stdout.strip().splitlines():
            values = [v.strip() for v in line.split(',')]
            if len(values) != 8:
                continue
            gpus.append({'index': values[0], 'name': values[1],
                         'utilization_ratio': _ratio(values[2], 100),
                         'memory_used_bytes': _bytes(values[3]), 'memory_total_bytes': _bytes(values[4]),
                         'temperature_celsius': _number(values[5]),
                         'power_watts': _number(values[6]), 'clock_mhz': _number(values[7])})
        return {'provider': self.name, 'gpus': gpus} if gpus else None


class JetsonTelemetryProvider(HardwareTelemetryProvider):
    """Jetson/tegrastats telemetry. Inactive anywhere but a real Jetson device."""

    name = 'tegrastats'
    kind = 'gpu'

    def __init__(self, interval_ms=1000):
        self.is_jetson = any(Path(path).exists() for path in JETSON_RELEASE_FILES)
        self.executable = shutil.which('tegrastats')
        self.interval_ms = int(interval_ms)
        self.samples = []
        self.error = None
        self._process = None
        self._thread = None
        self._stop = threading.Event()

    def available(self):
        return self.is_jetson and self.executable is not None

    @staticmethod
    def parse(line):
        """Parse one tegrastats line. Absent metrics are omitted, never zeroed.

        Field names follow tegrastats itself (`GR3D_FREQ`, `VDD_IN`, `tj@`), so a
        reader can line the parsed value up with the raw `tegrastats.log` line it
        came from.
        """
        parsed = {}
        for match in TEGRASTATS_SAMPLE.finditer(line):
            parsed[match.group('key')] = int(match.group('value'))
        ram = TEGRASTATS_RAM.search(line)
        if ram:
            parsed['ram_used_mb'], parsed['ram_total_mb'] = int(ram.group('used')), int(ram.group('total'))
        swap = TEGRASTATS_SWAP.search(line)
        if swap:
            parsed['swap_used_mb'], parsed['swap_total_mb'] = int(swap.group('used')), int(swap.group('total'))
        gr3d = TEGRASTATS_GR3D.search(line)
        if gr3d:
            parsed['gpu_utilization_percent'] = int(gr3d.group('value'))
        emc = TEGRASTATS_EMC.search(line)
        if emc:
            parsed['emc_utilization_percent'] = int(emc.group('value'))
        cpu = TEGRASTATS_CPU.search(line)
        if cpu:
            cores = [token.strip() for token in cpu.group('cores').split(',') if token.strip()]
            utilizations, frequencies = [], []
            for core in cores:
                if core == 'off':
                    utilizations.append(None)
                    frequencies.append(None)
                    continue
                match = re.match(r'(?P<percent>\d+)%(?:@(?P<mhz>\d+))?', core)
                if not match:
                    utilizations.append(None)
                    frequencies.append(None)
                    continue
                utilizations.append(int(match.group('percent')))
                frequencies.append(int(match.group('mhz')) if match.group('mhz') else None)
            parsed['cpu_core_utilization_percent'] = utilizations
            parsed['cpu_core_frequency_mhz'] = frequencies
            online = [value for value in utilizations if value is not None]
            parsed['cpu_online_cores'] = len(online)
            parsed['cpu_utilization_percent'] = round(sum(online) / len(online), 2) if online else None
        temperatures = {match.group('key'): float(match.group('value'))
                        for match in TEGRASTATS_TEMPERATURE.finditer(line)}
        if temperatures:
            parsed['temperatures_celsius'] = temperatures
        power = {match.group('key'): {'average_mw': int(match.group('average')),
                                      'total_mw': int(match.group('total'))}
                 for match in TEGRASTATS_POWER.finditer(line)}
        if power:
            # `VDD_IN` is the whole-module rail; the others are sub-rails. Both are
            # kept because a module total and a rail reading are not the same number.
            parsed['power_rails_milliwatts'] = power
            if 'VDD_IN' in power:
                parsed['module_power_milliwatts'] = power['VDD_IN']['average_mw']
        return parsed

    def start(self, interval_ms=1000):
        """Stream tegrastats through one long-lived process.

        The previous implementation re-ran a non-terminating tegrastats per loop
        iteration and waited for a timeout each time, which both sampled sparsely and
        left a process to kill per iteration. Streaming reads each line as it arrives.
        """
        if not self.available() or self._thread is not None:
            return False
        self.error = None

        def loop():
            try:
                process = subprocess.Popen([self.executable, '--interval', str(interval_ms)],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                           bufsize=1)
            except Exception as exc:  # noqa: BLE001
                self.error = type(exc).__name__
                return
            self._process = process
            for line in process.stdout:
                if self._stop.is_set():
                    break
                if line.strip():
                    self.samples.append(self.parse(line))
                    self.samples = self.samples[-600:]
            process.terminate()

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        process = getattr(self, '_process', None)
        if process is not None and process.poll() is None:
            process.terminate()

    def sample(self):
        if not self.available() or not self.samples:
            return None
        return {'provider': self.name, 'latest': self.samples[-1], 'sample_count': len(self.samples)}


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratio(value, scale):
    number = _number(value)
    return None if number is None else round(number / scale, 4)


def _bytes(mebibytes):
    number = _number(mebibytes)
    return None if number is None else int(number * 1024 * 1024)


class HardwareTelemetry:
    """Aggregate the available providers; absence of a GPU stays explicit."""

    def __init__(self, providers=None):
        self.cpu = CpuTelemetryProvider()
        self.gpu_providers = providers if providers is not None else [NvidiaSmiProvider(), JetsonTelemetryProvider()]

    def available_gpu_providers(self):
        return [provider.name for provider in self.gpu_providers if provider.available()]

    def sample(self):
        gpu = None
        for provider in self.gpu_providers:
            if provider.available():
                gpu = provider.sample()
                if gpu:
                    break
        return {'sampled_at': time.time(), 'cpu_metrics_available': True,
                'cpu': self.cpu.sample(),
                'gpu_metrics_available': gpu is not None,
                'gpu': gpu,
                'gpu_providers_available': self.available_gpu_providers(),
                'gpu_providers_unavailable': [{'provider': provider.name, 'reason': getattr(provider, 'reason', 'not_present')}
                                              for provider in self.gpu_providers if not provider.available()]}

    # Prometheus-safe values for the metric summary payload. `None` means unknown.
    def heartbeat_values(self):
        sample = self.sample()
        return {'cpu_percent': sample['cpu']['cpu_percent'], 'rss_bytes': sample['cpu']['rss_bytes'],
                'gpu_utilization_ratio': None if not sample['gpu_metrics_available'] else
                (sample['gpu']['gpus'][0]['utilization_ratio'] if sample['gpu'].get('gpus') else None),
                'gpu_memory_used_bytes': None if not sample['gpu_metrics_available'] else
                (sample['gpu']['gpus'][0]['memory_used_bytes'] if sample['gpu'].get('gpus') else None)}
