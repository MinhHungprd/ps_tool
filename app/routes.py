# app/routes.py
from flask import Blueprint, request, render_template, jsonify, abort
import os, shutil

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
    GET  -> render templates/index.html (full page lần đầu)
    POST -> GIỮ NGUYÊN LOGIC CŨ
    """
    if request.method == 'POST':
        excel_file = request.files.get('excel_file')
        if not excel_file:
            return jsonify({"error": "Thiếu file Excel (excel_file)"}), 400

        os.makedirs('storage', exist_ok=True)
        saved_path = os.path.join('storage', excel_file.filename)
        excel_file.save(saved_path)

        payload = []
        try:
            try:
                from app.services.order_processor import process_order_from_excel
            except Exception:
                process_order_from_excel = None
            try:
                from app.services.psd_handler import relink_and_export_batch
            except Exception:
                relink_and_export_batch = None

            sheet = request.form.get('sheet') or 'data'
            limit_raw = request.form.get('limit')
            limit = int(limit_raw) if (limit_raw and limit_raw.isdigit()) else None

            if process_order_from_excel:
                job = process_order_from_excel(saved_path, sheet=sheet, limit=limit)
                if relink_and_export_batch:
                    relink_and_export_batch(job)
                payload = job
            else:
                payload = []
        except Exception as e:
            return jsonify({"error": str(e)}), 500

        return jsonify(payload)

    return render_template('index.html')   # full page

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
