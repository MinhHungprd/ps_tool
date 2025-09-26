import os
import time
import tempfile
import subprocess
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
        else {{ if (isSmartObjectLayer(ly)) {{ app.activeDocument.activeLayer = ly; replaceSmartObjectContents(newFile); return true; }} }}
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
    r"D:\DATA\Adobe\Adobe Photoshop 2022\Photoshop.exe"
]

def _find_photoshop_exe(user_path: Optional[str]) -> str:
    if user_path and os.path.isfile(user_path):
        return user_path
    for p in DEFAULT_PS_EXE:
        if os.path.isfile(p):
            return p
    from shutil import which
    w = which("Photoshop.exe")
    if w: return w
    raise FileNotFoundError("Không tìm thấy Photoshop.exe. Truyền ps_exe đầy đủ.")

def _wait_for(path: str, timeout: int = 240) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.isfile(path): return True
        time.sleep(0.5)
    return False

def _relink_and_export_single(psd_path: str, img_path: str, out_path: str,
                              ps_exe: Optional[str] = None, timeout: int = 240) -> bool:
    ps_exe_resolved = _find_photoshop_exe(ps_exe)
    psd_abs = os.path.abspath(psd_path).replace("\\", "/")
    img_abs = os.path.abspath(img_path).replace("\\", "/")
    out_abs = os.path.abspath(out_path).replace("\\", "/")
    os.makedirs(os.path.dirname(out_abs), exist_ok=True)

    jsx_code = JSX_TEMPLATE.format(psd=psd_abs, img=img_abs, out=out_abs)
    with tempfile.NamedTemporaryFile(prefix="ps_relink_", suffix=".jsx", delete=False) as tf:
        jsx_path = tf.name
    Path(jsx_path).write_text(jsx_code, encoding="utf-8")

    try:
        subprocess.Popen([ps_exe_resolved, "-r", jsx_path], close_fds=True)
    except Exception as e:
        raise RuntimeError(f"Launch Photoshop failed: {e}")

    ok = _wait_for(out_abs, timeout=timeout)

    try: os.remove(jsx_path)
    except Exception: pass

    return ok

def relink_and_export_single(psd_path: str, img_path: str, out_path: str,
                             ps_exe: Optional[str] = None, timeout: int = 240) -> bool:
    """API đơn lẻ—gọi trực tiếp cho 1 dòng."""
    return _relink_and_export_single(psd_path, img_path, out_path, ps_exe=ps_exe, timeout=timeout)

def relink_and_export_batch(jobs: List[Dict],
                            ps_exe: Optional[str] = None, timeout: int = 240) -> List[Dict]:
    """
    Batch mode cho route:
      - jobs: list[dict] từ order_processor, mỗi dict phải có psd_path/img_path/out_path
      - cập nhật trạng thái IN-PLACE và trả lại jobs
    """
    if not isinstance(jobs, list):
        raise TypeError("jobs phải là list[dict].")

    for i, job in enumerate(jobs):
        if not isinstance(job, dict):
            jobs[i] = {"status": "ERROR", "reason": "Job không phải dict"}
            continue

        if job.get("status") != "READY":
            # Bỏ qua SKIP/ERROR đã đánh dấu từ khâu chuẩn bị
            continue

        psd = job.get("psd_path")
        inp = job.get("img_path")
        outp = job.get("out_path")
        if not (psd and inp and outp):
            job["status"] = "ERROR"
            job["reason"] = "Thiếu psd_path/img_path/out_path"
            continue

        try:
            ok = _relink_and_export_single(psd, inp, outp, ps_exe=ps_exe, timeout=timeout)
            job["status"] = "OK" if ok else "ERROR"
            if ok:
                job["output"] = outp
            else:
                job["reason"] = "Export timed out or failed."
        except Exception as e:
            job["status"] = "ERROR"
            job["reason"] = str(e)
        finally:
            if job.get("tmp_img") and isinstance(inp, str) and os.path.isfile(inp):
                try: os.remove(inp)
                except Exception: pass

    return jobs