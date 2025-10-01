# app/routes.py
from flask import Blueprint, request, render_template, jsonify, abort, redirect, url_for, session
import os, shutil, secrets
from pathlib import Path
import json, time
from google_auth_oauthlib.flow import Flow

from app.auth import login_required

main = Blueprint('main', __name__)

# ===== Helpers tên an toàn =====
_WIN_FORBIDDEN = set('\\/:*?"<>|')
_RESERVED = {'CON','PRN','AUX','NUL', *(f'COM{i}' for i in range(1,10)), *(f'LPT{i}' for i in range(1,10))}
def _fs_sanitize(name: str) -> str:
    if name is None: raise ValueError("Tên rỗng.")
    s = name.strip()
    if not s: raise ValueError("Tên rỗng.")
    if '..' in s or '/' in s or '\\' in s:
        raise ValueError("Tên chứa ký tự đường dẫn không hợp lệ.")
    if any(ch in _WIN_FORBIDDEN for ch in s):
        raise ValueError('Tên chứa ký tự bị hệ thống tệp chặn (\\/:*?"<>|).')
    if any(ord(ch) < 32 for ch in s):
        raise ValueError("Tên chứa ký tự điều khiển.")
    if s.rstrip(' .').upper() in _RESERVED:
        raise ValueError("Tên không hợp lệ (tên dự phòng hệ thống).")
    s = s.rstrip(' .')
    if not s:
        raise ValueError("Tên không hợp lệ sau khi chuẩn hóa.")
    return s

# -------------------- (1) TRANG XỬ LÝ ĐƠN / --------------------
@main.route('/', methods=['GET', 'POST'])
@login_required
def index():
    """
    GET  -> render templates/index.html
    POST -> Stream NDJSON: xử lý theo batch_size, và cứ mỗi report_every batch thì xuất trạng thái.
    """
    if request.method == 'POST':
        excel_file = request.files.get('excel_file')
        if not excel_file:
            return jsonify({"error": "Thiếu file Excel (excel_file)"}), 400

        os.makedirs('storage', exist_ok=True)
        saved_path = os.path.join('storage', excel_file.filename)
        excel_file.save(saved_path)

        # Lấy form params
        sheet = request.form.get('sheet') or 'data'
        limit_raw = request.form.get('limit')
        limit = int(limit_raw) if (limit_raw and str(limit_raw).isdigit()) else None

        batch_raw = request.form.get('batch')
        try:
            batch_size = int(batch_raw) if batch_raw else 5
            if batch_size <= 0:
                batch_size = 5
        except Exception:
            batch_size = 5

        report_raw = request.form.get('report_every')
        try:
            report_every = int(report_raw) if report_raw else 1
            if report_every <= 0:
                report_every = 1
        except Exception:
            report_every = 1

        # Import deps
        try:
            from app.services.order_processor import process_order_from_excel
        except Exception:
            process_order_from_excel = None
        try:
            from app.services.psd_handler import relink_and_export_batch
        except Exception:
            relink_and_export_batch = None
        try:
            from app.services.drive_sync import sync_outputs_to_drive
        except Exception:
            sync_outputs_to_drive = None

        import json, time
        from flask import Response, stream_with_context

        def _jsonline(obj: dict) -> str:
            return json.dumps(obj, ensure_ascii=False) + "\n"
        
        def _safe_unlink(p: str) -> bool:
            try:
                if p and os.path.isfile(p):
                    os.remove(p)
                    return True
            except Exception:
                pass
            return False

        def _cleanup_tmp_leftovers(job: list, tmp_dir: str = "storage/tmp") -> int:
            """
            Xóa ảnh tạm còn sót:
            - Xóa mọi _tmp_img của các item trong job nếu file vẫn còn.
            - Xóa các file lẻ trong tmp_dir mà không thuộc _tmp_img của job.
            Trả về tổng số file đã xóa.
            """
            removed = 0
            # 1) gom danh sách tmp của job
            job_tmp = set()
            for it in (job or []):
                p = it.get("_tmp_img")
                if isinstance(p, str) and p:
                    job_tmp.add(os.path.abspath(p))

            # 2) xóa _tmp_img còn tồn tại
            for p in list(job_tmp):
                removed += 1 if _safe_unlink(p) else 0

            # 3) xóa file lẻ trong storage/tmp
            try:
                abs_tmp = os.path.abspath(tmp_dir)
                if os.path.isdir(abs_tmp):
                    for name in os.listdir(abs_tmp):
                        full = os.path.abspath(os.path.join(abs_tmp, name))
                        # bỏ qua nếu nằm trong job_tmp (đã xử lý ở trên)
                        if full in job_tmp:
                            continue
                        # chỉ xóa file, không đụng folder
                        if os.path.isfile(full):
                            removed += 1 if _safe_unlink(full) else 0
            except Exception:
                pass

            return removed

        def gen():
            # primer: buộc proxy/browser nhả stream ngay
            yield (" " * 2048) + "\n"
            try:
                if not process_order_from_excel:
                    yield _jsonline({"type": "error", "message": "Thiếu process_order_from_excel"})
                    return

                job = process_order_from_excel(saved_path, sheet=sheet, limit=limit)  # list[dict]
                total = len(job) if job else 0
                yield _jsonline({"type": "start", "total": total, "batch_size": batch_size, "report_every": report_every})

                if not job or not relink_and_export_batch:
                    yield _jsonline({"type": "done", "items": job or []})
                    return

                done = 0
                batch_count = 0
                pending_report_items = []   # gom kết quả các lô chờ report
                processed_since_last_upload = 0

                # Chia theo lô
                for start in range(0, total, batch_size):
                    chunk = job[start:start + batch_size]
                    updated = relink_and_export_batch(chunk) or []

                    # ghi kết quả ngược lại vào job + gom phần cần report
                    for i, it2 in enumerate(updated):
                        job_idx = start + i
                        if job_idx < len(job):
                            job[job_idx] = it2
                        pending_report_items.append(it2)

                    # cập nhật đếm
                    done += len(updated)
                    batch_count += 1
                    processed_since_last_upload += len(updated)

                    # sau mỗi lô: có thể upload ngay (tuỳ bạn, để true để an toàn)
                    if sync_outputs_to_drive:
                        try:
                            sync_outputs_to_drive(delete_local=True, user=session.get('user'))
                        except Exception as e:
                            yield _jsonline({"type":"upload_error","message":str(e)})

                    # khi đủ n lô, xuất trạng thái
                    if batch_count % report_every == 0:
                        yield _jsonline({
                            "type": "progress",
                            "done": done,
                            "total": total,
                            "batch_count": batch_count,
                            "last_batches": [  # gửi gọn những item của n lô vừa rồi
                                {
                                    "order_id": it.get("order_id"),
                                    "template": it.get("template"),
                                    "output_path": it.get("output_path"),
                                    "status": it.get("status"),
                                    "error": it.get("error"),
                                } for it in pending_report_items
                            ]
                        })
                        pending_report_items.clear()

                    # nhả nhịp nhỏ để flush
                    time.sleep(0.001)

                # report phần còn lại nếu chưa đủ n lô để report
                if pending_report_items:
                    yield _jsonline({
                        "type": "progress",
                        "done": done,
                        "total": total,
                        "batch_count": batch_count,
                        "last_batches": [
                            {
                                "order_id": it.get("order_id"),
                                "template": it.get("template"),
                                "output_path": it.get("output_path"),
                                "status": it.get("status"),
                                "error": it.get("error"),
                            } for it in pending_report_items
                        ]
                    })
                    pending_report_items.clear()

                # Kết thúc
                yield _jsonline({"type": "done", "items": job})
                
                try:
                    removed = _cleanup_tmp_leftovers(job, tmp_dir="storage/tmp")
                    yield _jsonline({"type": "tmp_cleanup", "removed": int(removed)})
                except Exception as e:
                    yield _jsonline({"type": "tmp_cleanup_error", "message": str(e)})

            except Exception as e:
                yield _jsonline({"type": "error", "message": str(e)})

        headers = {
            "Content-Type": "application/x-ndjson; charset=utf-8",
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
        return Response(stream_with_context(gen()), headers=headers)  # bỏ direct_passthrough

    # GET → render trang
    return render_template('index.html',drive_connected=(session.get("drive_connected") or _has_drive_connection(username=session.get('user'))) )


# -------------------- (2) TEMPLATE MANAGER --------------------
TPL_ROOT = os.path.join('storage', 'templates')

@main.route('/templates', methods=['GET'])
@login_required
def template_manager():
    return render_template('folder_manager.html')  # full page

# -------------------- (3) PARTIALS CHO SPA --------------------
@main.route('/partial/<page>', methods=['GET'])
@login_required
def partial(page: str):
    """
    Trả về CHỈ phần body HTML để client fetch() và chèn vào #main-content.
    """
    mapping = {
        "orders": "partials/orders_partial.html",
        "templates": "partials/templates_partial.html",
    }
    tpl = mapping.get(page)
    if not tpl:
        abort(404)
    return render_template(tpl)

# -------------------- (4) API QUẢN LÝ TEMPLATE --------------------
@main.route('/api/templates/tree', methods=['GET'])
@login_required
def template_tree():
    os.makedirs(TPL_ROOT, exist_ok=True)
    result = []
    for type_name in sorted(os.listdir(TPL_ROOT)):
        type_path = os.path.join(TPL_ROOT, type_name)
        if not os.path.isdir(type_path): continue
        sizes = []
        for size in sorted(os.listdir(type_path)):
            size_path = os.path.join(type_path, size)
            if not os.path.isdir(size_path): continue
            psd_path = os.path.join(size_path, 'sda.psd')
            if os.path.isfile(psd_path):
                sizes.append(size)
        result.append({"type": type_name, "sizes": sizes})
    return jsonify({"items": result})

@main.route('/api/templates/upload', methods=['POST'])
@login_required
def template_upload():
    try:
        file = request.files.get('file')
        type_name = _fs_sanitize(request.form.get('type'))
        size_name = _fs_sanitize(request.form.get('size'))
        overwrite = (request.form.get('overwrite', 'true') or 'true').lower() in ('1', 'true', 'yes', 'on')

        if not file or not file.filename.lower().endswith('.psd'):
            return jsonify({"error": "Vui lòng chọn file .psd hợp lệ"}), 400

        dst_dir = os.path.join(TPL_ROOT, type_name, size_name)
        os.makedirs(dst_dir, exist_ok=True)
        dst_psd = os.path.join(dst_dir, 'sda.psd')

        if os.path.exists(dst_psd) and not overwrite:
            return jsonify({"error": "Đã tồn tại. Bật overwrite để thay thế."}), 409

        file.save(dst_psd)
        if not os.path.isfile(dst_psd):
            return jsonify({"error": "Ghi file thất bại"}), 500

        return jsonify({"ok": True, "type": type_name, "size": size_name, "filename": "sda.psd"})
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception:
        return jsonify({"error": "Upload thất bại"}), 500

@main.route('/api/templates/delete', methods=['DELETE'])
@login_required
def template_delete():
    try:
        type_name = _fs_sanitize(request.args.get('type', ''))
        size_name_raw = request.args.get('size')

        type_path = os.path.join(TPL_ROOT, type_name)
        if not os.path.isdir(type_path):
            return jsonify({"ok": True})

        if size_name_raw:
            size_name = _fs_sanitize(size_name_raw)
            size_path = os.path.join(type_path, size_name)
            if os.path.isdir(size_path):
                shutil.rmtree(size_path, ignore_errors=True)
                if os.path.isdir(size_path):
                    return jsonify({"error": "Không thể xóa thư mục size (đang bị khóa?)"}), 409
            return jsonify({"ok": True, "deleted": {"type": type_name, "size": size_name}})
        else:
            shutil.rmtree(type_path, ignore_errors=True)
            if os.path.isdir(type_path):
                return jsonify({"error": "Không thể xóa thư mục loại (đang bị khóa?)"}), 409
            return jsonify({"ok": True, "deleted": {"type": type_name}})
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception:
        return jsonify({"error": "Xóa thất bại"}), 500

@main.route('/api/templates/rename', methods=['PATCH'])
@login_required
def template_rename():
    try:
        data = request.get_json(silent=True) or {}

        type_name = _fs_sanitize(data.get('type', ''))
        type_path = os.path.join(TPL_ROOT, type_name)
        if not os.path.isdir(type_path):
            return jsonify({"error": "Loại áo không tồn tại"}), 404

        new_type_raw = (data.get('new_type') or '').strip()
        size_raw     = (data.get('size') or '').strip()
        new_size_raw = (data.get('new_size') or '').strip()

        # Đổi tên loại
        if new_type_raw and not size_raw:
            new_type = _fs_sanitize(new_type_raw)
            new_type_path = os.path.join(TPL_ROOT, new_type)
            if os.path.exists(new_type_path):
                return jsonify({"error": "Tên loại mới đã tồn tại"}), 409
            os.rename(type_path, new_type_path)
            if not os.path.isdir(new_type_path):
                return jsonify({"error": "Đổi tên loại thất bại"}), 500
            return jsonify({"ok": True, "type": new_type})

        # Đổi tên size
        if size_raw and new_size_raw:
            size_name = _fs_sanitize(size_raw)
            new_size  = _fs_sanitize(new_size_raw)
            size_path = os.path.join(type_path, size_name)
            if not os.path.isdir(size_path):
                return jsonify({"error": "Size không tồn tại"}), 404
            new_size_path = os.path.join(type_path, new_size)
            if os.path.exists(new_size_path):
                return jsonify({"error": "Size mới đã tồn tại"}), 409
            os.rename(size_path, new_size_path)
            if not os.path.isdir(new_size_path):
                return jsonify({"error": "Đổi tên size thất bại"}), 500
            return jsonify({"ok": True, "type": type_name, "size": new_size})

        return jsonify({"error": "Payload không hợp lệ"}), 400
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception:
        return jsonify({"error": "Đổi tên thất bại"}), 500
    

# ==== GOOGLE DRIVE OAUTH (per-user) + LƯU TOKEN BỀN VỮNG ====
# Cho phép HTTP khi dev
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:5000/oauth2/callback")
TOKEN_DIR = Path(os.environ.get("DRIVE_TOKEN_DIR", "storage/driveSession"))
TOKEN_DIR.mkdir(parents=True, exist_ok=True)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

def _client_config():
    return {
        "web": {
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }

def _sanitize_user(u: str) -> str:
    return "".join(ch for ch in u if ch.isalnum() or ch in ("-", "_", ".")).strip() or "unknown"

def _user_token_path(username: str) -> Path:
    return TOKEN_DIR / f"drive_token_{_sanitize_user(username)}.json"

def _load_user_tokens(username: str):
    p = _user_token_path(username)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None

def _save_user_tokens(username: str, creds):
    p = _user_token_path(username)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "access_token": getattr(creds, "token", None),
        "refresh_token": getattr(creds, "refresh_token", None),
        "expiry": getattr(creds, "expiry", None).isoformat() if getattr(creds, "expiry", None) else None,
        "created_at": int(time.time()),
        "scope": getattr(creds, "scopes", None),
        # Lưu kèm client để drive_sync có thể rebuild Credentials
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _has_drive_connection(username: str) -> bool:
    tok = _load_user_tokens(username)
    return bool(tok and tok.get("refresh_token"))

@main.route("/connect-drive")
@login_required
def connect_drive():
    username = session.get("user")
    if not username:
        return redirect(url_for("auth.login", next=request.path))

    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = REDIRECT_URI

    # Gắn state gồm user + nonce để xác thực callback
    nonce = secrets.token_urlsafe(16)
    state = f"user={_sanitize_user(username)};nonce={nonce}"
    session["oauth_state"] = state

    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
        state=state,
    )
    print("[OAUTH] redirect_uri =", flow.redirect_uri)
    print("[OAUTH] auth_url     =", authorization_url)
    return redirect(authorization_url)

@main.route("/oauth2/callback")
@login_required
def oauth2_callback():
    username = session.get("user")
    if not username:
        return redirect(url_for("auth.login"))

    try:
        # Xác thực state
        state_client = request.args.get("state") or ""
        state_session = session.get("oauth_state") or ""
        if not state_client or state_client != state_session:
            return "OAuth state mismatch.", 400

        flow = Flow.from_client_config(_client_config(), scopes=SCOPES, state=state_client)
        flow.redirect_uri = REDIRECT_URI
        flow.fetch_token(authorization_response=request.url)

        creds = flow.credentials
        _save_user_tokens(username, creds)
        session["drive_connected"] = True  # chỉ để hiển thị UI

        print(f"[OAUTH] connected for user={username}; has_refresh_token:",
              bool(getattr(creds, "refresh_token", None)))
        return redirect(url_for("main.index"))
    except Exception as e:
        import traceback; traceback.print_exc()
        return "OAuth callback error: " + str(e), 500

@main.route("/disconnect-drive")
@login_required
def disconnect_drive():
    username = session.get("user")
    try:
        p = _user_token_path(username) if username else None
        if p and p.exists():
            p.unlink()
    except Exception:
        pass
    session.pop("drive_connected", None)
    return redirect(url_for("main.index"))



