import logging
import os
import shutil
import tempfile
from pathlib import Path
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .profiler import ProfilerManager, ZH_INJECTION_SCRIPT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = os.environ.get("DATA_DIR") or os.environ.get("BOTTLE_APP_DATA_DIR") or "/data/app_data/memray"
profiler_manager = ProfilerManager(DATA_DIR)

app = FastAPI(title="Memray Web Studio", version="1.20.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class ProfileRequest(BaseModel):
    code: str
    name: str = "Memory Profile"
    native: bool = False
    follow_fork: bool = False
    trace_python_allocators: bool = False
    timeout: int = 60


@app.get("/healthz")
@app.get("/health")
def healthz():
    return {"status": "ok", "app": "memray-web-studio", "version": "1.20.0"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/api/profile")
def profile_code(req: ProfileRequest):
    code = req.code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="Code cannot be empty.")

    try:
        session = profiler_manager.profile_code(
            code=code,
            name=req.name,
            native=req.native,
            follow_fork=req.follow_fork,
            trace_python_allocators=req.trace_python_allocators,
            timeout_seconds=min(req.timeout, 120),
        )
        return session
    except Exception as e:
        logger.exception("Error running profile")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file selected")

    suffix = Path(file.filename).suffix.lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        if suffix == ".bin":
            session = profiler_manager.process_uploaded_bin(tmp_path, file.filename)
            return session
        elif suffix == ".py":
            with open(tmp_path, "r", encoding="utf-8", errors="ignore") as f:
                code = f.read()
            session = profiler_manager.profile_code(
                code=code,
                name=f"Script: {file.filename}",
            )
            return session
        else:
            raise HTTPException(status_code=400, detail="Only .bin (Memray capture) or .py files are supported.")
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


@app.get("/api/sessions")
def list_sessions():
    return profiler_manager.list_sessions()


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    sessions = profiler_manager.list_sessions()
    for s in sessions:
        if s.get("id") == session_id:
            return s
    raise HTTPException(status_code=404, detail="Session not found")


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    profiler_manager.delete_session(session_id)
    return {"status": "ok"}


def _read_and_inject_report(report_file: Path) -> Response:
    if not report_file.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    try:
        with open(report_file, "r", encoding="utf-8") as f:
            html = f.read()
        if "applyChineseLocalization" not in html and "</body>" in html:
            html = html.replace("</body>", f"{ZH_INJECTION_SCRIPT}\n</body>")
        return HTMLResponse(content=html)
    except Exception:
        return FileResponse(str(report_file), media_type="text/html")


@app.get("/reports/{session_id}/flamegraph")
def get_flamegraph(session_id: str):
    report_file = Path(DATA_DIR) / "reports" / session_id / "flamegraph.html"
    return _read_and_inject_report(report_file)


@app.get("/reports/{session_id}/table")
def get_table(session_id: str):
    report_file = Path(DATA_DIR) / "reports" / session_id / "table.html"
    return _read_and_inject_report(report_file)


@app.get("/reports/{session_id}/download/{file_type}")
def download_file(session_id: str, file_type: str):
    session_dir = Path(DATA_DIR) / "reports" / session_id
    if not session_dir.exists():
        raise HTTPException(status_code=404, detail="Session not found")

    mapping = {
        "bin": ("profile.bin", f"memray-{session_id}.bin", "application/octet-stream"),
        "flamegraph": ("flamegraph.html", f"memray-flamegraph-{session_id}.html", "text/html"),
        "table": ("table.html", f"memray-table-{session_id}.html", "text/html"),
        "stats": ("stats.json", f"memray-stats-{session_id}.json", "application/json"),
        "script": ("script.py", f"script-{session_id}.py", "text/x-python"),
    }

    if file_type not in mapping:
        raise HTTPException(status_code=400, detail="Invalid file type")

    fname, dl_name, mime = mapping[file_type]
    file_path = session_dir / fname
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File {fname} does not exist")

    return FileResponse(str(file_path), media_type=mime, filename=dl_name)
