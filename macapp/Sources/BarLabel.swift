import SwiftUI

/// The whole menu bar label, drawn as one image: a filled pill per window.
///
/// Drawn rather than built from SwiftUI Text because the menu bar renders a
/// label as a template. It throws colour away, and it keeps only the first
/// image and the first text of an HStack. Colour is the signal here and there
/// are two or three windows, so neither survives.
///
/// Each pill carries its own background, and that is the point. The menu bar
/// is tinted by whatever wallpaper sits behind it, so text drawn straight onto
/// it (grey labels, coloured figures) was unreadable on the first attempt.
/// A pill brings its own contrast: the fill says green, amber or red, and the
/// text on it is chosen against that fill, never against the bar.
enum BarLabel {
    private static let font = NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .semibold)
    private static let height: CGFloat = 18      // the image; the bar centres it
    private static let pillHeight: CGFloat = 16
    private static let padX: CGFloat = 5         // inside a pill, left and right
    private static let gap: CGFloat = 4          // between pills
    private static let radius: CGFloat = 4

    /// Fills are fixed sRGB, not system colours: a system green shifts with
    /// appearance and accessibility settings, and the text colour below is
    /// only guaranteed legible against these exact values.
    private static func fill(_ s: Severity) -> NSColor {
        switch s {
        case .calm:  return NSColor(srgbRed: 0.13, green: 0.55, blue: 0.27, alpha: 1)
        case .watch: return NSColor(srgbRed: 0.98, green: 0.76, blue: 0.10, alpha: 1)
        case .alert: return NSColor(srgbRed: 0.82, green: 0.15, blue: 0.15, alpha: 1)
        }
    }

    /// White on green and red; near-black on amber, where white washes out.
    private static func ink(_ s: Severity) -> NSColor {
        s == .watch ? NSColor(white: 0.10, alpha: 1) : .white
    }

    private static func text(_ w: UsageWindow) -> NSAttributedString {
        NSAttributedString(string: "\(w.barName) \(w.barValue)", attributes: [
            .font: font, .foregroundColor: ink(w.severity)])
    }

    static func image(_ windows: [UsageWindow]) -> NSImage {
        guard !windows.isEmpty else {
            // No reading yet. A template image, so the bar colours it itself.
            let dash = NSAttributedString(string: "--", attributes: [
                .font: font, .foregroundColor: NSColor.black])
            let size = dash.size()
            let img = NSImage(size: NSSize(width: ceil(size.width), height: height), flipped: false) { r in
                dash.draw(at: NSPoint(x: 0, y: (r.height - size.height) / 2)); return true
            }
            img.isTemplate = true
            return img
        }
        let widths = windows.map { ceil(text($0).size().width) + padX * 2 }
        let total = widths.reduce(0, +) + gap * CGFloat(windows.count - 1)
        let img = NSImage(size: NSSize(width: total, height: height), flipped: false) { r in
            var x: CGFloat = 0
            for (w, width) in zip(windows, widths) {
                let pill = NSRect(x: x, y: (r.height - pillHeight) / 2, width: width, height: pillHeight)
                fill(w.severity).setFill()
                NSBezierPath(roundedRect: pill, xRadius: radius, yRadius: radius).fill()
                let t = text(w), ts = t.size()
                t.draw(at: NSPoint(x: x + padX, y: pill.minY + (pillHeight - ts.height) / 2))
                x += width + gap
            }
            return true
        }
        img.isTemplate = false
        return img
    }
}
