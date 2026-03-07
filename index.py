"""
╔══════════════════════════════════════════════════════════════════╗
║           Zayro Hosting — FastAPI Backend                        ║
║  ملف الـ Backend الرئيسي المتوافق مع Vercel Serverless           ║
╚══════════════════════════════════════════════════════════════════╝

📁 هيكل الملفات:
  /api/index.py          ← هذا الملف (نقطة الدخول الرئيسية)
  /api/routers/          ← مجلد الـ Endpoints المنظمة
  /user_scripts/         ← ضع ملفات Python الخاصة بك هنا ✅
  /public/index.html     ← الواجهة الأمامية
  /vercel.json           ← إعدادات Vercel
  /requirements.txt      ← المكتبات المطلوبة
"""

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import subprocess
import os
import sys
import json
import asyncio
import time
import signal
import psutil
from datetime import datetime
from pathlib import Path

# ══════════════════════════════════════════════════════════════════
# APP INITIALIZATION
# ══════════════════════════════════════════════════════════════════

app = FastAPI(
    title="Zayro Hosting API",
    description="Backend لاستضافة سكربتات Python",
    version="1.0.0"
)

# السماح لملف HTML بالتواصل مع الـ API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ══════════════════════════════════════════════════════════════════
# DIRECTORIES SETUP
# ══════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).parent.parent
SCRIPTS_DIR = BASE_DIR / "user_scripts"
PUBLIC_DIR  = BASE_DIR / "public"
LOGS_DIR    = BASE_DIR / "logs"

# إنشاء المجلدات إذا لم تكن موجودة
SCRIPTS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# ══════════════════════════════════════════════════════════════════
# IN-MEMORY STATE  (في Vercel كل request مستقل — استخدم DB للإنتاج)
# ══════════════════════════════════════════════════════════════════

# تخزين معلومات السيرفرات الجارية (في الذاكرة)
running_processes: dict[int, dict] = {}

MAX_SERVERS = 7

def get_server_state():
    """إرجاع حالة جميع السيرفرات"""
    servers = []
    for srv_id in range(1, MAX_SERVERS + 1):
        info = running_processes.get(srv_id)
        if info:
            # التحقق إذا كان الـ process لا يزال يعمل
            proc: subprocess.Popen = info.get("process")
            is_alive = proc and proc.poll() is None
            if not is_alive and srv_id in running_processes:
                running_processes.pop(srv_id)
                info = None

        if info:
            servers.append({
                "id": srv_id,
                "status": "running",
                "file": info["file"],
                "started_at": info["started_at"],
                "uptime": _calc_uptime(info["started_at"]),
                "cpu": _get_cpu(info.get("pid")),
                "memory": _get_mem(info.get("pid")),
            })
        else:
            servers.append({
                "id": srv_id,
                "status": "offline",
                "file": None,
                "started_at": None,
                "uptime": "—",
                "cpu": 0,
                "memory": 0,
            })
    return servers

def _calc_uptime(started_at: str) -> str:
    if not started_at:
        return "—"
    try:
        start = datetime.fromisoformat(started_at)
        delta = datetime.now() - start
        h = delta.seconds // 3600
        m = (delta.seconds % 3600) // 60
        s = delta.seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"
    except:
        return "—"

def _get_cpu(pid) -> int:
    if not pid:
        return 0
    try:
        p = psutil.Process(pid)
        return int(p.cpu_percent(interval=0.1))
    except:
        return 0

def _get_mem(pid) -> int:
    if not pid:
        return 0
    try:
        p = psutil.Process(pid)
        return int(p.memory_info().rss / 1024 / 1024)  # MB
    except:
        return 0


# ══════════════════════════════════════════════════════════════════
# MODELS (بيانات الطلبات)
# ══════════════════════════════════════════════════════════════════

class StartServerRequest(BaseModel):
    server_id: int
    filename: str
    extra_args: Optional[str] = ""

class InstallLibRequest(BaseModel):
    package_name: str
    version: Optional[str] = "latest"

class ConsoleCommandRequest(BaseModel):
    command: str
    server_id: Optional[int] = None


# ══════════════════════════════════════════════════════════════════
# STATIC FILES — تقديم ملف index.html
# ══════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """تقديم الواجهة الأمامية"""
    html_path = PUBLIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>index.html not found in /public/</h1>", status_code=404)


# ══════════════════════════════════════════════════════════════════
# API — DASHBOARD STATS
# ══════════════════════════════════════════════════════════════════

@app.get("/api/stats")
async def get_stats():
    """إحصائيات لوحة التحكم"""
    servers = get_server_state()
    running_count = sum(1 for s in servers if s["status"] == "running")
    files = list(SCRIPTS_DIR.glob("*.py"))

    return {
        "running_servers": running_count,
        "total_servers": MAX_SERVERS,
        "uploaded_files": len(files),
        "uptime_percent": round((running_count / MAX_SERVERS) * 100, 1),
        "timestamp": datetime.now().isoformat()
    }


# ══════════════════════════════════════════════════════════════════
# API — SERVERS
# ══════════════════════════════════════════════════════════════════

@app.get("/api/servers")
async def list_servers():
    """قائمة بجميع السيرفرات وحالتها"""
    return {"servers": get_server_state()}


@app.post("/api/servers/start")
async def start_server(req: StartServerRequest):
    """
    ▶️ تشغيل سيرفر
    - server_id: رقم السيرفر (1-7)
    - filename: اسم ملف Python في مجلد /user_scripts/
    """
    if req.server_id < 1 or req.server_id > MAX_SERVERS:
        raise HTTPException(400, f"server_id يجب أن يكون بين 1 و {MAX_SERVERS}")

    if req.server_id in running_processes:
        raise HTTPException(409, f"السيرفر {req.server_id} يعمل بالفعل")

    script_path = SCRIPTS_DIR / req.filename
    if not script_path.exists():
        raise HTTPException(404, f"الملف '{req.filename}' غير موجود في /user_scripts/")

    # تشغيل السكربت كـ subprocess
    log_file_path = LOGS_DIR / f"server_{req.server_id}.log"
    try:
        args = [sys.executable, str(script_path)]
        if req.extra_args:
            args.extend(req.extra_args.split())

        with open(log_file_path, "a") as log_f:
            process = subprocess.Popen(
                args,
                stdout=log_f,
                stderr=log_f,
                cwd=str(SCRIPTS_DIR),
            )

        running_processes[req.server_id] = {
            "file": req.filename,
            "process": process,
            "pid": process.pid,
            "started_at": datetime.now().isoformat(),
            "log_path": str(log_file_path),
        }

        _log(req.server_id, f"✅ بدأ تشغيل {req.filename} (PID: {process.pid})")

        return {
            "success": True,
            "message": f"✅ تم تشغيل السيرفر {req.server_id}",
            "server_id": req.server_id,
            "pid": process.pid,
            "file": req.filename
        }
    except Exception as e:
        raise HTTPException(500, f"فشل التشغيل: {str(e)}")


@app.post("/api/servers/{server_id}/stop")
async def stop_server(server_id: int):
    """⏹️ إيقاف سيرفر"""
    if server_id not in running_processes:
        raise HTTPException(404, f"السيرفر {server_id} لا يعمل")

    info = running_processes.pop(server_id)
    proc: subprocess.Popen = info["process"]

    try:
        proc.terminate()
        await asyncio.sleep(0.5)
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass

    _log(server_id, f"⏹️ تم إيقاف السيرفر")
    return {"success": True, "message": f"⏹️ تم إيقاف السيرفر {server_id}"}


@app.post("/api/servers/{server_id}/restart")
async def restart_server(server_id: int):
    """🔄 إعادة تشغيل سيرفر"""
    if server_id not in running_processes:
        raise HTTPException(404, f"السيرفر {server_id} لا يعمل")

    info = running_processes[server_id]
    filename = info["file"]

    # إيقاف أولاً
    await stop_server(server_id)
    await asyncio.sleep(0.5)

    # إعادة التشغيل
    return await start_server(StartServerRequest(server_id=server_id, filename=filename))


@app.get("/api/servers/{server_id}/logs")
async def get_server_logs(server_id: int, lines: int = 50):
    """📋 عرض سجلات السيرفر"""
    log_path = LOGS_DIR / f"server_{server_id}.log"

    if not log_path.exists():
        return {"logs": [], "message": "لا توجد سجلات بعد"}

    with open(log_path, "r", errors="replace") as f:
        all_lines = f.readlines()

    last_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
    return {
        "server_id": server_id,
        "logs": [l.rstrip() for l in last_lines],
        "total_lines": len(all_lines)
    }


@app.delete("/api/servers/{server_id}/logs")
async def clear_server_logs(server_id: int):
    """🗑️ مسح سجلات السيرفر"""
    log_path = LOGS_DIR / f"server_{server_id}.log"
    if log_path.exists():
        log_path.write_text("")
    return {"success": True, "message": f"تم مسح سجلات السيرفر {server_id}"}


# ══════════════════════════════════════════════════════════════════
# API — FILES
# ══════════════════════════════════════════════════════════════════

@app.get("/api/files")
async def list_files():
    """📁 قائمة الملفات المرفوعة"""
    files = []
    for f in SCRIPTS_DIR.glob("*.py"):
        stat = f.stat()
        files.append({
            "name": f.name,
            "size": _human_size(stat.st_size),
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return {"files": files}


@app.post("/api/files/upload")
async def upload_file(file: UploadFile = File(...)):
    """⬆️ رفع ملف Python"""
    if not file.filename.endswith(".py"):
        raise HTTPException(400, "يُسمح فقط بملفات Python (.py)")

    # حماية من path traversal
    safe_name = Path(file.filename).name
    dest = SCRIPTS_DIR / safe_name

    content = await file.read()
    dest.write_bytes(content)

    return {
        "success": True,
        "message": f"✅ تم رفع {safe_name}",
        "filename": safe_name,
        "size": _human_size(len(content))
    }


@app.delete("/api/files/{filename}")
async def delete_file(filename: str):
    """🗑️ حذف ملف"""
    # إيقاف أي سيرفر يستخدم هذا الملف
    for srv_id, info in list(running_processes.items()):
        if info["file"] == filename:
            await stop_server(srv_id)

    file_path = SCRIPTS_DIR / Path(filename).name
    if not file_path.exists():
        raise HTTPException(404, "الملف غير موجود")

    file_path.unlink()
    return {"success": True, "message": f"🗑️ تم حذف {filename}"}


@app.get("/api/files/{filename}/content")
async def get_file_content(filename: str):
    """📄 قراءة محتوى ملف"""
    file_path = SCRIPTS_DIR / Path(filename).name
    if not file_path.exists():
        raise HTTPException(404, "الملف غير موجود")
    return {"filename": filename, "content": file_path.read_text(errors="replace")}


# ══════════════════════════════════════════════════════════════════
# API — LIBRARIES
# ══════════════════════════════════════════════════════════════════

@app.get("/api/libs")
async def list_installed_libs():
    """📦 قائمة المكتبات المثبتة"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=json"],
            capture_output=True, text=True, timeout=10
        )
        pkgs = json.loads(result.stdout)
        return {"packages": pkgs}
    except Exception as e:
        return {"packages": [], "error": str(e)}


@app.post("/api/libs/install")
async def install_library(req: InstallLibRequest, background_tasks: BackgroundTasks):
    """📥 تثبيت مكتبة جديدة"""
    pkg = req.package_name.strip()
    # حماية من command injection
    if any(c in pkg for c in [";", "&", "|", "`", "$", "(", ")"]):
        raise HTTPException(400, "اسم المكتبة يحتوي على أحرف غير مسموح بها")

    pkg_spec = f"{pkg}=={req.version}" if req.version and req.version != "latest" else pkg

    background_tasks.add_task(_pip_install, pkg_spec)
    return {"success": True, "message": f"📥 جاري تثبيت {pkg_spec}..."}


async def _pip_install(pkg_spec: str):
    """تثبيت مكتبة في الخلفية"""
    subprocess.run(
        [sys.executable, "-m", "pip", "install", pkg_spec],
        capture_output=True, timeout=120
    )


@app.delete("/api/libs/{package_name}")
async def uninstall_library(package_name: str):
    """🗑️ إلغاء تثبيت مكتبة"""
    pkg = Path(package_name).name  # تنظيف
    result = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "-y", pkg],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode == 0:
        return {"success": True, "message": f"✅ تم حذف {pkg}"}
    raise HTTPException(500, f"فشل الحذف: {result.stderr}")


# ══════════════════════════════════════════════════════════════════
# API — CONSOLE COMMANDS
# ══════════════════════════════════════════════════════════════════

@app.post("/api/console/run")
async def run_console_command(req: ConsoleCommandRequest):
    """
    💻 تنفيذ أمر من الكونسول
    الأوامر المدعومة: status, start <n>, stop <n>, restart <n>, clear, help
    """
    cmd = req.command.strip().lower()
    parts = cmd.split()

    if not parts:
        return {"output": ""}

    if parts[0] == "help":
        return {"output": (
            "الأوامر المتاحة:\n"
            "  status          — عرض حالة السيرفرات\n"
            "  stop <id>       — إيقاف سيرفر\n"
            "  restart <id>    — إعادة تشغيل سيرفر\n"
            "  logs <id>       — عرض آخر سجلات سيرفر\n"
            "  files           — قائمة الملفات\n"
            "  libs            — قائمة المكتبات\n"
            "  clear           — مسح الكونسول"
        )}

    elif parts[0] == "status":
        servers = get_server_state()
        running = [s for s in servers if s["status"] == "running"]
        lines = [f"🟢 يعمل: {len(running)}/{MAX_SERVERS}"]
        for s in running:
            lines.append(f"  ↳ السيرفر {s['id']}: {s['file']} | uptime {s['uptime']}")
        return {"output": "\n".join(lines)}

    elif parts[0] == "stop" and len(parts) > 1:
        try:
            srv_id = int(parts[1])
            await stop_server(srv_id)
            return {"output": f"⏹️ تم إيقاف السيرفر {srv_id}"}
        except HTTPException as e:
            return {"output": f"❌ {e.detail}"}

    elif parts[0] == "restart" and len(parts) > 1:
        try:
            srv_id = int(parts[1])
            await restart_server(srv_id)
            return {"output": f"🔄 تمت إعادة تشغيل السيرفر {srv_id}"}
        except HTTPException as e:
            return {"output": f"❌ {e.detail}"}

    elif parts[0] == "logs" and len(parts) > 1:
        try:
            srv_id = int(parts[1])
            result = await get_server_logs(srv_id, lines=20)
            return {"output": "\n".join(result["logs"]) or "لا توجد سجلات"}
        except:
            return {"output": "❌ خطأ في قراءة السجلات"}

    elif parts[0] == "files":
        result = await list_files()
        if not result["files"]:
            return {"output": "📭 لا توجد ملفات"}
        lines = [f"📁 {f['name']} ({f['size']})" for f in result["files"]]
        return {"output": "\n".join(lines)}

    elif parts[0] == "libs":
        result = await list_installed_libs()
        pkgs = result.get("packages", [])[:10]
        lines = [f"📦 {p['name']} v{p['version']}" for p in pkgs]
        return {"output": "\n".join(lines) or "لا توجد مكتبات"}

    else:
        return {"output": f"⚠️ أمر غير معروف: '{cmd}' — اكتب 'help' للمساعدة"}


# ══════════════════════════════════════════════════════════════════
# API — WEBHOOKS (للتكامل مع GitHub / Cron Jobs)
# ══════════════════════════════════════════════════════════════════

@app.post("/api/webhooks/deploy")
async def webhook_deploy(payload: dict):
    """
    🔗 Webhook للـ Deployment التلقائي
    أرسل POST request بـ JSON:
    { "server_id": 1, "filename": "my_bot.py" }
    """
    server_id = payload.get("server_id")
    filename  = payload.get("filename")

    if not server_id or not filename:
        raise HTTPException(400, "مطلوب: server_id و filename")

    return await start_server(StartServerRequest(
        server_id=server_id,
        filename=filename
    ))


@app.post("/api/webhooks/health-check")
async def webhook_health():
    """
    ⏰ Scheduled Health Check
    يمكن استدعاؤه كل دقيقة من Vercel Cron لإبقاء السيرفرات تحت المراقبة
    """
    servers = get_server_state()
    running = [s for s in servers if s["status"] == "running"]
    return {
        "healthy": True,
        "running_servers": len(running),
        "checked_at": datetime.now().isoformat()
    }


# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════

def _human_size(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"

def _log(server_id: int, message: str):
    log_path = LOGS_DIR / f"server_{server_id}.log"
    with open(log_path, "a") as f:
        f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n")


# ══════════════════════════════════════════════════════════════════
# LOCAL DEV — تشغيل محلي بـ uvicorn
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    print("\n🚀 Zayro Hosting — تشغيل محلي")
    print("📡 API:      http://localhost:8000")
    print("🌐 Frontend: http://localhost:8000")
    print("📚 Docs:     http://localhost:8000/docs\n")
    uvicorn.run("index:app", host="0.0.0.0", port=8000, reload=True)
