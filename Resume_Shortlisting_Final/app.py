from flask import Flask, request, jsonify, send_file, session, redirect, url_for
import threading
import uuid
import time
import secrets
import logging
import os
from datetime import datetime
from functools import wraps
from pathlib import Path
import pandas as pd
from flask import render_template

from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import json

load_dotenv(override=True)  # ensure Cloudflare creds load in server mode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET_KEY"]
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

OPSTEAM_SECRET_CODE = os.environ["OPSTEAM_SECRET_CODE"]

limiter = Limiter(get_remote_address, app=app, default_limits=[])

# In-memory reset tokens: token -> {employee_id, expires_at}
RESET_TOKENS = {}


from functools import wraps

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user' not in session:
            if request.path.startswith('/api/'):
                return jsonify({"error": "not_logged_in"}), 401
            return redirect(url_for('start_page'))
        return f(*args, **kwargs)
    return wrapper


def opsteam_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user' not in session or session['user'].get('role') != 'opsteam':
            if request.path.startswith('/api/'):
                return jsonify({"error": "not_authorized"}), 403
            return redirect(url_for('start_page'))
        return f(*args, **kwargs)
    return wrapper

from engines.resume_engine import parse_resumes, detect_updated_resumes
from engines.jd_engine import parse_jd
from engines.scoring_engine import score_all_resumes

DATA_JDS_DIR = Path("data/jds")
DATA_RESUMES_DIR = Path("data/resumes")
STORAGE_DIR = Path("storage")
USERS_FILE = STORAGE_DIR / "users.json"


ALLOWED_JD_EXT = {".pdf", ".docx", ".doc", ".txt"}


# Job store — persisted to disk so restarts don't lose state
JOBS_FILE = Path("storage/jobs.json")

def _load_jobs():
    if JOBS_FILE.exists():
        try:
            return json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _save_jobs(jobs):
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    JOBS_FILE.write_text(json.dumps(jobs, indent=2), encoding="utf-8")

JOBS = _load_jobs()

def new_job(job_type: str):
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "id": job_id,
        "type": job_type,
        "status": "queued",
        "progress": 0,
        "message": "Queued",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "result": None,
        "error": None
    }
    _save_jobs(JOBS)
    return job_id


def set_job(job_id: str, **fields):
    if job_id in JOBS:
        JOBS[job_id].update(fields)
        _save_jobs(JOBS)

@app.get("/api/job/<job_id>/status")
@login_required
def job_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify({
        "id": job["id"],
        "type": job["type"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "created_at": job["created_at"]
    })


def _load_users():
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    if not USERS_FILE.exists():
        return []
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8")) or []
    except Exception:
        return []


def _save_users(users):
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")


def _find_user_by_employee_id(employee_id):
    if not employee_id:
        return None
    employee_id = employee_id.strip().lower()
    for user in _load_users():
        if (user.get("employee_id") or "").strip().lower() == employee_id:
            return user
    return None


def _find_user_by_email(email):
    if not email:
        return None
    email = email.strip().lower()
    for user in _load_users():
        if (user.get("email") or "").strip().lower() == email:
            return user
    return None


@app.post("/api/auth")
@limiter.limit("10 per minute")
def api_auth():
    data = request.get_json(force=True, silent=True) or {}
    action = (data.get("action") or "login").strip().lower()
    role = (data.get("role") or "employee").strip()
    employee_id = (data.get("employee_id") or "").strip()
    password = (data.get("password") or "").strip()

    if action == "signup":
        full_name = (data.get("full_name") or "").strip()
        email = (data.get("email") or "").strip()
        grade = (data.get("grade") or "").strip()

        if not full_name or not email or not employee_id or not password or not grade:
            return jsonify({"error": "All signup fields are required."}), 400

        if role == "opsteam":
            admin_code = (data.get("admin_code") or "").strip()
            if admin_code != OPSTEAM_SECRET_CODE:
                return jsonify({"error": "Invalid opsteam secret code."}), 403

        if _find_user_by_employee_id(employee_id) is not None:
            return jsonify({"error": "Employee ID already exists."}), 409

        if _find_user_by_email(email) is not None:
            return jsonify({"error": "Email already exists."}), 409

        users = _load_users()
        users.append({
            "full_name": full_name,
            "email": email,
            "employee_id": employee_id,
            "role": role,
            "grade": grade,
            "password_hash": generate_password_hash(password)
        })
        _save_users(users)
        return jsonify({"status": "ok", "message": "Signup completed. Please log in."})

    if action == "login":
        if not employee_id or not password:
            return jsonify({"error": "Employee ID and password are required."}), 400

        user = _find_user_by_employee_id(employee_id)
        if user is None:
            return jsonify({"error": "User not found."}), 404

        if not check_password_hash(user.get("password_hash", ""), password):
            logger.warning("Failed login attempt for employee_id=%s", employee_id)
            return jsonify({"error": "Invalid credentials."}), 401

        # Check if user's role matches requested role
        user_role = user.get("role", "").strip().lower()
        requested_role = role.strip().lower()
        if requested_role == "opsteam" and user_role != "opsteam":
            return jsonify({"error": "You are not authorized as an opsteam member."}), 403

        # Set session data
        session['user'] = {
            'employee_id': user['employee_id'],
            'full_name': user.get('full_name', ''),
            'email': user['email'],
            'role': user['role'],
            'grade': user.get('grade', '')
        }

        target = "/ops-dashboard" if role == "opsteam" else "/employee-portal"
        return jsonify({"status": "ok", "redirect": target})

    return jsonify({"error": "Invalid auth action."}), 400


@app.post("/api/logout")
def api_logout():
    session.pop('user', None)
    return jsonify({"status": "ok", "redirect": "/"})


@app.get("/api/user")
@login_required
def api_user():
    return jsonify(session['user'])


@app.get("/reset-password")
def reset_password_page():
    return render_template("reset_password.html")


@app.post("/api/reset-password/verify")
@limiter.limit("5 per minute")
def api_reset_password_verify():
    data = request.get_json(force=True, silent=True) or {}
    employee_id = (data.get("employee_id") or "").strip()
    email = (data.get("email") or "").strip()

    if not employee_id or not email:
        return jsonify({"error": "Employee ID and email are required."}), 400

    user = _find_user_by_employee_id(employee_id)
    if user is None:
        return jsonify({"error": "User not found."}), 404

    if user.get("email", "").strip().lower() != email.lower():
        return jsonify({"error": "Email does not match our records."}), 400

    reset_token = secrets.token_urlsafe(32)
    RESET_TOKENS[reset_token] = {"employee_id": employee_id, "expires_at": time.time() + 600}
    return jsonify({"status": "ok", "reset_token": reset_token})


@app.post("/api/reset-password/reset")
@limiter.limit("5 per minute")
def api_reset_password_reset():
    data = request.get_json(force=True, silent=True) or {}
    reset_token = (data.get("reset_token") or "").strip()
    new_password = (data.get("new_password") or "").strip()

    if not reset_token or not new_password:
        return jsonify({"error": "Reset token and new password are required."}), 400

    if len(new_password) < 6:
        return jsonify({"error": "Password must be at least 6 characters long."}), 400

    entry = RESET_TOKENS.pop(reset_token, None)
    if not entry or time.time() > entry["expires_at"]:
        return jsonify({"error": "Invalid or expired reset token."}), 400

    employee_id = entry["employee_id"]
    users = _load_users()
    for u in users:
        if u.get("employee_id", "").strip().lower() == employee_id.lower():
            u["password_hash"] = generate_password_hash(new_password)
            break

    _save_users(users)
    return jsonify({"status": "ok", "message": "Password reset successfully."})


@app.get("/api/job/<job_id>/result")
@login_required
def job_result(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job.get("result") or {})


@app.get("/api/jds/list")
@login_required
def list_jds():
    DATA_JDS_DIR.mkdir(parents=True, exist_ok=True)

    # Load registry and master for enrichment
    from utils.req_id import all_req_ids
    registry = all_req_ids()  # { filename: req_id }

    # Build role_name lookup from jds_master.json
    role_lookup = {}  # { filename: role_name }
    master_path = STORAGE_DIR / "jds_master.json"
    if master_path.exists():
        with open(master_path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    obj = json.loads(ln)
                    fname = (obj.get("Meta") or {}).get("jd_source_filename", "")
                    role = obj.get("Role_name") or ""
                    if fname:
                        role_lookup[fname] = role
                except Exception:
                    continue

    jds = []
    for p in sorted(DATA_JDS_DIR.glob("*")):
        if p.is_file() and p.suffix.lower() in ALLOWED_JD_EXT:
            req_id = registry.get(p.name, "")
            role_name = role_lookup.get(p.name, "")
            jds.append({
                "filename": p.name,
                "req_id": req_id,
                "role_name": role_name,
                "label": f"{role_name} | {req_id}" if role_name and req_id else (req_id or p.name)
            })
    return jsonify({"jds": jds})


@app.get("/api/resumes/detect-updated")
@login_required
def api_detect_updated():
    DATA_RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    result = detect_updated_resumes(DATA_RESUMES_DIR)
    return jsonify(result)


@app.post("/api/resumes/parse")
@login_required
def api_parse_resumes():
    files = request.files.getlist("resumes")
    force_flag = request.form.get("force_reparse", "0") == "1"
    parse_mode = request.form.get("parse_mode", "all")

    if not files:
        return jsonify({"error": "No resume files uploaded"}), 400

    DATA_RESUMES_DIR.mkdir(parents=True, exist_ok=True)

    # Save uploaded files
    for f in files:
        if f and f.filename:
            fname = secure_filename(f.filename)
            (DATA_RESUMES_DIR / fname).write_bytes(f.read())

    job_id = new_job("resume_parse")

    def worker():
        try:
            set_job(job_id, status="running", progress=10, message="Parsing resumes...")
            result = parse_resumes(DATA_RESUMES_DIR, force_reparse=force_flag, parse_mode=parse_mode, verbose=True)
            set_job(job_id, status="done", progress=100, message="Resume parsing completed",
                    result={"count": len(result)})
        except Exception as e:
            set_job(job_id, status="error", progress=100, message="Failed", error=str(e))

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.get("/api/jd/raw")
@login_required
def api_jd_raw():
    name = (request.args.get('name') or '').strip()
    if not name:
        return jsonify({"error": "No name provided"}), 400
    jd_path = (DATA_JDS_DIR / name).resolve()
    if not str(jd_path).startswith(str(DATA_JDS_DIR.resolve())):
        return jsonify({"error": "Invalid path"}), 400
    if not jd_path.exists():
        return jsonify({"error": "Not found"}), 404
    try:
        from utils.file_reader import read_any
        text = read_any(jd_path)
        return jsonify({"text": text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/jd/preview")
@login_required
def api_jd_preview():
    name = (request.args.get('name') or '').strip()
    if not name:
        return jsonify({"error": "No name provided"}), 400

    from utils.req_id import get_req_id
    stem = Path(name).stem

    # Try stem_reqid.json first, then plain stem.json
    req_id = get_req_id(name)
    if req_id:
        jd_path = STORAGE_DIR / "jd_jsons" / f"{stem}_{req_id}.json"
    else:
        jd_path = STORAGE_DIR / "jd_jsons" / f"{stem}.json"

    # Fallback: scan for any file matching stem_*.json
    if not jd_path.exists():
        matches = list((STORAGE_DIR / "jd_jsons").glob(f"{stem}_*.json"))
        jd_path = matches[0] if matches else jd_path

    if not jd_path.exists():
        return jsonify({"error": "Not found"}), 404
    return jsonify(json.loads(jd_path.read_text(encoding="utf-8")))


@app.post("/api/jd/check-duplicate")
@login_required
def api_jd_check_duplicate():
    """
    Pre-upload duplicate check.
    Accepts: multipart file OR form field jd_text.
    Returns: { status: 'exact'|'similar'|'new', ... }
    """
    from utils.file_reader import read_any
    from utils.text_cleaner import clean_text
    from utils.jd_duplicate import check_duplicate
    import tempfile, os

    jd_file = request.files.get("jd_file")
    jd_text = (request.form.get("jd_text") or "").strip()
    filename = (request.form.get("filename") or "uploaded_jd.txt").strip()

    try:
        if jd_file and jd_file.filename:
            filename = jd_file.filename
            suffix = Path(filename).suffix.lower()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                jd_file.save(tmp.name)
                tmp_path = Path(tmp.name)
            try:
                raw = read_any(tmp_path)
            finally:
                os.unlink(tmp_path)
        elif jd_text:
            raw = jd_text
        else:
            return jsonify({"status": "new"})

        text = clean_text(raw)
        result = check_duplicate(text, filename)
        return jsonify(result)

    except Exception as e:
        return jsonify({"status": "new", "warning": str(e)})


@app.post("/api/jd/parse")
@login_required
def api_parse_jd():
    jd_file = request.files.get("jd_file")
    jd_selected = (request.form.get("jd_selected") or "").strip()
    jd_text = (request.form.get("jd_text") or "").strip()
    force_flag = request.form.get("force_reparse", "0") == "1"
    update_existing_req_id = (request.form.get("update_existing_req_id") or "").strip()
    update_existing_filename = (request.form.get("update_existing_filename") or "").strip()

    DATA_JDS_DIR.mkdir(parents=True, exist_ok=True)

    jd_path = None

    # Priority: file upload > text input > selected file
    if jd_file and jd_file.filename:
        fname = secure_filename(jd_file.filename)
        # If updating existing, save under the existing filename to preserve req_id mapping
        if update_existing_filename:
            fname = secure_filename(update_existing_filename)
        (DATA_JDS_DIR / fname).write_bytes(jd_file.read())
        jd_path = DATA_JDS_DIR / fname
    elif jd_text:
        import time
        if update_existing_filename:
            fname = secure_filename(update_existing_filename)
        else:
            fname = f"jd_text_{int(time.time())}.txt"
        jd_path = DATA_JDS_DIR / fname
        jd_path.write_text(jd_text, encoding="utf-8")
    else:
        if not jd_selected:
            return jsonify({"error": "No JD selected, uploaded, or written"}), 400
        jd_path = DATA_JDS_DIR / jd_selected
        if not jd_path.exists():
            return jsonify({"error": f"Selected JD not found: {jd_selected}"}), 404

    job_id = new_job("jd_parse")

    def worker():
        try:
            set_job(job_id, status="running", progress=10, message="Parsing JD...")
            result = parse_jd(jd_path, force_reparse=force_flag, verbose=True)
            set_job(job_id, status="done", progress=100, message="JD parsing completed", result=result)
        except Exception as e:
            set_job(job_id, status="error", progress=100, message="Failed", error=str(e))

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"job_id": job_id, "jd_file": jd_path.name})


@app.post("/api/score")
@login_required
def api_score():
    top_n = int(request.form.get("top_n", "100"))
    job_id = new_job("score")

    def worker():
        try:
            set_job(job_id, status="running", progress=5, message="Scoring resumes...")
            result = score_all_resumes(top_n=top_n, verbose=True)

            # attach jd_json for UI filtered export (normalize pointer safely)
            try:
                pointer_path = STORAGE_DIR / "jd_latest.json"
                if pointer_path.exists():
                    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
                    use = (pointer.get("use") or "").strip()

                    cand = STORAGE_DIR / "jd_jsons" / use
                    if not cand.exists():
                        p = Path(use)
                        # try stem + .json
                        cand2 = STORAGE_DIR / "jd_jsons" / f"{p.stem}.json"
                        if cand2.exists():
                            cand = cand2
                        else:
                            # try appending .json
                            if not use.lower().endswith(".json"):
                                cand3 = STORAGE_DIR / "jd_jsons" / (use + ".json")
                                if cand3.exists():
                                    cand = cand3

                    if cand.exists():
                        jd_json = json.loads(cand.read_text(encoding="utf-8"))
                        result["jd_json"] = jd_json
                    else:
                        logger.warning("jd_json not found for pointer '%s' under %s", use, STORAGE_DIR / 'jd_jsons')
                else:
                    logger.warning("jd_latest.json not found; did you parse JD?")
            except Exception as e:
                logger.error("attach jd_json failed: %s", repr(e))

            set_job(job_id, status="done", progress=100, message="Scoring completed", result=result)
        except Exception as e:
            set_job(job_id, status="error", progress=100, message="Failed", error=str(e))

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.post("/api/export/filtered")
@login_required
def api_export_filtered():
    data = request.get_json(force=True, silent=True) or {}
    results = data.get("results") or []
    threshold = data.get("threshold")
    top_n = data.get("top_n")
    jd_json = data.get("jd_json") or {}

    if not results:
        return jsonify({"error": "No filtered results provided"}), 400

    STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(results)
    df_jd = pd.DataFrame([jd_json]) if isinstance(jd_json, dict) else pd.DataFrame()

    out_path = STORAGE_DIR / f"filtered_results_threshold_{threshold}_topN_{top_n}.xlsx"
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Filtered_Results")
        if not df_jd.empty:
            df_jd.to_excel(writer, index=False, sheet_name="JD")

    return send_file(out_path, as_attachment=True, download_name=out_path.name)


@app.get("/api/open-file")
@login_required
def open_file():
    import subprocess
    import platform

    raw_path = request.args.get('path', '')
    file_path = Path(raw_path).resolve()
    allowed_base = STORAGE_DIR.resolve()
    if not str(file_path).startswith(str(allowed_base)):
        return jsonify({"error": "Access denied"}), 403
    if not file_path.exists():
        return jsonify({"error": "File not found"}), 404

    try:
        if platform.system() == 'Windows':
            os.startfile(str(file_path))
        elif platform.system() == 'Darwin':
            subprocess.run(['open', str(file_path)])
        else:
            subprocess.run(['xdg-open', str(file_path)])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/")
def start_page():
    return render_template("index_login.html")

@app.get("/login")
def login():
    role = request.args.get("role", "employee")
    return render_template("login.html", role=role)


# Helper functions for resume management
def get_resume_version_info(emp_id, emp_name):
    """Get current resume version and list of all versions for an employee."""
    DATA_RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    resume_archive = STORAGE_DIR / "resume_archive"
    resume_archive.mkdir(parents=True, exist_ok=True)
    
    all_versions = []
    
    # Check in data/resumes for all supported formats
    for file in DATA_RESUMES_DIR.glob(f"{emp_id}_{emp_name}_v*.docx"):
        all_versions.append(file)
    for file in DATA_RESUMES_DIR.glob(f"{emp_id}_{emp_name}_v*.pdf"):
        all_versions.append(file)
    for file in DATA_RESUMES_DIR.glob(f"{emp_id}_{emp_name}_v*.pptx"):
        all_versions.append(file)
    
    # Check in archive for all supported formats
    for file in resume_archive.glob(f"{emp_id}_{emp_name}_v*.docx"):
        all_versions.append(file)
    for file in resume_archive.glob(f"{emp_id}_{emp_name}_v*.pdf"):
        all_versions.append(file)
    for file in resume_archive.glob(f"{emp_id}_{emp_name}_v*.pptx"):
        all_versions.append(file)
    
    if not all_versions:
        return None, 0, []
    
    # Sort by modification time to get current (most recent)
    all_versions.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    current_resume = None
    
    # Current resume should be in data/resumes, not in archive
    for file in all_versions:
        if str(file).startswith(str(DATA_RESUMES_DIR)):
            current_resume = file
            break
    
    total_versions = len(all_versions)
    
    return current_resume, total_versions, all_versions


def get_next_version_number(emp_id, emp_name):
    """Get the next version number for a resume."""
    current, total_versions, _ = get_resume_version_info(emp_id, emp_name)
    return total_versions + 1


def sanitize_name(name):
    """Remove spaces and special characters from name for filename."""
    return name.replace(" ", "_").replace("-", "_").lower()


@app.post("/api/upload-resume")
@login_required
def upload_resume():
    """Handle resume upload for logged-in employee."""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file provided"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400
        
        # Validate file extension
        allowed_ext = {'.pdf', '.docx', '.doc', '.pptx'}
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in allowed_ext:
            return jsonify({"error": "Only PDF, DOCX, DOC, and PPTX files are allowed"}), 400
        
        # Convert .doc to .docx
        if file_ext == '.doc':
            file_ext = '.docx'
        
        # Get current user info
        emp_id = session['user']['employee_id']
        emp_name = sanitize_name(session['user']['full_name'])
        
        # Ensure directories exist
        DATA_RESUMES_DIR.mkdir(parents=True, exist_ok=True)
        resume_archive = STORAGE_DIR / "resume_archive"
        resume_archive.mkdir(parents=True, exist_ok=True)
        
        # Get next version number
        next_version = get_next_version_number(emp_id, emp_name)
        
        # Create new filename
        new_filename = f"{emp_id}_{emp_name}_v{next_version}{file_ext}"
        new_filepath = DATA_RESUMES_DIR / new_filename
        
        # If this is not the first version, archive the previous version
        if next_version > 1:
            # Find current resume in data/resumes and move it to archive
            current_resume, _, all_versions = get_resume_version_info(emp_id, emp_name)
            if current_resume and str(current_resume).startswith(str(DATA_RESUMES_DIR)):
                archived_path = resume_archive / current_resume.name
                current_resume.rename(archived_path)
        
        # Save new resume
        file.save(str(new_filepath))
        
        return jsonify({
            "status": "ok",
            "message": f"Resume uploaded successfully as version {next_version}",
            "filename": new_filename,
            "version": next_version
        }), 201
    
    except Exception as e:
        return jsonify({"error": f"Upload failed: {str(e)}"}), 500


@app.get("/api/resume-info")
@login_required
def get_resume_info():
    """Get current resume info for logged-in employee."""
    try:
        emp_id = session['user']['employee_id']
        emp_name = sanitize_name(session['user']['full_name'])
        
        current_resume, total_versions, _ = get_resume_version_info(emp_id, emp_name)
        
        if current_resume is None:
            return jsonify({
                "status": "no_resume",
                "message": "No resume uploaded yet",
                "filename": None,
                "version": 0,
                "total_versions": 0
            }), 200
        
        # Extract version number from filename
        import re
        version_match = re.search(r'_v(\d+)', current_resume.name)
        current_version = int(version_match.group(1)) if version_match else 0
        
        return jsonify({
            "status": "ok",
            "filename": current_resume.name,
            "version": current_version,
            "total_versions": total_versions
        }), 200
    
    except Exception as e:
        return jsonify({"error": f"Failed to get resume info: {str(e)}"}), 500


@app.get("/api/download-resume")
@login_required
def download_resume():
    """Download current resume for logged-in employee."""
    try:
        emp_id = session['user']['employee_id']
        emp_name = sanitize_name(session['user']['full_name'])
        
        current_resume, _, _ = get_resume_version_info(emp_id, emp_name)
        
        if current_resume is None:
            return jsonify({"error": "No resume found"}), 404
        
        return send_file(
            str(current_resume),
            as_attachment=True,
            download_name=current_resume.name
        )
    
    except Exception as e:
        return jsonify({"error": f"Download failed: {str(e)}"}), 500

@app.get("/employee-portal")
@login_required
def employee_portal():
    return render_template("employee_portal.html")

@app.get("/opsteam")
@opsteam_required
def opsteam_tool():
    return render_template("index.html")   # your original opsteam screen


@app.route('/jd_matching')
@login_required
def jd_matching():
    return render_template('jd_matching.html')

@app.route('/employee_portal')
@login_required
def back():
    return render_template('employee_portal.html')

@app.get("/ops-dashboard")
@opsteam_required
def ops_dashboard():
    return render_template("ops_dashboard.html")

@app.get("/employee-details")
@login_required
def employee_details():
    users = [u for u in _load_users() if u.get("role") != "opsteam"]
    return render_template("employee_details.html", users=users)

@app.get("/employee-profile")
@login_required
def employee_profile():
    gg_id = request.args.get("gg_id", "").strip()
    user = _find_user_by_employee_id(gg_id) or {}
    return render_template("employee_profile.html", user=user)

@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)