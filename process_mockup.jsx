/** process_mockup.jsx
 * Đọc cấu hình từ C:\ps_tool_config.json:
 * {
 *   "psd": "D:/SOURCE_CODE/ps_tool/storage/templates/BCNL-IN/Adult M/sda.psd",
 *   "image": "D:/SOURCE_CODE/ps_tool/storage/input/file design.jpg",
 *   "output": "D:/SOURCE_CODE/ps_tool/storage/outputs/result.jpg",
 *   "designLayerName": "design_layer",     // optional: ưu tiên layer này
 *   "textLayerName": "text_layer",         // optional
 *   "textContent": "BCNL-IN-Đen-Adult M-3805564513-1-3", // optional
 *   "format": "jpg" // "jpg" | "png"
 * }
 */

#target photoshop
app.displayDialogs = DialogModes.NO;

function readTextFile(path) { var f=new File(path); if(!f.exists) throw new Error("Config not found "+path);
  f.open("r"); var s=f.read(); f.close(); return s; }
var cfgPath = File($.fileName).parent + "/ps_tool_config.json";

function saveAsJPEG(doc, outPath) {
  var f = new File(outPath);
  var opt = new JPEGSaveOptions();
  opt.quality = 12;      // 0..12
  opt.embedColorProfile = true;
  opt.matte = MatteType.NONE;
  doc.saveAs(f, opt, true);
}

function saveAsPNG(doc, outPath) {
  var f = new File(outPath);
  var opt = new PNGSaveOptions();
  doc.saveAs(f, opt, true);
}

function findLayerByName(container, nameLower) {
  for (var i = 0; i < container.layers.length; i++) {
    var ly = container.layers[i];
    if (ly.name && ly.name.toString().toLowerCase() === nameLower) return ly;
    if (ly.typename === "LayerSet") {
      var r = findLayerByName(ly, nameLower);
      if (r) return r;
    }
  }
  return null;
}

function isSmartObjectLayer(ly) {
  try { return ly.kind == LayerKind.SMARTOBJECT; } catch (e) { return false; }
}

// Action: placedLayerReplaceContents(file)
function replaceSmartObjectContents(newFile) {
  var idplacedLayerReplaceContents = stringIDToTypeID("placedLayerReplaceContents");
  var desc = new ActionDescriptor();
  desc.putPath(charIDToTypeID("null"), new File(newFile));
  executeAction(idplacedLayerReplaceContents, desc, DialogModes.NO);
}

function relinkSmartObjectLayer(ly, newFile) {
  if (!isSmartObjectLayer(ly)) return false;
  // Chọn layer
  app.activeDocument.activeLayer = ly;
  // Thay nội dung (embedded/linked đều dùng được)
  replaceSmartObjectContents(newFile);
  return true;
}

function setTextLayerContent(ly, text) {
  try {
    if (ly.kind == LayerKind.TEXT) {
      ly.textItem.contents = text;
      return true;
    }
  } catch (e) {}
  return false;
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

(function main() {
  var cfgPath = File($.fileName).parent + "/ps_tool_config.json";

  var cfg = JSON.parse(readTextFile(cfgPath));

  var psdFile = new File(cfg.psd);
  if (!psdFile.exists) throw new Error("PSD not found: " + cfg.psd);
  var imgFile = new File(cfg.image);
  if (!imgFile.exists) throw new Error("Image not found: " + cfg.image);

  var doc = app.open(psdFile);

  // Ưu tiên relink theo tên layer nếu có
  var didRelink = false;
  if (cfg.designLayerName) {
    var target = findLayerByName(doc, cfg.designLayerName.toString().toLowerCase());
    if (target && isSmartObjectLayer(target)) {
      didRelink = relinkSmartObjectLayer(target, imgFile);
    }
  }
  // Nếu chưa relink được, lấy Smart Object đầu tiên
  if (!didRelink) {
    didRelink = walkAndRelinkFirstSO(doc, imgFile);
  }

  // Cập nhật text (tuỳ chọn)
  if (cfg.textContent) {
    var ok = false;
    if (cfg.textLayerName) {
      var tly = findLayerByName(doc, cfg.textLayerName.toString().toLowerCase());
      if (tly) ok = setTextLayerContent(tly, cfg.textContent.toString());
    }
    // nếu không có tên layer text, bỏ qua (hoặc có thể tìm layer text đầu tiên)
  }

  // Flatten nhẹ nhàng để export (không ghi đè PSD)
  // (tuỳ PSD; nếu cần giữ hiệu ứng, có thể export trực tiếp mà không flatten)
  var dup = doc.duplicate();
  dup.flatten();

  // Lưu
  var outPath = cfg.output;
  var fmt = (cfg.format || "jpg").toLowerCase();
  if (fmt === "png") saveAsPNG(dup, outPath);
  else saveAsJPEG(dup, outPath);

  dup.close(SaveOptions.DONOTSAVECHANGES);
  doc.close(SaveOptions.DONOTSAVECHANGES);
})();
