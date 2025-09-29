# app/services/psd_handler.py
import os, time, tempfile, subprocess
from pathlib import Path
from typing import Optional, List, Dict, Tuple

# ---- (Optional) load .env ----
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ---- Google Drive deps ----
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# Lấy CREDENTIALS_FILE và SCOPES từ app.config nếu có, không thì dùng default
try:
    from app.config import CREDENTIALS_FILE as _CF, SCOPES as _SC
    CREDENTIALS_FILE = _CF
    SCOPES = _SC
except Exception:
    CREDENTIALS_FILE = "app/config/credentials.json"
    # drive.file đủ để tạo/thao tác file do app tạo
    SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# ========= NEW: lấy tên thư mục Drive từ ENV (hoặc mặc định) =========
DRIVE_FOLDER_NAME_ENV = os.getenv("DRIVE_FOLDER_NAME", "save_file_in")  # <-- CHANGED

# ================== JSX để chạy trong Photoshop ==================
JSX_TEMPLATE = r"""#target photoshop
app.displayDialogs = DialogModes.NO;

var psdPath = "{psd}";
var imgPath = "{img}";
var outPath = "{out}";
var okFlag  = outPath + ".ok";
var errFlag = outPath + ".err";

// ---------- Helpers ----------
function replaceSmartObjectContents(newFile) {{
    var idplacedLayerReplaceContents = stringIDToTypeID("placedLayerReplaceContents");
    var desc = new ActionDescriptor();
    desc.putPath(charIDToTypeID("null"), new File(newFile));
    executeAction(idplacedLayerReplaceContents, desc, DialogModes.NO);
}}
function isSmartObjectLayer(ly) {{ try {{ return ly.kind == LayerKind.SMARTOBJECT; }} catch (e) {{ return false; }} }}
function walkAndRelinkFirstSO(container, newFile) {{
    for (var i=0; i<container.layers.length; i++) {{
        var ly = container.layers[i];
        if (ly.typename === "LayerSet") {{
            var ok = walkAndRelinkFirstSO(ly, newFile);
            if (ok) return true;
        }} else {{
            if (isSmartObjectLayer(ly)) {{
                app.activeDocument.activeLayer = ly;
                var idplacedLayerReplaceContents = stringIDToTypeID("placedLayerReplaceContents");
                var desc = new ActionDescriptor();
                desc.putPath(charIDToTypeID("null"), new File(newFile));
                executeAction(idplacedLayerReplaceContents, desc, DialogModes.NO);
                return true;
            }}
        }}
    }}
    return false;
}}

function isTextLayer(ly) {{
    try {{ return (ly.typename === "ArtLayer" && ly.kind === LayerKind.TEXT); }}
    catch (e) {{ return false; }}
}}
function enableUnicodeComposerIfSupported(textItem) {{
    try {{
        if (typeof TextComposer !== "undefined" && textItem) {{
            if ("ADOBEEASTASIAN" in TextComposer) textItem.textComposer = TextComposer.ADOBEEASTASIAN;
            else if ("ADOBESINGLELINEEASTASIAN" in TextComposer) textItem.textComposer = TextComposer.ADOBESINGLELINEEASTASIAN;
            else if ("ADOBEEVERYLINEEASTASIAN" in TextComposer) textItem.textComposer = TextComposer.ADOBEEVERYLINEEASTASIAN;
        }}
        try {{ textItem.useAutoKern = true; }} catch(e) {{}}
    }} catch(e) {{}}
}}
function setFirstTextLayer(container, newText) {{
    for (var i=0; i<container.layers.length; i++) {{
        var ly = container.layers[i];
        if (ly.typename === "LayerSet") {{
            if (setFirstTextLayer(ly, newText)) return true;
        }} else if (isTextLayer(ly)) {{
            try {{ if (ly.allLocked) ly.allLocked = false; }} catch(e) {{}}
            try {{
                ly.visible = true;
                enableUnicodeComposerIfSupported(ly.textItem);
                ly.textItem.contents = newText; // giữ Unicode đã decode
            }} catch(e) {{
                return false;
            }}
            return true;
        }}
    }}
    return false;
}}
function basenameNoExt(p) {{
    var f = new File(p);
    var n = f.name;
    var dot = n.lastIndexOf(".");
    if (dot > 0) return n.substring(0, dot);
    return n;
}}

// --- Save PNG via Save For Web (ổn định, tránh dialog) ---
function savePNG_SFW(doc, outPath) {{
    var f = new File(outPath);
    var opts = new ExportOptionsSaveForWeb();
    opts.format = SaveDocumentType.PNG;
    opts.PNG8 = false;              // PNG-24
    opts.transparency = true;       // giữ alpha nếu có
    opts.includeProfile = false;
    doc.exportDocument(f, ExportType.SAVEFORWEB, opts);
}}

function writeFlag(p, txt) {{
    try {{
        var fl = new File(p);
        fl.encoding = "UTF8";
        fl.open("w");
        fl.write(txt);
        fl.close();
    }} catch(e) {{}}
}}

// ----------------- MAIN with error flags -----------------
try {{
    var psdFile = new File(psdPath);
    if (!psdFile.exists) throw new Error("PSD not found: " + psdPath);
    var doc = app.open(psdFile);

    // Relink smart object đầu tiên
    var ok = walkAndRelinkFirstSO(doc, imgPath);
    if (!ok) {{
        doc.close(SaveOptions.DONOTSAVECHANGES);
        throw new Error("No Smart Object layer found to relink.");
    }}

    // Sửa text layer đầu tiên = decodeURIComponent(tên output không đuôi)
    var outName = basenameNoExt(outPath);
    try {{ outName = decodeURIComponent(outName); }} catch(e) {{}}
    setFirstTextLayer(doc, outName);

    // Export PNG
    var dup = doc.duplicate();
    // Nếu cần nền trong suốt: có thể comment dòng dưới
    dup.flatten();
    savePNG_SFW(dup, outPath);
    dup.close(SaveOptions.DONOTSAVECHANGES);
    doc.close(SaveOptions.DONOTSAVECHANGES);

    writeFlag(okFlag, "OK");
}} catch(e) {{
    writeFlag(errFlag, e.toString());
    throw e;
}}
"""

DEFAULT_PS_EXE = [
    r"D:\DATA\Adobe\Adobe Photoshop 2022\photoshop.exe",
]

def _find_photoshop_exe(user_path: Optional[str]) -> Optional[str]:
    if user_path and os.path.isfile(user_path):
        return user_path
    for p in DEFAULT_PS_EXE:
        if os.path.isfile(p):
            return p
    from shutil import which
    w = which("Photoshop.exe")
    return w if w and os.path.isfile(w) else None

def _wait_for_result(path: str, timeout: int = 240) -> Tuple[bool, Optional[str]]:
    """
    Chờ file output hoặc cờ .ok; nếu có .err thì trả lỗi ngay.
    """
    t0 = time.time()
    ok_flag = path + ".ok"
    err_flag = path + ".err"

    while time.time() - t0 < timeout:
        if os.path.isfile(err_flag):
            try:
                return False, Path(err_flag).read_text(encoding="utf-8", errors="ignore")
            except Exception:
                return False, "Unknown Photoshop error."
        if os.path.isfile(path) or os.path.isfile(ok_flag):
            return True, None
        time.sleep(0.5)

    # Hết giờ: nếu có .err thì đọc để trả về
    if os.path.isfile(err_flag):
        try:
            return False, Path(err_flag).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass
    return False, "Timeout waiting for export."

def _force_png_path(path: str) -> str:
    """Ép đuôi .png nếu chưa phải PNG."""
    p = Path(path)
    if p.suffix.lower() != ".png":
        p = p.with_suffix(".png")
    return str(p)

# ================== Google Drive helpers ==================

def _get_drive_service():
    """
    NOTE:
    - Dùng luồng OAuth local + lưu token.json.
    - Nếu bạn đã có refresh_token khác (web flow), bạn có thể hoán đổi hàm này
      sang đọc file token của web giống test_upload.py.
    """
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())
    return build("drive", "v3", credentials=creds)

def _ensure_drive_folder(service, folder_name: str) -> str:
    """Tìm folder theo tên, nếu chưa có thì tạo; trả về folder_id."""
    q = "name = '{0}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false".format(folder_name.replace("'", "\\'"))
    res = service.files().list(q=q, spaces="drive", fields="files(id,name)", pageSize=1).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    # create
    meta = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder"
    }
    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]

def _upload_file_to_folder(service, file_path: str, filename: str, folder_id: str) -> Dict[str, str]:
    media = MediaFileUpload(file_path, mimetype="image/png", resumable=False)
    body = {"name": filename, "parents": [folder_id]}
    file = service.files().create(body=body, media_body=media, fields="id,webViewLink").execute()
    return {"id": file.get("id"), "link": file.get("webViewLink")}

# ================== Photoshop relink + export ==================

def _relink_and_export_single(ps_exe: str, psd_path: str, img_path: str, out_path: str, timeout: int = 240) -> Tuple[bool, Optional[str]]:
    psd_abs = os.path.abspath(psd_path).replace("\\", "/")
    img_abs = os.path.abspath(img_path).replace("\\", "/")
    out_abs = os.path.abspath(out_path).replace("\\", "/")
    os.makedirs(os.path.dirname(out_abs), exist_ok=True)

    jsx_code = JSX_TEMPLATE.format(psd=psd_abs, img=img_abs, out=out_abs)
    with tempfile.NamedTemporaryFile(prefix="ps_relink_", suffix=".jsx", delete=False) as tf:
        jsx_path = tf.name
    Path(jsx_path).write_text(jsx_code, encoding="utf-8")

    try:
        subprocess.Popen([ps_exe, "-r", jsx_path], close_fds=True)
    except Exception as e:
        raise RuntimeError(f"Launch Photoshop failed: {e}")

    ok, err = _wait_for_result(out_abs, timeout=timeout)

    # dọn temp JSX + cờ
    try: os.remove(jsx_path)
    except Exception: pass
    for flag in (out_abs + ".ok", out_abs + ".err"):
        try:
            if os.path.isfile(flag):
                os.remove(flag)
        except Exception:
            pass

    return ok, err

# ================== Batch pipeline + upload Drive ==================

def relink_and_export_batch(items: List[Dict], ps_exe: Optional[str] = None, timeout: int = 240) -> List[Dict]:
    """
    Nhận list items từ order_processor (status=READY) và export PNG rồi upload lên Drive:
    - Photoshop OK → status=OK, đồng thời upload vào thư mục (ENV) DRIVE_FOLDER_NAME.
      Trả về it['drive_file_id'], it['drive_url'].
    - Nếu KHÔNG tìm thấy Photoshop → giữ READY, thêm error mô tả.
    - Nếu export lỗi → status=ERROR + error chi tiết.
    - Nếu upload Drive lỗi → status vẫn OK nhưng thêm it['drive_error'] để bạn biết.
    """
    if not isinstance(items, list):
        return items

    exe = _find_photoshop_exe(ps_exe)
    if not exe:
        for it in items:
            if it.get("status") == "READY":
                it.setdefault("error", None)
                if not it["error"]:
                    it["error"] = "Photoshop chưa cấu hình — bỏ qua bước render."
        return items

    # Chuẩn bị Drive service 1 lần (nếu có item cần upload)
    drive_service = None
    drive_folder_id = None

    for it in items:
        if it.get("status") != "READY":
            continue

        psd = it.get("_psd_path")
        img = it.get("_img_path")
        outp = it.get("output_path")

        if not (psd and img and outp):
            it["status"] = "ERROR"
            it["error"]  = "Thiếu đường dẫn psd/img/output."
            continue

        # Ép .png và cập nhật lại vào item
        outp_png = _force_png_path(outp)
        it["output_path"] = outp_png

        try:
            ok, err = _relink_and_export_single(exe, psd, img, outp_png, timeout=timeout)
            if ok:
                it["status"] = "OK"
                it["error"]  = None

                # ---- Upload lên Google Drive/{DRIVE_FOLDER_NAME_ENV} ----  <-- CHANGED
                try:
                    if drive_service is None:
                        drive_service = _get_drive_service()
                        drive_folder_id = _ensure_drive_folder(drive_service, DRIVE_FOLDER_NAME_ENV)

                    # Tên file trên Drive = đúng tên output hiện tại (basename)
                    filename = Path(outp_png).name  # <-- CHANGED (đúng yêu cầu)
                    up = _upload_file_to_folder(drive_service, outp_png, filename, drive_folder_id)
                    it["drive_file_id"] = up.get("id")
                    it["drive_url"] = up.get("link") or (f"https://drive.google.com/file/d/{up.get('id')}/view" if up.get("id") else None)
                except Exception as up_err:
                    it["drive_error"] = str(up_err)

            else:
                it["status"] = "ERROR"
                it["error"]  = err or "Export failed."
        except Exception as e:
            it["status"] = "ERROR"
            it["error"]  = str(e)
        finally:
            # Xoá ảnh tạm nếu có
            if it.get("_tmp_img") and isinstance(img, str) and os.path.isfile(img):
                try: os.remove(img)
                except Exception: pass

    return items
