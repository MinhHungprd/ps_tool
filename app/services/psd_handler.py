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

# ---- Google Drive deps (giữ nguyên nếu chưa dùng) ----
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
    SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# ========= ENV cấu hình =========
DRIVE_FOLDER_NAME_ENV = os.getenv("DRIVE_FOLDER_NAME", "save_file_in")
USE_PS_COM = os.getenv("USE_PS_COM", "0") == "1"      # 1: dùng COM nếu có; 0: subprocess
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "12"))  # 0-12
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "0.25"))  # giây
SINGLE_TIMEOUT = int(os.getenv("SINGLE_TIMEOUT", "540"))   # giây cho 1 output
BATCH_TIMEOUT = int(os.getenv("BATCH_TIMEOUT", "1040"))    # giây cho batch

# ========= Import drive_sync để kiểm tra trùng tên trên Drive =========
from app.services import drive_sync as dsync

# ================== JSX để chạy trong Photoshop ==================
JSX_TEMPLATE = r"""#target photoshop
app.displayDialogs = DialogModes.NO;

var psdPath = "{psd}";
var imgPath = "{img}";
var outPath = "{out}";
var okFlag  = outPath + ".ok";
var errFlag = outPath + ".err";
var JPEG_QUALITY = {jpeg_quality};

// ---------- Helpers ----------
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
                ly.textItem.contents = newText;
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

function ensureParentFolder(pathStr) {{
    try {{
        var f = new File(pathStr);
        var folder = f.parent;
        if (!folder.exists) folder.create();
    }} catch (e) {{}}
}}

function normalizeForRaster(doc) {{
    try {{
        if (doc.bitsPerChannel && doc.bitsPerChannel == BitsPerChannelType.THIRTYTWO) {{
            doc.bitsPerChannel = BitsPerChannelType.EIGHT;
        }}
    }} catch (e) {{}}
    try {{
        if (doc.mode != DocumentMode.RGB && doc.mode != DocumentMode.GRAYSCALE && doc.mode != DocumentMode.INDEXEDCOLOR) {{
            doc.changeMode(ChangeMode.RGB);
        }}
    }} catch (e) {{}}
}}

function saveJPEG_HQ(doc, outPath, quality) {{
    ensureParentFolder(outPath);
    normalizeForRaster(doc);
    try {{ doc.flatten(); }} catch (e) {{}}

    var f = new File(outPath);
    var opts = new JPEGSaveOptions();
    opts.quality = quality; // 0–12
    opts.embedColorProfile = true;
    opts.formatOptions = FormatOptions.STANDARDBASELINE;
    opts.matte = MatteType.NONE;
    doc.saveAs(f, opts, true, Extension.LOWERCASE);
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

    var ok = walkAndRelinkFirstSO(doc, imgPath);
    if (!ok) {{
        doc.close(SaveOptions.DONOTSAVECHANGES);
        throw new Error("No Smart Object layer found to relink.");
    }}

    var outName = basenameNoExt(outPath);
    try {{ outName = decodeURIComponent(outName); }} catch(e) {{}}
    setFirstTextLayer(doc, outName);

    var dup = doc.duplicate();
    saveJPEG_HQ(dup, outPath, JPEG_QUALITY);

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

def _wait_for_result(path: str, timeout: int = SINGLE_TIMEOUT) -> Tuple[bool, Optional[str]]:
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
        time.sleep(POLL_INTERVAL)

    if os.path.isfile(err_flag):
        try:
            return False, Path(err_flag).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass
    return False, "Timeout waiting for export."

def _force_jpg_path(path: str) -> str:
    p = Path(path)
    if p.suffix.lower() not in [".jpg", ".jpeg"]:
        p = p.with_suffix(".jpg")
    return str(p)

# ================== Photoshop Bridge (COM / Subprocess) ==================

class _PhotoshopBridge:
    """
    Bridge 2 chế độ:
    - COM (win32com) nếu USE_PS_COM=1 và có Photoshop COM.
    - Subprocess photoshop.exe -r JSX nếu không có COM.
    """
    def __init__(self, ps_exe: Optional[str] = None, use_com: bool = USE_PS_COM):
        self.mode = "subprocess"
        self.exe = _find_photoshop_exe(ps_exe)
        self.app = None  # COM app
        if use_com:
            try:
                import win32com.client  # type: ignore
                self.app = win32com.client.Dispatch('Photoshop.Application')
                self.mode = "com"
            except Exception as e:
                print(f"[PS_BRIDGE] COM init failed, fallback to subprocess. Reason: {e}")
                self.mode = "subprocess"

        if self.mode == "subprocess" and not self.exe:
            raise RuntimeError("Photoshop executable not found.")

    def relink_and_export(self, psd_path: str, img_path: str, out_path: str, timeout: int = SINGLE_TIMEOUT) -> Tuple[bool, Optional[str]]:
        psd_abs = os.path.abspath(psd_path).replace("\\", "/")
        img_abs = os.path.abspath(img_path).replace("\\", "/")
        out_abs = os.path.abspath(out_path).replace("\\", "/")
        os.makedirs(os.path.dirname(out_abs), exist_ok=True)

        jsx_code = JSX_TEMPLATE.format(psd=psd_abs, img=img_abs, out=out_abs, jpeg_quality=JPEG_QUALITY)

        if self.mode == "com":
            try:
                self.app.DoJavaScript(jsx_code)  # type: ignore
            except Exception as e:
                # vẫn đợi cờ .err để lấy message chi tiết
                print(f"[PS_BRIDGE][COM] DoJavaScript error: {e}")
            ok, err = _wait_for_result(out_abs, timeout=timeout)
            # dọn cờ
            for flag in (out_abs + ".ok", out_abs + ".err"):
                try:
                    if os.path.isfile(flag): os.remove(flag)
                except Exception:
                    pass
            return ok, err

        # subprocess fallback
        with tempfile.NamedTemporaryFile(prefix="ps_relink_", suffix=".jsx", delete=False) as tf:
            jsx_path = tf.name
        Path(jsx_path).write_text(jsx_code, encoding="utf-8")

        try:
            subprocess.Popen([self.exe, "-r", jsx_path], close_fds=True)
        except Exception as e:
            try: os.remove(jsx_path)
            except Exception: pass
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

# ================== Batch pipeline ==================

def relink_and_export_batch(items: List[Dict], ps_exe: Optional[str] = None, timeout: int = BATCH_TIMEOUT) -> List[Dict]:
    """
    Nhận list items từ order_processor (status=READY):
    - Nếu file đích đã có sẵn trên Google Drive (thư mục ngày) -> SKIPPED (kiểm tra THEO FILE như cũ).
    - Photoshop OK → status=OK.
    - Nếu KHÔNG tìm thấy Photoshop → giữ READY, thêm error mô tả.
    - Nếu export lỗi → status=ERROR + error chi tiết.
    """
    if not isinstance(items, list):
        return items

    # Khởi Photoshop bridge 1 lần cho cả batch (giảm overhead khởi động)
    try:
        bridge = _PhotoshopBridge(ps_exe=ps_exe, use_com=USE_PS_COM)
    except Exception as e:
        for it in items:
            if it.get("status") == "READY":
                it.setdefault("error", None)
                if not it["error"]:
                    it["error"] = f"Photoshop chưa cấu hình — {e}"
        return items

    # Cho phép bật/tắt skip nếu đã có trên Drive (ENV: SKIP_IF_EXISTS_ON_DRIVE=0 để tắt)
    SKIP_IF_EXISTS_ON_DRIVE = os.environ.get("SKIP_IF_EXISTS_ON_DRIVE", "1") != "0"

    # Chuẩn bị context Drive (service + folder ngày) MỘT LẦN (nhưng CHECK TỪNG FILE như cũ)
    drive_ctx = None  # (service, day_id)
    if SKIP_IF_EXISTS_ON_DRIVE:
        try:
            service = dsync._get_drive_service()
            root_id = dsync._ensure_drive_folder(service, getattr(dsync, "DRIVE_FOLDER_NAME", DRIVE_FOLDER_NAME_ENV))
            day_id  = dsync._ensure_child_folder(service, root_id, dsync._today_folder_name())
            drive_ctx = (service, day_id)
        except Exception as e:
            print(f"[BATCH][WARN] Drive check disabled (reason: {e})")
            drive_ctx = None

    t_end = time.time() + timeout

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

        outp_jpg = _force_jpg_path(outp)
        it["output_path"] = outp_jpg

        # BỎ QUA nếu file trùng tên đã tồn tại trên Drive (THƯ MỤC NGÀY) — kiểm tra TỪNG FILE như cũ
        if drive_ctx is not None:
            try:
                service, day_id = drive_ctx
                filename = Path(outp_jpg).name
                existing_id = dsync._file_exists_in_folder(service, day_id, filename)
                if existing_id:
                    it["status"] = "SKIPPED"
                    it["error"]  = f"Exists on Drive (file_id={existing_id})"
                    continue
            except Exception as e:
                # Nếu check lỗi, cảnh báo rồi vẫn render bình thường
                print(f"[BATCH][WARN] Drive exists check failed for {outp_jpg}: {e}")

        # Kiểm tra timeout tổng (batch)
        if time.time() > t_end:
            it["status"] = "ERROR"
            it["error"]  = "Batch timeout."
            continue

        try:
            ok, err = bridge.relink_and_export(psd, img, outp_jpg, timeout=SINGLE_TIMEOUT)
            if ok:
                it["status"] = "OK"
                it["error"]  = None
            else:
                it["status"] = "ERROR"
                it["error"]  = err or "Export failed."
        except Exception as e:
            it["status"] = "ERROR"
            it["error"]  = str(e)
        finally:
            # Dọn ảnh tạm nếu có
            if it.get("_tmp_img") and isinstance(img, str) and os.path.isfile(img):
                try: os.remove(img)
                except Exception: pass

    return items
