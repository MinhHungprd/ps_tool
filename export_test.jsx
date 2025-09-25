#target photoshop
app.displayDialogs = DialogModes.NO;

// === Đường dẫn chỉnh cho phù hợp ===
var psdPath = "D:/SOURCE_CODE/ps_tool/storage/templates/BCNL-IN/Adult M/sda.psd";
var imgPath = "D:/SOURCE_CODE/ps_tool/storage/input/file design.jpg";
var outPath = "D:/SOURCE_CODE/ps_tool/storage/outputs/test_export.jpg";

// ===== Helpers =====
function replaceSmartObjectContents(newFile) {
    var idplacedLayerReplaceContents = stringIDToTypeID("placedLayerReplaceContents");
    var desc = new ActionDescriptor();
    desc.putPath(charIDToTypeID("null"), new File(newFile));
    executeAction(idplacedLayerReplaceContents, desc, DialogModes.NO);
}

function isSmartObjectLayer(ly) {
    try { return ly.kind == LayerKind.SMARTOBJECT; } catch (e) { return false; }
}

function walkAndRelinkFirstSO(container, newFile) {
    for (var i = 0; i < container.layers.length; i++) {
        var ly = container.layers[i];
        if (ly.typename === "LayerSet") {
            var ok = walkAndRelinkFirstSO(ly, newFile);
            if (ok) return true;
        } else {
            if (isSmartObjectLayer(ly)) {
                app.activeDocument.activeLayer = ly;
                replaceSmartObjectContents(newFile);
                return true;
            }
        }
    }
    return false;
}

function saveAsJPEG(doc, outPath) {
    var f = new File(outPath);
    var opt = new JPEGSaveOptions();
    opt.quality = 12; // 0..12
    opt.embedColorProfile = true;
    opt.matte = MatteType.NONE;
    doc.saveAs(f, opt, true);
}

// ===== MAIN =====
var psdFile = new File(psdPath);
if (!psdFile.exists) { alert("PSD not found: " + psdPath); }
else {
    var doc = app.open(psdFile);
    // Relink Smart Object đầu tiên
    var ok = walkAndRelinkFirstSO(doc, imgPath);
    if (!ok) { alert("Không tìm thấy Smart Object để relink."); }

    // Flatten nhẹ nhàng để export
    var dup = doc.duplicate();
    dup.flatten();
    saveAsJPEG(dup, outPath);

    dup.close(SaveOptions.DONOTSAVECHANGES);
    doc.close(SaveOptions.DONOTSAVECHANGES);

    alert("Đã export: " + outPath);
}
