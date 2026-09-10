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
    (r'Memray run stats', r'Memray 执行采样统计汇总'),
    (r'How to interpret flamegraph reports', r'如何解读内存火焰图报告'),
    (r'How to interpret table reports', r'如何解读内存分配明细表'),
    (r'Resident set size over time', r'常驻内存 (RSS) 随时间消耗曲线'),

    # Stats Modal Body Labels
    (r'Command line:', r'执行命令行:'),
    (r'Start time:', r'开始执行时间:'),
    (r'End time:', r'结束执行时间:'),
    (r'Duration:', r'采样持续总耗时:'),
    (r'Total number of allocations:', r'内存分配调用总次数:'),
    (r'Total number of frames seen:', r'遍历调用栈帧总数:'),
    (r'Peak memory usage:', r'峰值内存占用总量:'),
    (r'Python allocator:', r'Python 内存分配器:'),

    # Help Modal Body Translations (Flamegraph)
    (r'The flame graph displays a snapshot of memory used across stack frames at the time <b>when the memory usage was at its peak</b>\.',
     r'火焰图展示了<b>内存使用达到峰值时刻</b>，跨各个调用调用栈帧所消耗内存的瞬时快照。'),
    (r'The vertical ordering of the stack frames corresponds to the order of function calls, from parent to children\.\s*The horizontal ordering does not represent the passage of time in the application: they simply represent child frames in arbitrary order\.',
     r'<b>纵向层级</b>对应函数调用顺序（自父函数调用到子函数）。<b>横向宽度</b>不代表时间先后，而代表内存占用比例：横条越宽，说明该代码路径吃掉的内存越多。'),
    (r'On the flame graph, each bar represents a stack frame and shows the code which triggered the memory allocation\.\s*Hovering over the frame you can also see the overall memory allocated in the given frame and its children and the number of times allocations have occurred\.',
     r'在火焰图中，每个色块代表一个函数调用栈帧，并标注触发内存申请的具体代码位置。鼠标悬停在色块上可查看该帧及其子调用累计消耗的总内存、自身内存及调用次数。'),
    (r'The <b>Show/Hide Irrelevant Frames</b> button can be used to reveal and hide frames which contain allocations in code which might not be\s*relevant for the application\.\s*These include frames in the CPython eval loop as well as frames introduced by memray during the analysis\.',
     r'<b>“隐藏非核心/解释器内部调用帧”</b>开关用于隐藏 CPython 解释器自身的执行循环以及 Memray 工具内部开销，让您专注于自己的业务代码。'),
    (r'You can find more information in the <a target="_blank"\s*href="https://bloomberg\.github\.io/memray/flamegraph\.html">documentation</a>\.',
     r'了解更多详情，请参阅 Memray 官方中文与原厂文档。'),

    # Help Modal Body Translations (Table)
    (r'The table reporter provides a simple tabular representation of memory\s*allocations in the target <b>when the memory usage was at its peak</b>\.',
     r'内存明细表以表格形式直观展示<b>内存达到峰值时刻</b>目标代码的分配清单，便于按占用大小排序定位行号。'),
    (r'You can find more information in the <a target="_blank" href="https://bloomberg\.github\.io/memray/table\.html">documentation</a>\.',
     r'了解更多详情，请参阅 Memray 官方文档。'),
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
