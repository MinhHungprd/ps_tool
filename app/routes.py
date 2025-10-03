# app/routes.py
from flask import Blueprint, request, render_template, jsonify, abort, redirect, url_for, session, Response, stream_with_context
import os, shutil, secrets, json, time
from pathlib import Path
from datetime import datetime
# from google_auth_oauthlib.flow import Flow  # (VÔ HIỆU hóa Drive OAuth theo yêu cầu)

from app.auth import login_required

main = Blueprint('main', __name__)

# ===== Helpers =====
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

# ====== Lưu cấu hình thư mục đích LOCAL (server) ======
_LOCAL_DEST_FILE = Path("storage/local_dest.txt")
_LOCAL_DEST_FILE.parent.mkdir(parents=True, exist_ok=True)

def _get_local_dest() -> str:
    try:
        if _LOCAL_DEST_FILE.is_file():
            p = _LOCAL_DEST_FILE.read_text(encoding="utf-8").strip()
            if p:
                return p
    except Exception:
        pass
    return ""

def _set_local_dest(path_str: str) -> str:
    # Cho phép nhập tương đối/absolute -> lưu absolute chuẩn hóa
    p = Path(path_str).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    _LOCAL_DEST_FILE.write_text(str(p), encoding="utf-8")
    return str(p)

@main.route('/api/local-dest', methods=['GET', 'POST'])
@login_required
def api_local_dest():
    if request.method == 'GET':
        cur = _get_local_dest()
        return jsonify({"path": cur, "exists": bool(cur and Path(cur).exists())})
    data = request.get_json(silent=True) or {}
    raw = (data.get("path") or "").strip()
    if not raw:
        return jsonify({"error": "Chưa nhập đường dẫn thư mục."}), 400
    try:
        saved = _set_local_dest(raw)
        return jsonify({"ok": True, "path": saved})
    except Exception as e:
        return jsonify({"error": f"Không thể lưu thư mục: {e}"}), 400

# -------------------- (1) TRANG XỬ LÝ ĐƠN / --------------------
@main.route('/', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        excel_file = request.files.get('excel_file')
        if not excel_file:
            return jsonify({"error": "Thiếu file Excel (excel_file)"}), 400

        # Bắt buộc đã chọn thư mục local
        local_dest = _get_local_dest()
        if not local_dest:
            return jsonify({"error": "Chưa chọn thư mục lưu trữ local. Hãy chọn trước khi bắt đầu."}), 400

        # Lưu Excel tạm vẫn OK (không liên quan ảnh output)
        os.makedirs('storage', exist_ok=True)
        saved_path = os.path.join('storage', excel_file.filename)
        excel_file.save(saved_path)

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

        try:
            from app.services.order_processor import process_order_from_excel
        except Exception:
            process_order_from_excel = None
        try:
            from app.services.psd_handler import relink_and_export_batch
        except Exception:
            relink_and_export_batch = None

        # ❌ KHÔNG CÒN: from app.services.folder_sync import move_outputs_to_folder

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
            removed = 0
            job_tmp = set()
            for it in (job or []):
                p = it.get("_tmp_img")
                if isinstance(p, str) and p:
                    job_tmp.add(os.path.abspath(p))
            for p in list(job_tmp):
                removed += 1 if _safe_unlink(p) else 0
            try:
                abs_tmp = os.path.abspath(tmp_dir)
                if os.path.isdir(abs_tmp):
                    for name in os.listdir(abs_tmp):
                        full = os.path.abspath(os.path.join(abs_tmp, name))
                        if full in job_tmp:
                            continue
                        if os.path.isfile(full):
                            removed += 1 if _safe_unlink(full) else 0
            except Exception:
                pass
            return removed
        def _cleanup_flags_in_dir(dir_path: str) -> int:
            """
            Quét thư mục đích, xóa mọi file *.jpg.ok và *.jpg.err.
            Trả về số file đã xóa (best-effort).
            """
            removed = 0
            try:
                dp = Path(dir_path)
                if not dp.is_dir():
                    return 0
                for p in dp.iterdir():
                    name = p.name.lower()
                    if p.is_file() and (name.endswith(".jpg.ok") or name.endswith(".jpg.err")):
                        try:
                            p.unlink()
                            removed += 1
                        except Exception:
                            pass
            except Exception:
                pass
            return removed

        def _today_folder_name() -> str:
            return datetime.now().strftime("%d_%m_%y")

        def gen():
            yield (" " * 2048) + "\n"
            try:
                if not process_order_from_excel:
                    yield _jsonline({"type": "error", "message": "Thiếu process_order_from_excel"})
                    return

                # 1) Đọc Excel → job
                job = process_order_from_excel(saved_path, sheet=sheet, limit=limit)  # list[dict]
                total = len(job) if job else 0

                # 2) Xác định day_dir (local_dest/DD_MM_YY) và sửa output_path của từng item
                day_dir = Path(local_dest).expanduser().resolve() / _today_folder_name()
                day_dir.mkdir(parents=True, exist_ok=True)

                for it in (job or []):
                    # Lấy tên file (giữ nguyên tên đã build từ pipeline cũ), ép .jpg
                    orig = it.get("output_path") or ""
                    name = Path(orig).name if orig else ""
                    if not name:
                        # fallback: tự đặt tên nếu thiếu
                        order_id = str(it.get("order_id") or "unknown")
                        tmpl     = str(it.get("template") or "tpl")
                        name = f"{order_id}-{tmpl}.jpg"
                    else:
                        # ép đuôi .jpg
                        if not name.lower().endswith((".jpg", ".jpeg")):
                            name = Path(name).with_suffix(".jpg").name
                    it["output_path"] = str(day_dir / name)

                yield _jsonline({"type": "start", "total": total, "batch_size": batch_size, "report_every": report_every})

                if not job or not relink_and_export_batch:
                    yield _jsonline({"type": "done", "items": job or []})
                    return

                done = 0
                batch_count = 0
                pending_report_items = []

                # 3) Chạy từng batch: psd_handler sẽ GHI TRỰC TIẾP vào it["output_path"]
                for start in range(0, total, batch_size):
                    chunk = job[start:start + batch_size]
                    updated = relink_and_export_batch(chunk) or []
                    # Quét dọn flag lần 2 (phòng hờ dừng giữa chừng)
                    try:
                        _ = _cleanup_flags_in_dir(str(day_dir))
                    except Exception:
                        pass
                    # cập nhật lại job + progress
                    for i, it2 in enumerate(updated):
                        job_idx = start + i
                        if job_idx < len(job):
                            job[job_idx] = it2
                        pending_report_items.append(it2)

                    done += len(updated)
                    batch_count += 1

                    if batch_count % report_every == 0:
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

                    time.sleep(0.001)

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

                yield _jsonline({"type": "done", "items": job})
                try:
                    removed_flags = _cleanup_flags_in_dir(str(day_dir))
                    if removed_flags:
                        yield _jsonline({"type": "flag_cleanup", "removed": int(removed_flags)})
                except Exception:
                    pass


                # 4) Xóa ảnh tạm trong storage/tmp (nếu có) — KHÔNG ảnh output nào nằm ở storage nữa
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
        return Response(stream_with_context(gen()), headers=headers)

    return render_template('index.html', drive_connected=False, local_dest=_get_local_dest())


# -------------------- (2) TEMPLATE MANAGER (giữ nguyên) --------------------
TPL_ROOT = os.path.join('storage', 'templates')

@main.route('/templates', methods=['GET'])
@login_required
def template_manager():
    return render_template('folder_manager.html')

@main.route('/partial/<page>', methods=['GET'])
@login_required
def partial(page: str):
    mapping = {
        "orders": "partials/orders_partial.html",
        "templates": "partials/templates_partial.html",
    }
    tpl = mapping.get(page)
    if not tpl:
        abort(404)
    return render_template(tpl)

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

        if new_type_raw and not size_raw:
            new_type = _fs_sanitize(new_type_raw)
            new_type_path = os.path.join(TPL_ROOT, new_type)
            if os.path.exists(new_type_path):
                return jsonify({"error": "Tên loại mới đã tồn tại"}), 409
            os.rename(type_path, new_type_path)
            if not os.path.isdir(new_type_path):
                return jsonify({"error": "Đổi tên loại thất bại"}), 500
            return jsonify({"ok": True, "type": new_type})

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


# ==== GOOGLE DRIVE OAUTH — VÔ HIỆU HÓA (để nguyên code, nhưng comment toàn bộ) ====
# os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
# REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:5000/oauth2/callback")
# TOKEN_DIR = Path(os.environ.get("DRIVE_TOKEN_DIR", "storage/driveSession"))
# TOKEN_DIR.mkdir(parents=True, exist_ok=True)
# SCOPES = ["https://www.googleapis.com/auth/drive.file"]
# def _client_config(): ...
# def _sanitize_user(u: str) -> str: ...
# def _user_token_path(username: str) -> Path: ...
# def _load_user_tokens(username: str): ...
# def _save_user_tokens(username: str, creds): ...
# def _has_drive_connection(username: str) -> bool: ...
# @main.route("/connect-drive") ...
# @main.route("/oauth2/callback") ...
# @main.route("/disconnect-drive") ...
# (Phần này giữ nguyên trong file của bạn nếu muốn, chỉ cần bọc comment như trên)
