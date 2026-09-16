import SwiftUI

/// How much attention a window deserves. Three steps, not a gradient: the bar
/// has one glyph and the eye gets one decision out of it.
enum Severity: Int, Comparable {
    case calm = 0, watch = 1, alert = 2

    static func < (a: Severity, b: Severity) -> Bool { a.rawValue < b.rawValue }

    var color: Color {
        switch self {
        case .calm:  return .green
        // Amber-leaning rather than pure yellow, which washes out against a
        // light menu bar.
        case .watch: return Color(red: 0.90, green: 0.68, blue: 0.0)
        case .alert: return .red
        }
    }
}

struct UsageWindow: Codable, Identifiable {
    let key: String
    let label: String
    let usedPct: Double
    /// Where this window ends up: the answer, not the slope.
    let headline: String
    let verdict: String
    let tone: String
    let state: String
    let kicker: String
    let detail: String
    let basis: String
    let resetsAt: String?
    let overCap: Bool
    let resetsIn: Int?

    var id: String { key }

    /// `state` is the whole answer; the server's chart_state already weighed
    /// proximity, pace and the projected crossing. Reading `tone` here as well
    /// is what previously made amber unreachable, since every window ahead of
    /// pace carried both tone "warning" and state "alert".
    var severity: Severity {
        switch state {
        case "alert": return .alert
        case "watch": return .watch
        default:      return .calm
        }
    }

    /// What the bar shows for this window: a whole number, no decimal point.
    /// 84.4 and 84.6 are the same fact at menu bar size.
    var shortPct: String { "\(Int(usedPct.rounded()))%" }

    enum CodingKeys: String, CodingKey {
        case key, label, headline, verdict, tone, state, kicker, detail, basis
        case usedPct   = "used_pct"
        case resetsAt  = "resets_at"
        case overCap   = "over_cap"
        case resetsIn  = "resets_in"
    }
}

struct MenuBarPayload: Codable {
    let ok: Bool
    let error: String?
    let checkedAt: String?
    let hoursPerDay: Int?
    let hoursLabel: String?
    let sampleDays: Int?
    let worst: String?
    let windows: [UsageWindow]

    enum CodingKeys: String, CodingKey {
        case ok, error, worst, windows
        case checkedAt  = "checked_at"
        case hoursPerDay = "hours_per_day"
        case hoursLabel = "hours_label"
        case sampleDays = "sample_days"
    }
}

/// Seconds to a short human countdown. "4h 12m", "38m", "under a minute".
func countdown(_ seconds: Int) -> String {
    if seconds <= 60 { return "under a minute" }
    let m = seconds / 60, h = m / 60
    if h >= 1 { return m % 60 == 0 ? "\(h)h" : "\(h)h \(m % 60)m" }
    return "\(m)m"
}


extension String {
    /// Server strings are lowercase fragments by convention; the client decides
    /// which of them start a sentence.
    var sentenceCased: String { isEmpty ? self : prefix(1).uppercased() + dropFirst() }
}
