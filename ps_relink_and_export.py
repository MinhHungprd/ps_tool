# ps_relink_and_export.py — Run JSX via Photoshop "-r"
import json, argparse, os, time, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG_PATH = HERE / "ps_tool_config.json"
JSX_PATH = HERE / "process_mockup.jsx"

DEFAULT_CANDIDATES = [
    r"C:\Program Files\Adobe\Adobe Photoshop 2025\Photoshop.exe",
    r"C:\Program Files\Adobe\Adobe Photoshop 2024\Photoshop.exe",
    r"C:\Program Files\Adobe\Adobe Photoshop 2023\Photoshop.exe",
    r"C:\Program Files\Adobe\Adobe Photoshop\Photoshop.exe",
]

def find_photoshop_exe(candidates):
    for p in candidates:
        if os.path.isfile(p):
            return p
    # thử tìm qua PATH (nếu người dùng đã add)
    from shutil import which
    p = which("Photoshop.exe")
    return p

def run_photoshop_with_jsx(photoshop_exe, jsx_path):
    # quotes bắt buộc nếu có dấu cách
    cmd = [photoshop_exe, "-r", str(jsx_path)]
    try:
        subprocess.Popen(cmd, close_fds=True)
        return True
    except Exception as e:
        print("[ERR] Launch Photoshop failed:", e)
        return False

def wait_for_file(path, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.isfile(path):
            return True
        time.sleep(0.5)
    return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ps-exe", dest="ps_exe", default=None,
                    help='Đường dẫn Photoshop.exe (vd: "C:\\Program Files\\Adobe\\Adobe Photoshop 2024\\Photoshop.exe")')
    ap.add_argument("--psd", required=True)
    ap.add_argument("--img", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--design-layer", dest="design_layer", default=None)
    ap.add_argument("--text-layer", dest="text_layer", default=None)
    ap.add_argument("--text", dest="text_content", default=None)
    ap.add_argument("--fmt", default="jpg", choices=["jpg","png"])
    args = ap.parse_args()

    cfg = {
        "psd": os.path.abspath(args.psd).replace("\\", "/"),
        "image": os.path.abspath(args.img).replace("\\", "/"),
        "output": os.path.abspath(args.out).replace("\\", "/"),
        "designLayerName": args.design_layer,
        "textLayerName": args.text_layer,
        "textContent": args.text_content,
        "format": args.fmt.lower()
    }
    os.makedirs(os.path.dirname(cfg["output"]), exist_ok=True)
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    # xác định photoshop.exe
    photoshop_exe = args.ps_exe or find_photoshop_exe(DEFAULT_CANDIDATES)
    if not photoshop_exe or not os.path.isfile(photoshop_exe):
        raise FileNotFoundError(
            "Không tìm thấy Photoshop.exe. Truyền tham số --ps-exe với đường dẫn đầy đủ.\n"
            "Ví dụ: --ps-exe \"C:\\Program Files\\Adobe\\Adobe Photoshop 2024\\Photoshop.exe\""
        )

    if not run_photoshop_with_jsx(photoshop_exe, JSX_PATH):
        sys.exit(1)

    if not wait_for_file(cfg["output"], timeout=240):
        raise RuntimeError("Export failed, not found: " + cfg["output"])

    print("OK:", cfg["output"])

if __name__ == "__main__":
    import time
    main()
