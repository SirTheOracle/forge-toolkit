// render-icon.swift — rasterize forge-icon.svg to the template PNG embedded in
// forge-board.5s.sh. AppKit's NSImage reads SVG natively (macOS 11+), so this
// needs no external tooling (no librsvg/ImageMagick on the machine).
// Output: 36x36 px bitmap tagged 18x18 pt (@2x) — NSBitmapImageRep.size writes
// PNG DPI metadata, so SwiftBar (via NSImage) draws it at 18pt, retina-sharp.
// Usage: swift swiftbar/render-icon.swift <in.svg> <out.png>
import AppKit

let args = CommandLine.arguments
guard args.count == 3 else {
    FileHandle.standardError.write("usage: render-icon.swift <in.svg> <out.png>\n".data(using: .utf8)!)
    exit(2)
}
guard let svg = NSImage(contentsOfFile: args[1]) else {
    FileHandle.standardError.write("error: cannot load \(args[1])\n".data(using: .utf8)!)
    exit(1)
}
let pt = 18, scale = 2
guard let rep = NSBitmapImageRep(
    bitmapDataPlanes: nil, pixelsWide: pt * scale, pixelsHigh: pt * scale,
    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else {
    FileHandle.standardError.write("error: bitmap alloc failed\n".data(using: .utf8)!)
    exit(1)
}
rep.size = NSSize(width: pt, height: pt)   // point size → 144dpi PNG metadata
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
svg.draw(in: NSRect(x: 0, y: 0, width: pt, height: pt),
         from: .zero, operation: .sourceOver, fraction: 1.0)
NSGraphicsContext.restoreGraphicsState()
guard let png = rep.representation(using: .png, properties: [:]) else {
    FileHandle.standardError.write("error: png encode failed\n".data(using: .utf8)!)
    exit(1)
}
try! png.write(to: URL(fileURLWithPath: args[2]))
