import AppKit
import CoreText

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8)); exit(1)
}
let args = CommandLine.arguments
guard args.count == 3,
      let data = try? Data(contentsOf: URL(fileURLWithPath: args[1])),
      let info = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { fail("无法读取封面配置") }
let pixelsWide = (info["width"] as? Int) ?? 1920
let pixelsHigh = (info["height"] as? Int) ?? 1080
guard [(1920, 1080), (1440, 1080), (1080, 1440)].contains(where: { $0.0 == pixelsWide && $0.1 == pixelsHigh }) else { fail("封面仅支持 16:9、4:3 和 3:4") }
let canvasWidth = CGFloat(pixelsWide), canvasHeight = CGFloat(pixelsHigh)
let horizontalScale = canvasWidth/1920, verticalScale = canvasHeight/1080
let textWidth = 1744*horizontalScale
guard let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixelsWide, pixelsHigh: pixelsHigh,
                                   bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                   isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
      let context = NSGraphicsContext(bitmapImageRep: bitmap) else { fail("无法创建封面画布") }
func value(_ key: String) -> String {
    guard let text = info[key] as? String else { fail("封面缺少字段：\(key)") }; return text
}
func teamColor(_ key: String) -> NSColor {
    let hex = value(key)
    guard hex.count == 7, hex.first == "#", let rgb = UInt32(hex.dropFirst(), radix: 16) else {
        fail("无效球队颜色：\(hex)")
    }
    return NSColor(srgbRed: CGFloat((rgb >> 16) & 255)/255,
                   green: CGFloat((rgb >> 8) & 255)/255, blue: CGFloat(rgb & 255)/255, alpha: 1)
}
NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current = context
func panel(_ side: String, points: [NSPoint], x: CGFloat, top: CGFloat, width: CGFloat, height: CGFloat) {
    let path = NSBezierPath(); path.move(to: points[0])
    for point in points.dropFirst() { path.line(to: point) }
    path.close()
    NSGraphicsContext.saveGraphicsState(); path.addClip()
    let team = teamColor(side + "_color")
    NSGradient(colorsAndLocations: (team.blended(withFraction: 0.88, of: .black)!, 0),
               (team, 0.7), (team.blended(withFraction: 0.45, of: .black)!, 1))!
        .draw(in: NSRect(x: 0, y: 0, width: canvasWidth, height: canvasHeight), angle: 90)
    let font = NSFont(name: "HelveticaNeue-CondensedBlack", size: 400) ?? NSFont.boldSystemFont(ofSize: 400)
    let attributes: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: NSColor.white.withAlphaComponent(0.07)]
    let line = CTLineCreateWithAttributedString(NSAttributedString(string: value(side + "_code"), attributes: attributes))
    let bounds = CTLineGetImageBounds(line, nil)
    guard bounds.width > 0, bounds.height > 0 else { fail("无法测量球队缩写字形") }
    // 用可见字形边界定位，避免字体自身的行高和留白改变背景字位置。
    let scaleX = width/bounds.width, scaleY = height/bounds.height
    let cg = context.cgContext
    cg.saveGState()
    cg.translateBy(x: x-bounds.minX*scaleX, y: canvasHeight-top-height-bounds.minY*scaleY)
    cg.scaleBy(x: scaleX, y: scaleY)
    cg.textMatrix = .identity; cg.textPosition = .zero
    CTLineDraw(line, cg)
    cg.restoreGState()
    NSGraphicsContext.restoreGraphicsState()
}
// 先铺满客队底色，避免两块裁剪边缘留下透明细缝。
panel("away", points: [NSPoint(x: 0, y: 0), NSPoint(x: canvasWidth, y: 0), NSPoint(x: canvasWidth, y: canvasHeight), NSPoint(x: 0, y: canvasHeight)], x: 0, top: 0, width: 660*horizontalScale, height: 540*verticalScale)
panel("home", points: [NSPoint(x: 820*horizontalScale, y: 0), NSPoint(x: canvasWidth, y: 0), NSPoint(x: canvasWidth, y: canvasHeight), NSPoint(x: 1100*horizontalScale, y: canvasHeight)], x: 1260*horizontalScale, top: 540*verticalScale, width: 660*horizontalScale, height: 540*verticalScale)
func text(_ string: String, top: CGFloat, size: CGFloat, left: CGFloat? = nil) {
    let name = "PingFangSC-Semibold"
    var font = NSFont(name: name, size: size) ?? NSFont.boldSystemFont(ofSize: size)
    let width = (string as NSString).size(withAttributes: [.font: font]).width
    if width > textWidth {
        font = NSFont(name: name, size: size*textWidth/width) ?? NSFont.boldSystemFont(ofSize: size*textWidth/width)
    }
    let shadow = NSShadow(); shadow.shadowColor = NSColor.black.withAlphaComponent(0.35)
    shadow.shadowBlurRadius = 6; shadow.shadowOffset = NSSize(width: 0, height: -3)
    let attributes: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: NSColor.white, .shadow: shadow]
    let dimensions = (string as NSString).size(withAttributes: attributes)
    guard font.pointSize >= 80, dimensions.width <= textWidth+1, top+dimensions.height <= canvasHeight-60 else {
        fail("封面文字过长，无法保持缩略图字号：\(string)")
    }
    (string as NSString).draw(at: NSPoint(x: left ?? (canvasWidth-dimensions.width)/2, y: canvasHeight-top-dimensions.height), withAttributes: attributes)
}
func logo(_ path: String, center: CGFloat) {
    guard let image = NSImage(contentsOfFile: path) else { fail("无法读取官方 Logo：\(path)") }
    let scale = min(620*horizontalScale/image.size.width, 470*verticalScale/image.size.height)
    let width = image.size.width*scale, height = image.size.height*scale
    image.draw(in: NSRect(x: center-width/2, y: canvasHeight-canvasHeight/2-height/2, width: width, height: height))
}
text(value("season") + " " + value("stage"), top: 806*verticalScale, size: 80, left: 88*horizontalScale)
logo(value("away_logo"), center: 440*horizontalScale); logo(value("home_logo"), center: 1490*horizontalScale)
text(value("date"), top: 906*verticalScale, size: 80, left: 88*horizontalScale)
NSGraphicsContext.restoreGraphicsState()
guard let png = bitmap.representation(using: .png, properties: [:]) else { fail("无法编码封面 PNG") }
do { try png.write(to: URL(fileURLWithPath: args[2])) } catch { fail("无法保存封面：\(error)") }
