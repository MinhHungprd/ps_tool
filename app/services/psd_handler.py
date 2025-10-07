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
TEXT_LAYER_NAME = os.getenv("TEXT_LAYER_NAME", "CODE VAT")     # tên layer chữ cần gán (mặc định: idsp)

# ========= Import drive_sync để kiểm tra trùng tên trên Drive =========
# from app.services import drive_sync as dsync

def _cleanup_flags(out_path: str) -> None:
    for flag in (out_path + ".ok", out_path + ".err"):
        try:
            if os.path.isfile(flag):
                os.remove(flag)
        except Exception:
            pass

# --- thêm vào đầu file ---
def _render_curly(template: str, mapping: dict) -> str:
    # thay thế {{ key }} -> value (không động vào {{...}} khác)
    out = template
    for k, v in mapping.items():
        out = out.replace(f"{{{{ {k} }}}}", str(v))
    return out

# ================== JSX để chạy trong Photoshop ==================
# ================== JSX để chạy trong Photoshop ==================
JSX_TEMPLATE = r"""#target photoshop
app.displayDialogs = DialogModes.NO;

// giảm overhead UI/History
try { app.preferences.numberOfHistoryStates = 2; } catch(e) {}
try { app.preferences.exportClipboard = false; } catch(e) {}

var psdPath = "{{ psd }}";
var imgPath = "{{ img }}";
var outPath = "{{ out }}";
var okFlag  = outPath + ".ok";
var errFlag = outPath + ".err";
var JPEG_QUALITY = {{ jpeg_quality }};
var textLayerName = "{{ text_layer }}"; // fast-path nếu đặt tên cố định

// ---------- Helpers ----------
function writeFlag(p, txt) {
    try {
        var f = new File(p);
        f.encoding = "UTF8";
        f.open("w"); f.write(txt); f.close();
    } catch(e) {}
}
function safeLog(msg) { try { $.writeln("[JSX] " + msg); } catch(e) {} }

function ensureParentFolder(pathStr) {
    try {
        var f = new File(pathStr);
        var folder = f.parent;
        if (!folder.exists) folder.create();
    } catch(e) {}
}
function normalizeForRaster(doc) {
    try { if (doc.bitsPerChannel == BitsPerChannelType.THIRTYTWO) doc.bitsPerChannel = BitsPerChannelType.SIXTEEN; } catch(e) {}
    try { if (doc.bitsPerChannel == BitsPerChannelType.SIXTEEN)  doc.bitsPerChannel = BitsPerChannelType.EIGHT; } catch(e) {}
    try {
        if (doc.mode != DocumentMode.RGB &&
            doc.mode != DocumentMode.GRAYSCALE &&
            doc.mode != DocumentMode.INDEXEDCOLOR) {
            doc.changeMode(ChangeMode.RGB);
        }
    } catch(e) {}
}
function saveJPEG_HQ(doc, outPath, quality) {
    ensureParentFolder(outPath);
    normalizeForRaster(doc);
    try { doc.flatten(); } catch(e) {}
    var f = new File(outPath);
    // 1) saveAs
    try{
        var o = new JPEGSaveOptions();
        o.quality = quality; // 0..12
        o.embedColorProfile = true;
        o.formatOptions = FormatOptions.STANDARDBASELINE;
        o.matte = MatteType.NONE;
        doc.saveAs(f, o, true, Extension.LOWERCASE);
    } catch(e1) {
        safeLog("saveAs JPEG failed: " + e1);
        // 2) fallback SaveForWeb
        try {
            var sfw = new ExportOptionsSaveForWeb();
            sfw.format = SaveDocumentType.JPEG;
            sfw.includeProfile = false;
            sfw.interlaced = false;
            sfw.optimized = true;
            sfw.quality = Math.min(100, quality * 8);
            doc.exportDocument(f, ExportType.SAVEFORWEB, sfw);
        } catch(e2) {
            throw new Error("Save failed both methods: " + e2);
        }
    }
}

function isSmartObjectLayer(ly) { try { return ly.kind == LayerKind.SMARTOBJECT; } catch(e) { return false; } }
function isTextLayer(ly)       { try { return (ly.typename === "ArtLayer" && ly.kind === LayerKind.TEXT); } catch(e){ return false; } }

function findFirstTextRec(container) {
    for (var i=0; i<container.layers.length; i++) {
        var ly = container.layers[i];
        if (ly.typename === "LayerSet") {
            var hit = findFirstTextRec(ly);
            if (hit) return hit;
        } else if (isTextLayer(ly)) {
            return ly;
        }
    }
    return null;
}
function getTextLayerFast(doc, name) {
    if (!name) return null;
    try { return doc.artLayers.getByName(name); } catch(e) {}
    try {
        var grp = doc.layerSets.getByName(name);
        for (var i=0;i<grp.layers.length;i++) {
            var ly = grp.layers[i];
            if (isTextLayer(ly)) return ly;
            if (ly.typename === "LayerSet") {
                var ly2 = findFirstTextRec(ly);
                if (ly2) return ly2;
            }
        }
    } catch(e) {}
    return null;
}
function enableUnicodeComposerIfSupported(textItem) {
    try {
        if (typeof TextComposer !== "undefined" && textItem) {
            if ("ADOBEEASTASIAN" in TextComposer) textItem.textComposer = TextComposer.ADOBEEASTASIAN;
            else if ("ADOBESINGLELINEEASTASIAN" in TextComposer) textItem.textComposer = TextComposer.ADOBESINGLELINEEASTASIAN;
            else if ("ADOBEEVERYLINEEASTASIAN" in TextComposer) textItem.textComposer = TextComposer.ADOBEEVERYLINEEASTASIAN;
        }
        try { textItem.useAutoKern = true; } catch(e) {}
    } catch(e) {}
}
function setTextFastOrFirst(doc, newText) {
    var target = getTextLayerFast(doc, textLayerName);
    if (!target) target = findFirstTextRec(doc);
    if (!target) return false;
    try { if (target.allLocked) target.allLocked = false; } catch(e) {}
    try {
        enableUnicodeComposerIfSupported(target.textItem);
        target.visible = true;
        target.textItem.contents = newText;
        return true;
    } catch(e) { return false; }
}

function rasterizeActiveLayer() {
    try {
        var idRst = stringIDToTypeID("rasterizeLayer");
        var desc2 = new ActionDescriptor();
        var ref = new ActionReference();
        ref.putEnumerated(charIDToTypeID("Lyr "), charIDToTypeID("Ordn"), charIDToTypeID("Trgt"));
        desc2.putReference(charIDToTypeID("null"), ref);
        executeAction(idRst, desc2, DialogModes.NO);
        return true;
    } catch(e) { safeLog("Rasterize failed: " + e); return false; }
}

function walkAndRelinkFirstSO(container, newFile) {
    for (var i=0; i<container.layers.length; i++) {
        var ly = container.layers[i];
        if (ly.typename === "LayerSet") {
            var ok = walkAndRelinkFirstSO(ly, newFile);
            if (ok) return true;
        } else {
            if (isSmartObjectLayer(ly)) {
                app.activeDocument.activeLayer = ly;
                try {
                    var idplacedLayerReplaceContents = stringIDToTypeID("placedLayerReplaceContents");
                    var desc = new ActionDescriptor();
                    desc.putPath(charIDToTypeID("null"), new File(newFile));
                    executeAction(idplacedLayerReplaceContents, desc, DialogModes.NO);
                } catch (e) {
                    safeLog("ReplaceContents failed → rasterizing: " + e);
                    rasterizeActiveLayer();
                }
                return true;
            }
        }
    }
    return false;
}

function basenameNoExt(p) {
    var f = new File(p);
    var n = f.name;
    var dot = n.lastIndexOf(".");
    if (dot > 0) return n.substring(0, dot);
    return n;
}

// ----------------- MAIN -----------------
function _main() {
    var doc = app.activeDocument;
    var ok = walkAndRelinkFirstSO(doc, imgPath);
    if (!ok) throw new Error("Không tìm thấy Smart Object layer để relink.");

    var outName = basenameNoExt(outPath);
    try { outName = decodeURIComponent(outName); } catch(e) {}
    setTextFastOrFirst(doc, outName);

    var dup = doc.duplicate();
    saveJPEG_HQ(dup, outPath, JPEG_QUALITY);
    dup.close(SaveOptions.DONOTSAVECHANGES);
    doc.close(SaveOptions.DONOTSAVECHANGES);
}

try {
    var psdFile = new File(psdPath);
    if (!psdFile.exists) throw new Error("PSD not found: " + psdPath);

    var doc = app.open(psdFile);
    try {
        if (doc && doc.suspendHistory) doc.suspendHistory("BatchRelinkExport", "_main()");
        else _main();
    } catch (e2) { _main(); }

    writeFlag(okFlag, "OK");
} catch(e) {
    writeFlag(errFlag, e.toString());
    throw e;
}
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

class _PhotoshopBridge:
    def __init__(self, ps_exe: Optional[str] = None, use_com: bool = USE_PS_COM):
        self.mode = "subprocess"
        self.exe = _find_photoshop_exe(ps_exe)
        self.app = None
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

        text_layer_name = (TEXT_LAYER_NAME or "").replace('"', '\\"')
        jsx_code = _render_curly(JSX_TEMPLATE, {
            "psd": psd_abs,
            "img": img_abs,
            "out": out_abs,
            "jpeg_quality": JPEG_QUALITY,
            "text_layer": text_layer_name,
        })

        if self.mode == "com":
            try:
                self.app.DoJavaScript(jsx_code)  # type: ignore
            except Exception as e:
                print(f"[PS_BRIDGE][COM] DoJavaScript error: {e}")
            ok, err = _wait_for_result(out_abs, timeout=timeout)
            for flag in (out_abs + ".ok", out_abs + ".err"):
                try:
                    if os.path.isfile(flag): os.remove(flag)
                except Exception:
                    pass
            _cleanup_flags(out_abs)
            return ok, err

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
        try: os.remove(jsx_path)
        except Exception: pass
        for flag in (out_abs + ".ok", out_abs + ".err"):
            try:
                if os.path.isfile(flag):
                    os.remove(flag)
            except Exception:
                pass
        _cleanup_flags(out_abs)
        return ok, err

def relink_and_export_batch(items: List[Dict], ps_exe: Optional[str] = None, timeout: int = BATCH_TIMEOUT) -> List[Dict]:
    """
    Batch chạy Photoshop.
    (ĐÃ BỎ phần kiểm tra "trùng tên trên Drive" để SKIP — theo yêu cầu vô hiệu hóa Drive)
    """
    if not isinstance(items, list):
        return items

    try:
        bridge = _PhotoshopBridge(ps_exe=ps_exe, use_com=USE_PS_COM)
    except Exception as e:
        for it in items:
            if it.get("status") == "READY":
                it.setdefault("error", None)
                if not it["error"]:
                    it["error"] = f"Photoshop chưa cấu hình — {e}"
        return items

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
            if it.get("_tmp_img") and isinstance(img, str) and os.path.isfile(img):
                try: os.remove(img)
                except Exception: pass

    return items