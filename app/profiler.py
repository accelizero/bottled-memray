import json
import logging
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def format_bytes(bytes_val: int) -> str:
    if not bytes_val or bytes_val <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    val = float(bytes_val)
    while val >= 1024 and i < len(units) - 1:
        val /= 1024.0
        i += 1
    return f"{val:.2f} {units[i]}"


ZH_REPLACEMENTS = [
    # Flamegraph & Table Navbar Brand
    (r'<span class="navbar-brand mb-0 mr-2 h1">memray</span> flamegraph report',
     r'<span class="navbar-brand mb-0 mr-2 h1">memray</span> 内存火焰图分析报告'),
    (r'<span class="navbar-brand mb-0 mr-2 h1">memray</span> table report',
     r'<span class="navbar-brand mb-0 mr-2 h1">memray</span> 内存分配明细表'),

    # Allocator Badge
    (r'Python Allocator: pymalloc', r'Python 内存分配器: pymalloc (对象池机制)'),

    # Filters
    (r'>Hide Irrelevant Frames<', r'>隐藏非核心/解释器内部调用帧<'),
    (r'>Hide Import System Frames<', r'>隐藏 Python 模块导入系统调用帧<'),

    # Flame / Icicles buttons
    (r'&nbsp;\s*Flames\s*</label>', r'&nbsp; 火焰图 (自底向上)</label>'),
    (r'Icicles\s*&nbsp;', r'冰柱图 (自顶向下) &nbsp;'),

    # Zoom & Buttons
    (r'>Reset Zoom<', r'>重置视角/缩放<'),
    (r'>Memory Graph<', r'>内存变化曲线<'),
    (r'>Stats<', r'>统计汇总<'),
    (r'>Help<', r'>帮助说明<'),
    (r'>Close<', r'>关闭<'),
    (r'>Reset<', r'>重置<'),

    # Tooltips
    (r'title="Hide CPython eval frames and Memray-related frames"',
     r'title="隐藏 CPython 解释器循环和 Memray 内部调用栈，只展示您的业务代码"'),
    (r'title="Hide frames related to the Python import system"',
     r'title="隐藏 Python 导入模块时的内部堆栈"'),
    (r'title="Enable flame graph mode: functions above their callers with the root at the bottom"',
     r'title="启用经典火焰图模式：根入口在底部，被调用函数在上方"'),
    (r'title="Enable icicle graph mode: functions below their callers with the root at the top"',
     r'title="启用冰柱图模式：根入口在顶部，自顶向下展开调用"'),

    # Search placeholder
    (r'placeholder="Search"', r'placeholder="🔍 搜索函数、文件名或代码行..."'),

    # Table columns
    (r'title:"Thread ID"', r'title:"线程 ID"'),
    (r'title:"Size"', r'title:"内存大小"'),
    (r'title:"Allocator"', r'title:"分配器"'),
    (r'title:"Allocations"', r'title:"分配次数"'),
    (r'title:"Location"', r'title:"代码位置/调用行"'),

    # Modal titles
    (r'Resident set size over time', r'常驻内存 (RSS) 随时间消耗曲线'),
]


ZH_TOOLTIP_INJECTION = """
<script>
(function() {
  // Mutation observer for dynamic flamegraph tooltip
  const observer = new MutationObserver(mutations => {
    mutations.forEach(mutation => {
      mutation.addedNodes.forEach(node => {
        if (node.nodeType === 1 && (node.classList?.contains('d3-flame-graph-tip') || node.classList?.contains('tooltip'))) {
          translateTip(node);
        }
      });
      if (mutation.target && mutation.target.classList?.contains('d3-flame-graph-tip')) {
        translateTip(mutation.target);
      }
    });
  });

  observer.observe(document.body, { childList: true, subtree: true, characterData: true });

  function translateTip(el) {
    if (!el) return;
    let html = el.innerHTML;
    if (html.includes(' total<br>') || html.includes(' allocation')) {
      html = html.replace(/([0-9.]+\s*[KMGT]?B)\s*total/g, '总计内存占用: <b style="color:#fb923c">$1</b>');
      html = html.replace(/([0-9,]+)\s*allocations?/g, '累计分配次数: <b style="color:#38bdf8">$1</b> 次');
      html = html.replace(/Thread ID:/g, '线程编号:');
      html = html.replace(/File\s+([^,]+),\s*line\s+([0-9]+)\s+in\s+([^<]+)/g, '代码文件: <span style="color:#94a3b8">$1</span><br>第 <b>$2</b> 行函数: <span style="color:#4ade80">$3</span>');
      el.innerHTML = html;
    }
  }
})();
</script>
"""


def apply_chinese_to_html_content(content: str) -> str:
    for pattern, repl in ZH_REPLACEMENTS:
        content = re.sub(pattern, repl, content)
    if "translateTip" not in content and "</body>" in content:
        content = content.replace("</body>", f"{ZH_TOOLTIP_INJECTION}\n</body>")
    return content


class ProfilerManager:
    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.reports_dir = self.data_dir / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.data_dir / "sessions.json"

    def list_sessions(self) -> list:
        if not self.index_file.exists():
            return []
        try:
            with open(self.index_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def save_session_meta(self, meta: dict):
        sessions = self.list_sessions()
        sessions = [s for s in sessions if s.get("id") != meta.get("id")]
        sessions.insert(0, meta)
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
            if flame_file.exists():
                self._localize_file(flame_file)
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
            if table_file.exists():
                self._localize_file(table_file)
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

    def _localize_file(self, file_path: Path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            content = apply_chinese_to_html_content(content)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            logger.warning("Failed to localize file %s: %s", file_path, e)
