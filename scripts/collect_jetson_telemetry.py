"""Collect real Jetson telemetry: `tegrastats.log` raw, `jetson_telemetry.json` parsed.

`edge/hardware_telemetry.py` already owns the tegrastats line format, so parsing is
shared rather than reimplemented: this module streams the process, keeps every raw
line, and aggregates the parsed samples.

Two rules the output obeys:

* a metric the device did not report stays `null` — never `0`, and never a value
  borrowed from another board's specification;
* power rails are not summed. `VDD_IN` is the module rail and the sub-rails
  (`VDD_CPU_GPU_CV`, `VDD_SOC`, ...) are parts of it, so adding them would invent a
  number that the hardware never produced.

Usable as a CLI for a fixed window, or as `TelemetryRecorder` around a benchmark
(which is how `scripts/verify_physical_jetson.py` uses it).

    python3 -m scripts.collect_jetson_telemetry --seconds 60 --out evidence/jetson
"""
import argparse
import json
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edge.hardware_telemetry import CpuTelemetryProvider, JetsonTelemetryProvider  # noqa: E402


def utc():
    return datetime.now(timezone.utc).isoformat()


class TelemetryRecorder:
    """Stream tegrastats for the duration of a benchmark, keeping raw and parsed data."""

    def __init__(self, out_dir, interval_ms=1000, collect_cpu=True):
        self.out_dir = Path(out_dir)
        self.interval_ms = int(interval_ms)
        self.provider = JetsonTelemetryProvider(interval_ms=interval_ms)
        self.cpu = CpuTelemetryProvider() if collect_cpu else None
        self.raw_lines = []
        self.cpu_samples = []
        self.started_at = None
        self.stopped_at = None
        self._process = None
        self._threads = []
        self._stop = threading.Event()
        self.tegrastats_available = self.provider.available()
        self.reason = None if self.tegrastats_available else (
            'tegrastats_not_found' if self.provider.executable is None
            else 'not_a_jetson_host_no_tegra_release_marker')

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        self.started_at = utc()
        self._stop.clear()
        if self.tegrastats_available:
            self._threads.append(threading.Thread(target=self._stream_tegrastats, daemon=True))
        if self.cpu is not None:
            self._threads.append(threading.Thread(target=self._sample_cpu, daemon=True))
        for thread in self._threads:
            thread.start()
        return self

    def _stream_tegrastats(self):
        try:
            self._process = subprocess.Popen([self.provider.executable, '--interval', str(self.interval_ms)],
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        except Exception as exc:  # noqa: BLE001
            self.reason = f'tegrastats_start_failed:{type(exc).__name__}'
            self.tegrastats_available = False
            return
        for line in self._process.stdout:
            if self._stop.is_set():
                break
            line = line.strip()
            if not line:
                continue
            self.raw_lines.append(line)
            self.provider.samples.append(JetsonTelemetryProvider.parse(line))
        try:
            self._process.terminate()
        except Exception:  # noqa: BLE001
            pass

    def _sample_cpu(self):
        # The sample rate is tied to the tegrastats interval so the two series line up.
        while True:
            sample = self.cpu.sample()
            self.cpu_samples.append({'at': utc(), 'cpu_percent': sample['cpu_percent'],
                                     'rss_bytes': sample['rss_bytes'],
                                     'system_memory_percent': sample['system_memory_percent']})
            if self._stop.wait(self.interval_ms / 1000.0):
                return

    def stop(self):
        self._stop.set()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
        for thread in self._threads:
            thread.join(timeout=5)
        self.stopped_at = utc()
        return self.summary()

    # -- output ------------------------------------------------------------
    @staticmethod
    def _series(samples, key):
        return [sample[key] for sample in samples if sample.get(key) is not None]

    @staticmethod
    def _stats(values):
        """min/mean/max, or None for every field when nothing was measured."""
        if not values:
            return {'samples': 0, 'min': None, 'mean': None, 'max': None, 'latest': None}
        return {'samples': len(values), 'min': min(values), 'mean': round(statistics.fmean(values), 3),
                'max': max(values), 'latest': values[-1]}

    def summary(self):
        samples = self.provider.samples
        durations = None
        if self.started_at and self.stopped_at:
            try:
                started = datetime.fromisoformat(self.started_at)
                stopped = datetime.fromisoformat(self.stopped_at)
                durations = round((stopped - started).total_seconds(), 3)
            except ValueError:
                durations = None

        zones = {}
        for sample in samples:
            for zone, value in (sample.get('temperatures_celsius') or {}).items():
                zones.setdefault(zone, []).append(value)
        rails = {}
        for sample in samples:
            for rail, reading in (sample.get('power_rails_milliwatts') or {}).items():
                rails.setdefault(rail, []).append(reading['average_mw'])

        ram_used = self._series(samples, 'ram_used_mb')
        ram_total = next((sample['ram_total_mb'] for sample in samples if sample.get('ram_total_mb')), None)
        ram_percent = None
        if ram_used and ram_total:
            ram_percent = round(max(ram_used) / ram_total * 100, 2)
        online_cores = self._series(samples, 'cpu_online_cores')

        return {
            'captured_at': utc(),
            'window': {'started_at': self.started_at, 'stopped_at': self.stopped_at,
                       'seconds': durations, 'interval_ms': self.interval_ms},
            'tegrastats': {
                'available': self.tegrastats_available,
                'executable': self.provider.executable,
                'reason': self.reason,
                'sample_count': len(samples),
                'log': 'tegrastats.log' if self.raw_lines else None,
                'raw_line_sha256': None,  # filled by the caller that writes the file
            },
            'cpu': {
                'tegrastats_utilization_percent': self._stats(self._series(samples, 'cpu_utilization_percent')),
                'psutil_cpu_percent': self._stats([sample['cpu_percent'] for sample in self.cpu_samples]),
                'psutil_rss_bytes': self._stats([sample['rss_bytes'] for sample in self.cpu_samples]),
                'online_cores': self._stats(online_cores),
                'per_core_utilization_percent': (samples[-1].get('cpu_core_utilization_percent') if samples else None),
                'per_core_frequency_mhz': (samples[-1].get('cpu_core_frequency_mhz') if samples else None),
            },
            'gpu': {
                # GR3D_FREQ is the 3D engine's activity as reported by tegrastats. It is
                # not a "percent of peak FLOPS" figure and is not described as one.
                'gr3d_utilization_percent': self._stats(self._series(samples, 'gpu_utilization_percent')),
                'emc_utilization_percent': self._stats(self._series(samples, 'emc_utilization_percent')),
            },
            'memory': {
                'ram_used_mb': self._stats(ram_used), 'ram_total_mb': ram_total,
                'peak_ram_percent': ram_percent,
                'swap_used_mb': self._stats(self._series(samples, 'swap_used_mb')),
                'swap_total_mb': next((sample['swap_total_mb'] for sample in samples
                                       if sample.get('swap_total_mb')), None),
            },
            'thermal': {'zones_celsius': {zone: self._stats(values) for zone, values in sorted(zones.items())},
                        'max_zone_celsius': (max(zone_max for zone_max, _zone in
                                                 ((max(values), zone) for zone, values in zones.items()))
                                             if zones else None),
                        'hottest_zone': (max(((max(values), zone) for zone, values in zones.items()))[1]
                                         if zones else None)},
            'power': {
                # Rails are reported separately and never summed: VDD_IN already
                # contains the sub-rails, so a total would double count.
                'rails_milliwatts': {rail: self._stats(values) for rail, values in sorted(rails.items())},
                'module_power_milliwatts': self._stats(self._series(samples, 'module_power_milliwatts')),
                'summing_rails_note': ('sub-rails are parts of VDD_IN; they are never added together here'),
            },
            'absent_metrics': sorted(key for key, value in
                                     (('tegrastats_samples', samples),
                                      ('gpu_utilization', self._series(samples, 'gpu_utilization_percent')),
                                      ('thermal_zones', zones), ('power_rails', rails)) if not value),
            'note': ('absent means the device did not report it, not that it was zero. No value here was '
                     'read from a datasheet or copied from another board.'),
        }

    def write(self):
        """Write the raw log and the parsed summary into `out_dir`."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        summary = self.summary()
        if self.raw_lines:
            log_path = self.out_dir / 'tegrastats.log'
            # Header lines the reader needs to trust the file: the exact command and
            # which machine produced it.
            header = [f'# tegrastats --interval {self.interval_ms}',
                      f'# window {self.started_at} .. {self.stopped_at}',
                      f'# lines {len(self.raw_lines)}']
            log_path.write_text('\n'.join(header + self.raw_lines) + '\n', encoding='utf-8')
            import hashlib
            summary['tegrastats']['raw_line_sha256'] = hashlib.sha256(log_path.read_bytes()).hexdigest()
            summary['tegrastats']['log'] = log_path.name
        target = self.out_dir / 'jetson_telemetry.json'
        target.write_text(json.dumps(summary, indent=2, default=str))
        return summary, target


def main(argv=None):
    parser = argparse.ArgumentParser(description='Collect Jetson telemetry (tegrastats + CPU/RSS).')
    parser.add_argument('--seconds', type=float, default=30.0, help='collection window')
    parser.add_argument('--interval-ms', type=int, default=1000)
    parser.add_argument('--out', default='docs/evidence/jetson')
    arguments = parser.parse_args(argv)

    recorder = TelemetryRecorder(arguments.out, interval_ms=arguments.interval_ms).start()
    if not recorder.tegrastats_available:
        print(json.dumps({'tegrastats_available': False, 'reason': recorder.reason,
                          'note': ('no tegrastats on this host, so GPU/thermal/power fields will be null; '
                                   'on Jetson this file is produced by the real device')}, indent=2), flush=True)
    time.sleep(max(arguments.seconds, 0.1))
    recorder.stop()
    summary, target = recorder.write()
    print(json.dumps({'tegrastats_available': summary['tegrastats']['available'],
                      'sample_count': summary['tegrastats']['sample_count'],
                      'absent_metrics': summary['absent_metrics'],
                      'max_zone_celsius': summary['thermal']['max_zone_celsius'],
                      'module_power_milliwatts': summary['power']['module_power_milliwatts'],
                      'evidence': str(target)}, indent=2, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
