import SwiftUI

/// How each window is drawn in the menu bar. The colour always means the same
/// thing (the server's forecast state: green on pace, amber watch, red over);
/// the style only changes how loudly it is said.
enum PillStyle: String, CaseIterable, Identifiable {
    case solid, split, tinted, outline, dot, quiet, microbar

    static let key = "pillStyle"
    static let fallback = PillStyle.solid

    var id: String { rawValue }

    var title: String {
        switch self {
        case .solid:    return "Solid"
        case .split:    return "Split badge"
        case .tinted:   return "Tinted"
        case .outline:  return "Outline"
        case .dot:      return "Dot"
        case .quiet:    return "Quiet until it matters"
        case .microbar: return "Micro bar"
        }
    }

    var blurb: String {
        switch self {
        case .solid:    return "A filled pill in the window's colour. Loudest, and reads over any wallpaper."
        case .split:    return "The name on a grey block, the figure on the coloured one."
        case .tinted:   return "A soft wash of the colour behind coloured text."
        case .outline:  return "A coloured border, with text in the menu bar's own colour."
        case .dot:      return "A small coloured dot before each figure. The most native look."
        case .quiet:    return "Plain text while on pace, tinted at amber, filled at red."
        case .microbar: return "Each figure with a thin fill bar underneath."
        }
    }
}

/// The whole menu bar label, drawn as one image.
///
/// Drawn rather than built from SwiftUI Text because the menu bar renders a
/// label as a template. It throws colour away, and it keeps only the first
/// image and the first text of an HStack. Colour is the signal here and there
/// are two or three windows, so neither survives.
///
/// Styles that put text straight on the bar (outline, dot, quiet, micro bar)
/// pick black or white from the appearance they are drawn into, which for a
/// status item is the menu bar's own. Styles with a fill choose their text
/// against the fill, never against the bar.
enum BarLabel {
    private static let font = NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .semibold)
    private static let nameFont = NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .medium)
    private static let smallFont = NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .semibold)
    private static let height: CGFloat = 18      // the image; the bar centres it
    private static let pillHeight: CGFloat = 16
    private static let padX: CGFloat = 5         // inside a pill, left and right
    private static let gap: CGFloat = 4          // between pills
    private static let radius: CGFloat = 4
    private static let dotSize: CGFloat = 7

    private static func srgb(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat) -> NSColor {
        NSColor(srgbRed: r, green: g, blue: b, alpha: 1)
    }

    /// Solid fills. Fixed sRGB, not system colours: a system green shifts with
    /// appearance and accessibility settings, and the ink below is only
    /// guaranteed legible against these exact values.
    private static func fill(_ s: Severity) -> NSColor {
        switch s {
        case .calm:  return srgb(0.13, 0.55, 0.27)
        case .watch: return srgb(0.98, 0.76, 0.10)
        case .alert: return srgb(0.82, 0.15, 0.15)
        }
    }

    /// White on green and red; near-black on amber, where white washes out.
    private static func ink(_ s: Severity) -> NSColor {
        s == .watch ? NSColor(white: 0.10, alpha: 1) : .white
    }

    /// Accent for strokes, dots and bars drawn straight on the menu bar:
    /// brighter on a dark bar, deeper on a light one.
    private static func accent(_ s: Severity, dark: Bool) -> NSColor {
        switch (s, dark) {
        case (.calm, true):   return srgb(0.19, 0.82, 0.35)
        case (.watch, true):  return srgb(1.00, 0.70, 0.10)
        case (.alert, true):  return srgb(1.00, 0.30, 0.26)
        case (.calm, false):  return srgb(0.12, 0.58, 0.28)
        case (.watch, false): return srgb(0.88, 0.55, 0.00)
        case (.alert, false): return srgb(0.84, 0.10, 0.12)
        }
    }

    /// Coloured text on a tint. Deeper than the accent on a light bar, where a
    /// light green on a pale wash would vanish.
    private static func tintInk(_ s: Severity, dark: Bool) -> NSColor {
        if dark { return accent(s, dark: true) }
        switch s {
        case .calm:  return srgb(0.07, 0.42, 0.18)
        case .watch: return srgb(0.58, 0.32, 0.00)
        case .alert: return srgb(0.72, 0.04, 0.08)
        }
    }

    private static func tint(_ s: Severity, dark: Bool) -> NSColor {
        accent(s, dark: dark).withAlphaComponent(dark ? 0.28 : 0.22)
    }

    /// Text drawn straight on the bar.
    private static func plain(dark: Bool) -> NSColor {
        dark ? NSColor(white: 1, alpha: 0.95) : NSColor(white: 0.08, alpha: 1)
    }

    private static func str(_ s: String, _ f: NSFont, _ c: NSColor) -> NSAttributedString {
        NSAttributedString(string: s, attributes: [.font: f, .foregroundColor: c])
    }

    private static func label(_ w: UsageWindow) -> String { "\(w.barName) \(w.barValue)" }

    private static func textWidth(_ s: String, _ f: NSFont = font) -> CGFloat {
        ceil(str(s, f, .black).size().width)
    }

    // ------------------------------------------------------------ widths

    private static func width(_ w: UsageWindow, _ style: PillStyle) -> CGFloat {
        switch style {
        case .solid, .tinted, .outline, .quiet:
            return textWidth(label(w)) + padX * 2
        case .split:
            return textWidth(w.barName, nameFont) + textWidth(w.barValue) + padX * 4
        case .dot:
            return dotSize + 4 + textWidth(label(w)) + 1
        case .microbar:
            return textWidth(label(w), smallFont) + 4
        }
    }

    // ------------------------------------------------------------ drawing

    private static func centred(_ t: NSAttributedString, x: CGFloat, in pill: NSRect) {
        let ts = t.size()
        t.draw(at: NSPoint(x: x, y: pill.minY + (pill.height - ts.height) / 2))
    }

    private static func draw(_ w: UsageWindow, _ style: PillStyle, x: CGFloat, width: CGFloat,
                             in r: NSRect, dark: Bool) {
        let s = w.severity
        let pill = NSRect(x: x, y: (r.height - pillHeight) / 2, width: width, height: pillHeight)
        let shape = NSBezierPath(roundedRect: pill, xRadius: radius, yRadius: radius)

        switch style {
        case .solid:
            fill(s).setFill(); shape.fill()
            centred(str(label(w), font, ink(s)), x: x + padX, in: pill)

        case .tinted:
            tint(s, dark: dark).setFill(); shape.fill()
            centred(str(label(w), font, tintInk(s, dark: dark)), x: x + padX, in: pill)

        case .outline:
            let ring = NSBezierPath(roundedRect: pill.insetBy(dx: 0.75, dy: 0.75),
                                    xRadius: radius - 0.5, yRadius: radius - 0.5)
            ring.lineWidth = 1.5
            accent(s, dark: dark).setStroke(); ring.stroke()
            centred(str(label(w), font, plain(dark: dark)), x: x + padX, in: pill)

        case .split:
            let nameW = textWidth(w.barName, nameFont) + padX * 2
            NSGraphicsContext.saveGraphicsState()
            shape.addClip()
            NSColor(white: dark ? 0.36 : 0.40, alpha: 1).setFill()
            NSRect(x: x, y: pill.minY, width: nameW, height: pill.height).fill()
            fill(s).setFill()
            NSRect(x: x + nameW, y: pill.minY, width: width - nameW, height: pill.height).fill()
            NSGraphicsContext.restoreGraphicsState()
            centred(str(w.barName, nameFont, .white), x: x + padX, in: pill)
            centred(str(w.barValue, font, ink(s)), x: x + nameW + padX, in: pill)

        case .dot:
            let dot = NSRect(x: x, y: (r.height - dotSize) / 2, width: dotSize, height: dotSize)
            accent(s, dark: dark).setFill(); NSBezierPath(ovalIn: dot).fill()
            centred(str(label(w), font, plain(dark: dark)), x: x + dotSize + 4, in: pill)

        case .quiet:
            switch s {
            case .calm:
                centred(str(label(w), font, plain(dark: dark)), x: x + padX, in: pill)
            case .watch:
                tint(s, dark: dark).setFill(); shape.fill()
                centred(str(label(w), font, tintInk(s, dark: dark)), x: x + padX, in: pill)
            case .alert:
                fill(s).setFill(); shape.fill()
                centred(str(label(w), font, ink(s)), x: x + padX, in: pill)
            }

        case .microbar:
            str(label(w), smallFont, plain(dark: dark)).draw(at: NSPoint(x: x + 2, y: 3.5))
            let track = NSRect(x: x + 2, y: 0.5, width: width - 4, height: 2.5)
            NSColor(white: dark ? 1 : 0, alpha: dark ? 0.25 : 0.18).setFill()
            NSBezierPath(roundedRect: track, xRadius: 1.25, yRadius: 1.25).fill()
            let frac = min(max(w.usedPct / 100, 0), 1)
            if frac > 0 {
                var bar = track
                bar.size.width = max(track.width * frac, 2.5)
                accent(s, dark: dark).setFill()
                NSBezierPath(roundedRect: bar, xRadius: 1.25, yRadius: 1.25).fill()
            }
        }
    }

    /// `dark` is the menu bar's appearance. The caller passes it rather than
    /// this reading it at draw time, because SwiftUI may rasterise the image
    /// once, under an appearance that is not the menu bar's.
    static func image(_ windows: [UsageWindow], style: PillStyle = .fallback, dark: Bool) -> NSImage {
        guard !windows.isEmpty else {
            // No reading yet. A template image, so the bar colours it itself.
            let dash = str("--", font, .black)
            let size = dash.size()
            let img = NSImage(size: NSSize(width: ceil(size.width), height: height), flipped: false) { r in
                dash.draw(at: NSPoint(x: 0, y: (r.height - size.height) / 2)); return true
            }
            img.isTemplate = true
            return img
        }
        let sp: CGFloat = (style == .dot || style == .microbar) ? 8 : gap
        let widths = windows.map { width($0, style) }
        let total = widths.reduce(0, +) + sp * CGFloat(windows.count - 1)
        let img = NSImage(size: NSSize(width: total, height: height), flipped: false) { r in
            var x: CGFloat = 0
            for (w, wd) in zip(windows, widths) {
                draw(w, style, x: x, width: wd, in: r, dark: dark)
                x += wd + sp
            }
            return true
        }
        img.isTemplate = false
        return img
    }
}
