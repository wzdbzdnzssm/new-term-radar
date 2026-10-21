"""Daily, resumable one-source subprocess scans. Never reviews or delivers messages."""
import argparse
import contextlib
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from zoneinfo import ZoneInfo

from .runtime import DEFAULT_ROOT
ROOT = DEFAULT_ROOT
DEFAULT_MEMORY_MIB = 700

class ScanBusy(RuntimeError):
    pass

@contextlib.contextmanager
def batch_lock(root):
    import fcntl
    root.mkdir(parents=True, exist_ok=True)
    with (root / "scheduled-scan.lock").open("a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ScanBusy("Another scheduled scan (or its surviving worker) is active")
        try:
            yield handle.fileno()
        finally:
            # Do not explicitly unlock: an inherited fd keeps ownership if the
            # coordinator dies while its worker is still committing evidence.
            pass

def worker(args):
    import resource
    limit = args.memory_mib * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    from . import daily_scan
    return daily_scan.run(args.root, [args.worker], receipt_path=args.receipt)

def wait_worker(command, log, inherited_lock):
    environment = dict(os.environ)
    package_root = str(Path(__file__).resolve().parents[2])
    environment['PYTHONPATH'] = package_root + os.pathsep + environment.get('PYTHONPATH', '')
    process = subprocess.Popen(command, env=environment, stdout=log, stderr=subprocess.STDOUT,
                               pass_fds=(inherited_lock,))
    _, status, usage = os.wait4(process.pid, 0)
    process.returncode = os.waitstatus_to_exitcode(status)
    return process.returncode, usage.ru_maxrss

def run(root, selected=None, memory_mib=DEFAULT_MEMORY_MIB, run_day=None, snapshot=None, execution_id=None):
    from .queue_store import monitor, load, write, locked, recover, digest
    root = root.resolve()
    if memory_mib <= 0: raise ValueError("memory_mib must be positive")
    day = run_day or datetime.datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    datetime.date.fromisoformat(day)
    with batch_lock(root) as lock_fd:
        with locked(root): recover(root)
        config = load(root / "monitor/config.json", {})
        sources = [s for s in config["sources"] if s["id"] in selected] if selected else [s for s in config["sources"] if s.get("enabled") or s["id"] in {"vswp-app", "remakeyourmeme-com"}]
        if not sources: raise ValueError("No sources selected")
        ids = [s["id"] for s in sources]
        if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[a-zA-Z0-9_-]+", sid) for sid in ids):
            raise ValueError("Source IDs must be unique safe path components")
        fingerprint = digest({"sources": sources, "fetch": config.get("fetch", {})})
        if execution_id is not None and not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', execution_id):
            raise ValueError('Invalid collection execution identifier')
        relative = "batch-runs/" + day + "-" + fingerprint[:16] + ("-" + execution_id if execution_id else "")
        directory = root / relative
        progress_path = directory / "progress.json"
        progress = load(progress_path, {"schema_version": 1, "run_id": directory.name, "run_day": day,
                       "config_hash": fingerprint, "source_ids": ids, "started_at": monitor.utc_now(), "attempts": {}})
        if progress["config_hash"] != fingerprint or progress["source_ids"] != ids:
            raise ValueError("Resume configuration does not match the persisted run")
        progress["phase"] = "running"; progress["last_invoked_at"] = monitor.utc_now()
        write(progress_path, progress)

        def receipt(sid):
            found = []
            for path in sorted((directory / "sources" / sid).glob("*-receipt.json")):
                row = load(path, {})
                if row.get("source_id") != sid: raise ValueError("Worker receipt source mismatch")
                if row.get("status") == "committed": return path, row
                found.append((path, row))
            attempts = progress["attempts"].get(sid, [])
            if attempts:
                latest = root / attempts[-1]["receipt_file"]
                for path, row in found:
                    if path == latest: return path, row
            return None, None

        def publish(phase):
            # One source result is resident at a time, even for large baselines.
            statuses, errors, receipt_files = [], [], {}
            result_path = directory / "results.json"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=result_path.parent, prefix="results.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    stream.write('{"schema_version": 2, "sources": [\n')
                    for index, sid in enumerate(ids):
                        path, row = receipt(sid)
                        if row:
                            result = row["result"]
                            receipt_files[sid] = path.relative_to(root).as_posix()
                        else:
                            attempt = progress["attempts"].get(sid, [])
                            result = attempt[-1].get("failure_result") if attempt else None
                            result = result or {"id": sid, "status": "not_checked", "error": "Source not yet completed", "checked_maps": [], "added": [], "changed": [], "removed": [], "baseline": []}
                        if index: stream.write(",\n")
                        json.dump(result, stream, ensure_ascii=False)
                        statuses.append((sid, result["status"]))
                        if result["status"] != "ok": errors.append({"source_id": sid, "error": result.get("error", result["status"])})
                    stream.write("\n]}\n"); stream.flush(); os.fsync(stream.fileno())
                os.replace(temporary, result_path)
            finally:
                if os.path.exists(temporary): os.unlink(temporary)
            success = sum(status == "ok" for _, status in statuses)
            pending_count = len(load(root / "pending-observations.json", {"observations": []})["observations"])
            manifest = {"schema_version": 2, "observed_at": monitor.utc_now(), "started_at": progress["started_at"],
                        "status": "ok" if success == len(ids) else ("partial" if success else "failed"),
                        "scan_phase": phase, "run_id": directory.name, "source_count": len(ids), "success_count": success,
                        "errors": errors, "pending_observations_count": pending_count,
                        "raw_result_file": relative + "/results.json", "progress_file": relative + "/progress.json",
                        "source_receipt_files": receipt_files, "memory_limit_mib": memory_mib,
                        "not_checked_sources": [sid for sid, status in statuses if status == "not_checked"]}
            write(directory / "manifest.json", manifest)
            write(root / "latest-scan.json", manifest)
            return manifest

        publish("running")
        for sid in ids:
            _, existing = receipt(sid)
            if existing and existing.get("status") == "committed":
                for old_attempt in progress["attempts"].get(sid, []):
                    if old_attempt["phase"] == "running": old_attempt["phase"] = "recovered_committed"
                write(progress_path, progress)
                continue
            current_config = load(root / "monitor/config.json", {})
            current_sources = [s for s in current_config["sources"] if s["id"] in ids]
            if digest({"sources": current_sources, "fetch": current_config.get("fetch", {})}) != fingerprint:
                progress["phase"] = "configuration_changed"; write(progress_path, progress)
                publish("configuration_changed")
                raise ValueError("Source configuration changed during the run; no further sources started")
            attempts = progress["attempts"].setdefault(sid, [])
            for old_attempt in attempts:
                if old_attempt["phase"] == "running":
                    old_attempt.update(phase="interrupted", failure_kind="coordinator_interrupted")
            number = len(attempts) + 1
            prefix = relative + "/sources/" + sid + "/" + str(number).zfill(4)
            attempt = {"number": number, "started_at": monitor.utc_now(), "phase": "running",
                       "receipt_file": prefix + "-receipt.json", "log_file": prefix + ".log", "memory_limit_mib": memory_mib}
            attempts.append(attempt); write(progress_path, progress)
            log_path = root / attempt["log_file"]; log_path.parent.mkdir(parents=True, exist_ok=True)
            command = [sys.executable, "-m", __package__, "scan", "--root", str(root), "--worker", sid,
                       "--receipt", attempt["receipt_file"], "--memory-mib", str(memory_mib)]
            with log_path.open("w", encoding="utf-8") as log:
                exit_code, rss = wait_worker(command, log, lock_fd)
            attempt.update(exit_code=exit_code, maximum_resident_set_kb=rss, finished_at=monitor.utc_now(), phase="finished")
            # Replay a child killed between baseline/pending/receipt replacements
            # before deciding whether that source committed successfully.
            with locked(root): recover(root)
            _, completed = receipt(sid)
            if not completed or completed.get("status") != "committed":
                text = log_path.read_text(encoding="utf-8", errors="replace")
                evidence_error = completed.get("result", {}) if completed else {}
                description = evidence_error.get("error", "")
                if "MemoryError" in text or evidence_error.get("error_type") == "MemoryError": reason = "memory_limit"
                elif exit_code < 0: reason = "terminated_signal_" + str(-exit_code)
                elif re.search(r"HTTP|urlopen|timed out|timeout", description, re.I): reason = "http_or_network_error"
                elif evidence_error: reason = "collection_error"
                else: reason = "worker_exit_" + str(exit_code)
                attempt["failure_kind"] = reason
                if not completed:
                    attempt["failure_result"] = {"id": sid, "status": "error", "error": reason,
                                                 "observed_at": monitor.utc_now(), "checked_maps": [], "added": [], "changed": [], "removed": [], "baseline": []}
            write(progress_path, progress)
            publish("running")
        progress["phase"] = "complete"; progress["finished_at"] = monitor.utc_now(); write(progress_path, progress)
        manifest = publish("complete")
        if snapshot is not None:
            snapshot.update(manifest)
        print(json.dumps(manifest, ensure_ascii=False))
        return 0 if manifest["status"] == "ok" else 1

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--site", action="append")
    parser.add_argument("--memory-mib", type=int, default=DEFAULT_MEMORY_MIB)
    parser.add_argument("--run-day")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--receipt", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        if not args.receipt or args.memory_mib <= 0: parser.error("Worker needs a receipt and positive memory limit")
        return worker(args)
    try:
        return run(args.root, args.site, args.memory_mib, args.run_day)
    except ScanBusy as error:
        print(json.dumps({"status": "busy", "error": str(error)})); return 75
if __name__ == "__main__": raise SystemExit(main())
