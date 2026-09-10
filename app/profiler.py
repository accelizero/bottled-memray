import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def format_bytes(size_in_bytes: int | float) -> str:
    """Format bytes to human readable string (B, KB, MB, GB)."""
    if size_in_bytes is None:
        return "0 B"
    size = float(size_in_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024.0:
            return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.2f} PB"


class ProfilerManager:
    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.reports_dir = self.data_dir / "reports"
        self.sessions_dir = self.data_dir / "sessions"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.data_dir / "sessions.json"
        self._ensure_index()

    def _ensure_index(self):
        if not self.index_file.exists():
            with open(self.index_file, "w", encoding="utf-8") as f:
                json.dump([], f)

    def list_sessions(self) -> list[dict]:
        self._ensure_index()
        try:
            with open(self.index_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def save_session_meta(self, meta: dict):
        sessions = self.list_sessions()
        # Prepend new session
        sessions = [s for s in sessions if s.get("id") != meta.get("id")]
        sessions.insert(0, meta)
        # Keep latest 100
        sessions = sessions[:100]
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(sessions, f, indent=2, ensure_ascii=False)

    def delete_session(self, session_id: str):
        sessions = self.list_sessions()
        sessions = [s for s in sessions if s.get("id") != session_id]
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(sessions, f, indent=2, ensure_ascii=False)
        target_dir = self.reports_dir / session_id
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)

    def profile_code(
        self,
        code: str,
        name: str = "Memory Profile Run",
        native: bool = False,
        follow_fork: bool = False,
        trace_python_allocators: bool = False,
        timeout_seconds: int = 60,
    ) -> dict:
        session_id = uuid.uuid4().hex[:12]
        session_dir = self.reports_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        script_file = session_dir / "script.py"
        bin_file = session_dir / "profile.bin"
        flame_file = session_dir / "flamegraph.html"
        table_file = session_dir / "table.html"
        stats_file = session_dir / "stats.json"
        summary_file = session_dir / "summary.txt"

        with open(script_file, "w", encoding="utf-8") as f:
            f.write(code)

        # 1. Run memray
        cmd = ["memray", "run", "-o", str(bin_file)]
        if native:
            cmd.append("--native")
        if follow_fork:
            cmd.append("--follow-fork")
        if trace_python_allocators:
            cmd.append("--trace-python-allocators")
        cmd.append(str(script_file))

        start_time = time.time()
        stdout_text = ""
        stderr_text = ""
        status = "success"
        error_message = ""

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=str(session_dir),
            )
            stdout_text = res.stdout
            stderr_text = res.stderr
            if res.returncode != 0:
                status = "error"
                error_message = f"Script exited with return code {res.returncode}: {stderr_text[:500]}"
        except subprocess.TimeoutExpired:
            status = "timeout"
            error_message = f"Execution timed out after {timeout_seconds} seconds."
        except Exception as e:
            status = "error"
            error_message = str(e)

        elapsed = time.time() - start_time

        # If bin file exists, generate reports
        report_meta = self._generate_reports(bin_file, flame_file, table_file, stats_file, summary_file)

        session_meta = {
            "id": session_id,
            "name": name,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": status,
            "error_message": error_message,
            "elapsed_seconds": round(elapsed, 3),
            "native": native,
            "peak_memory_bytes": report_meta.get("peak_memory_bytes", 0),
            "peak_memory_human": report_meta.get("peak_memory_human", "0 B"),
            "total_allocations": report_meta.get("total_allocations", 0),
            "has_flamegraph": flame_file.exists(),
            "has_table": table_file.exists(),
            "has_stats": stats_file.exists(),
            "stdout": stdout_text,
            "stderr": stderr_text,
            "summary": report_meta.get("summary_text", ""),
            "stats": report_meta.get("stats_dict", {}),
            "code": code,
        }

        self.save_session_meta(session_meta)
        return session_meta

    def process_uploaded_bin(self, uploaded_bin_path: Path, filename: str) -> dict:
        session_id = uuid.uuid4().hex[:12]
        session_dir = self.reports_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        bin_file = session_dir / "profile.bin"
        shutil.copy2(uploaded_bin_path, bin_file)

        flame_file = session_dir / "flamegraph.html"
        table_file = session_dir / "table.html"
        stats_file = session_dir / "stats.json"
        summary_file = session_dir / "summary.txt"

        report_meta = self._generate_reports(bin_file, flame_file, table_file, stats_file, summary_file)

        session_meta = {
            "id": session_id,
            "name": f"Uploaded: {filename}",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "success",
            "error_message": "",
            "elapsed_seconds": 0,
            "native": False,
            "peak_memory_bytes": report_meta.get("peak_memory_bytes", 0),
            "peak_memory_human": report_meta.get("peak_memory_human", "0 B"),
            "total_allocations": report_meta.get("total_allocations", 0),
            "has_flamegraph": flame_file.exists(),
            "has_table": table_file.exists(),
            "has_stats": stats_file.exists(),
            "stdout": "",
            "stderr": "",
            "summary": report_meta.get("summary_text", ""),
            "stats": report_meta.get("stats_dict", {}),
            "code": "# Profile generated from uploaded binary",
        }

        self.save_session_meta(session_meta)
        return session_meta

    def _generate_reports(
        self,
        bin_file: Path,
        flame_file: Path,
        table_file: Path,
        stats_file: Path,
        summary_file: Path,
    ) -> dict:
        if not bin_file.exists() or bin_file.stat().st_size == 0:
            return {}

        # 1. Flamegraph
        try:
            subprocess.run(
                ["memray", "flamegraph", str(bin_file), "-o", str(flame_file), "--force"],
                capture_output=True,
                check=False,
                timeout=30,
            )
        except Exception as e:
            logger.warning("Error generating flamegraph: %s", e)

        # 2. Table
        try:
            subprocess.run(
                ["memray", "table", str(bin_file), "-o", str(table_file), "--force"],
                capture_output=True,
                check=False,
                timeout=30,
            )
        except Exception as e:
            logger.warning("Error generating table: %s", e)

        # 3. Stats JSON
        stats_dict = {}
        try:
            res = subprocess.run(
                ["memray", "stats", "--json", str(bin_file), "-o", str(stats_file), "--force"],
                capture_output=True,
                check=False,
                timeout=20,
            )
            if stats_file.exists():
                with open(stats_file, "r", encoding="utf-8") as f:
                    stats_dict = json.load(f)
        except Exception as e:
            logger.warning("Error generating stats: %s", e)

        # 4. Summary Text
        summary_text = ""
        try:
            res = subprocess.run(
                ["memray", "summary", str(bin_file)],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            summary_text = res.stdout
            with open(summary_file, "w", encoding="utf-8") as f:
                f.write(summary_text)
        except Exception as e:
            logger.warning("Error generating summary: %s", e)

        peak_bytes = 0
        total_allocs = 0
        if stats_dict:
            metadata = stats_dict.get("metadata", {})
            peak_bytes = metadata.get("peak_memory", 0)
            total_allocs = stats_dict.get("total_num_allocations", 0) or metadata.get("total_allocations", 0)

        return {
            "peak_memory_bytes": peak_bytes,
            "peak_memory_human": format_bytes(peak_bytes),
            "total_allocations": total_allocs,
            "summary_text": summary_text,
            "stats_dict": stats_dict,
        }
