# ps_relink_and_export.py
import argparse, os, time, tempfile, subprocess, sys
from pathlib import Path

JSX_TEMPLATE = r"""#target photoshop
app.displayDialogs = DialogModes.NO;

// === Đường dẫn tự động điền từ Python ===
var psdPath = "{psd}";
var imgPath = "{img}";
var outPath = "{out}";

// ===== Helpers =====
function replaceSmartObjectContents(newFile) {{
    var idplacedLayerReplaceContents = stringIDToTypeID("placedLayerReplaceContents");
    var desc = new ActionDescriptor();
    desc.putPath(charIDToTypeID("null"), new File(newFile));
    executeAction(idplacedLayerReplaceContents, desc, DialogModes.NO);
}}
function isSmartObjectLayer(ly) {{
    try {{ return ly.kind == LayerKind.SMARTOBJECT; }} catch (e) {{ return false; }}
}}
function walkAndRelinkFirstSO(container, newFile) {{
    for (var i = 0; i < container.layers.length; i++) {{
        var ly = container.layers[i];
        if (ly.typename === "LayerSet") {{
            var ok = walkAndRelinkFirstSO(ly, newFile);
            if (ok) return true;
        }} else {{
            if (isSmartObjectLayer(ly)) {{
                app.activeDocument.activeLayer = ly;
                replaceSmartObjectContents(newFile);
                return true;
            }}
        }}
    }}
    return false;
}}
function saveAsJPEG(doc, outPath) {{
    var f = new File(outPath);
    var opt = new JPEGSaveOptions();
    opt.quality = 12; // 0..12
    opt.embedColorProfile = true;
    opt.matte = MatteType.NONE;
    doc.saveAs(f, opt, true);
}}

// ===== MAIN =====
var psdFile = new File(psdPath);
if (!psdFile.exists) {{ alert("PSD not found: " + psdPath); }}
else {{
    var doc = app.open(psdFile);
    // Relink Smart Object đầu tiên
    var ok = walkAndRelinkFirstSO(doc, imgPath);
    if (!ok) {{ alert("Không tìm thấy Smart Object để relink."); }}

    // Flatten và export
    var dup = doc.duplicate();
    dup.flatten();
    saveAsJPEG(dup, outPath);

    dup.close(SaveOptions.DONOTSAVECHANGES);
    doc.close(SaveOptions.DONOTSAVECHANGES);
}}
"""

DEFAULT_PS_EXE = [
    r"D:\Adobe Photoshop 2022 v23.0.0.36 (x64) Multilingual\Adobe Photoshop 2022\Photoshop.exe",
]

def find_photoshop_exe(user_path: str | None) -> str:
    if user_path and os.path.isfile(user_path):
        return user_path
    for p in DEFAULT_PS_EXE:
        if os.path.isfile(p):
            return p
    from shutil import which
    w = which("Photoshop.exe")
    if w:
        return w
    raise FileNotFoundError("Không tìm thấy Photoshop.exe. Truyền --ps-exe với đường dẫn đầy đủ.")

def wait_for(path: str, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.isfile(path):
            return True
        time.sleep(0.5)
    return False

def main():
    ap = argparse.ArgumentParser(description="Relink SmartObject đầu tiên và export JPG (1 ảnh).")
    ap.add_argument("--ps-exe", required=False, help="Đường dẫn Photoshop.exe (vd: D:\\DATA\\Adobe\\Adobe Photoshop 2022\\Photoshop.exe)")
    ap.add_argument("--psd", required=True, help="Đường dẫn PSD template")
    ap.add_argument("--img", required=True, help="Đường dẫn ảnh đầu vào")
    ap.add_argument("--out", required=True, help="Đường dẫn file JPG đầu ra")
    args = ap.parse_args()

    ps_exe = find_photoshop_exe(args.ps_exe)

    psd = os.path.abspath(args.psd).replace("\\", "/")
    img = os.path.abspath(args.img).replace("\\", "/")
    outp = os.path.abspath(args.out).replace("\\", "/")

    os.makedirs(os.path.dirname(outp), exist_ok=True)

    # Tạo JSX tạm với đường dẫn đã nhúng
    jsx_code = JSX_TEMPLATE.format(psd=psd, img=img, out=outp)
    with tempfile.NamedTemporaryFile(prefix="ps_relink_", suffix=".jsx", delete=False) as tf:
        jsx_path = tf.name
    Path(jsx_path).write_text(jsx_code, encoding="utf-8")

    # Chạy Photoshop -r JSX
    cmd = [ps_exe, "-r", jsx_path]
    try:
        subprocess.Popen(cmd, close_fds=True)
    except Exception as e:
        print("[ERR] Launch Photoshop failed:", e)
        sys.exit(1)

    # Chờ file output
    if not wait_for(outp, timeout=240):
        print("[ERR] Không thấy file output:", outp)
        sys.exit(2)

    print("OK:", outp)
    # Dọn file JSX tạm (giữ lại nếu bạn muốn debug)
    try:
        os.remove(jsx_path)
    except Exception:
        pass

if __name__ == "__main__":
    main()
