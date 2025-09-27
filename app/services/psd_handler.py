# app/services/psd_handler.py
import os, time, tempfile, subprocess
from pathlib import Path
from typing import Optional, List, Dict

JSX_TEMPLATE = r"""#target photoshop
app.displayDialogs = DialogModes.NO;
var psdPath = "{psd}";
var imgPath = "{img}";
var outPath = "{out}";
function replaceSmartObjectContents(newFile) {{
    var idplacedLayerReplaceContents = stringIDToTypeID("placedLayerReplaceContents");
    var desc = new ActionDescriptor();
    desc.putPath(charIDToTypeID("null"), new File(newFile));
    executeAction(idplacedLayerReplaceContents, desc, DialogModes.NO);
}}
function isSmartObjectLayer(ly) {{ try {{ return ly.kind == LayerKind.SMARTOBJECT; }} catch (e) {{ return false; }} }}
function walkAndRelinkFirstSO(container, newFile) {{
    for (var i=0;i<container.layers.length;i++) {{
        var ly = container.layers[i];
        if (ly.typename === "LayerSet") {{ var ok = walkAndRelinkFirstSO(ly,newFile); if (ok) return true; }}
        else {{ if (isSmartObjectLayer(ly)) {{ app.activeDocument.activeLayer = ly; 
            var idplacedLayerReplaceContents = stringIDToTypeID("placedLayerReplaceContents");
            var desc = new ActionDescriptor(); desc.putPath(charIDToTypeID("null"), new File(newFile));
            executeAction(idplacedLayerReplaceContents, desc, DialogModes.NO); return true; }} }}
    }}
    return false;
}}
function saveAsJPEG(doc, outPath) {{
    var f = new File(outPath);
    var opt = new JPEGSaveOptions(); opt.quality = 12; opt.embedColorProfile = true; opt.matte = MatteType.NONE;
    doc.saveAs(f, opt, true);
}}
var psdFile = new File(psdPath);
if (!psdFile.exists) {{ throw new Error("PSD not found: " + psdPath); }}
var doc = app.open(psdFile);
var ok = walkAndRelinkFirstSO(doc, imgPath);
if (!ok) {{ doc.close(SaveOptions.DONOTSAVECHANGES); throw new Error("No Smart Object layer found to relink."); }}
var dup = doc.duplicate(); dup.flatten(); saveAsJPEG(dup, outPath);
dup.close(SaveOptions.DONOTSAVECHANGES); doc.close(SaveOptions.DONOTSAVECHANGES);
"""

DEFAULT_PS_EXE = [
    r"D:\Adobe Photoshop 2022 v23.0.0.36 (x64) Multilingual\Adobe Photoshop 2022\Photoshop.exe",
]

def _find_photoshop_exe(user_path: Optional[str]) -> Optional[str]:
    if user_path and os.path.isfile(user_path): return user_path
    for p in DEFAULT_PS_EXE:
        if os.path.isfile(p): return p
    from shutil import which
    w = which("Photoshop.exe")
    return w if w and os.path.isfile(w) else None

def _wait_for(path: str, timeout: int = 240) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.isfile(path): return True
        time.sleep(0.5)
    return False

def _relink_and_export_single(ps_exe: str, psd_path: str, img_path: str, out_path: str, timeout: int = 240) -> bool:
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

    ok = _wait_for(out_abs, timeout=timeout)

    try: os.remove(jsx_path)
    except Exception: pass

    return ok

def relink_and_export_batch(items: List[Dict], ps_exe: Optional[str] = None, timeout: int = 240) -> List[Dict]:
    """
    Nhận list items từ order_processor (status=READY) và cố export.
    - Nếu KHÔNG tìm thấy Photoshop, KHÔNG gán ERROR: giữ nguyên READY và ghi chú.
    - Nếu export OK: status=OK, điền output_path (đã có sẵn).
    - Nếu lỗi: status=ERROR + error.
    """
    if not isinstance(items, list): return items

    exe = _find_photoshop_exe(ps_exe)
    if not exe:
        # Photoshop chưa sẵn sàng → không render, giữ READY
        for it in items:
            if it.get("status") == "READY":
                it.setdefault("error", None)
                if not it["error"]:
                    it["error"] = "Photoshop chưa cấu hình — bỏ qua bước render."
        return items

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

        try:
            ok = _relink_and_export_single(exe, psd, img, outp, timeout=timeout)
            if ok:
                it["status"] = "OK"
            else:
                it["status"] = "ERROR"
                it["error"]  = "Export time-out/failed."
        except Exception as e:
            it["status"] = "ERROR"
            it["error"]  = str(e)
        finally:
            if it.get("_tmp_img") and isinstance(img, str) and os.path.isfile(img):
                try: os.remove(img)
                except Exception: pass

    return items
