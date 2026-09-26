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
    /// What change lands this window at 100% by reset. Absent while it is green.
    let advice: String?
    let verdict: String
    let tone: String
    let state: String
    let kicker: String
    let detail: String
    let basis: String
    let resetsAt: String?
    let overCap: Bool
    let resetsIn: Int?

    // The burn-up chart, present when the app asks /api/menubar?series=1.
    // Epoch seconds; each point is [t, percent].
    let t0: Double?
    let t1: Double?
    let now: Double?
    let yMax: Double?
    let reference: [[Double]]?
    let observed: [[Double]]?
    let projection: [[Double]]?

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

    /// The name this window goes by in the menu bar, where every character
    /// costs width. A model-scoped window is named by its model's initial
    /// ("Fable" -> "F"); the panel underneath spells everything out.
    var barName: String {
        switch key {
        case "session":           return "5h"
        case "weekly_all":        return "All"
        case "weekly_oauth_apps": return "Apps"
        default:                  return label.first.map { String($0).uppercased() } ?? "?"
        }
    }

    /// The figure beside the name, as a percentage. A window at its cap says
    /// so in a word, because "100%" reads as a number that might still move.
    var barValue: String { usedPct >= 100 ? "spent" : shortPct }

    // Which windows sit in the bar is the user's choice now; see WindowPrefs.

    enum CodingKeys: String, CodingKey {
        case key, label, headline, advice, verdict, tone, state, kicker, detail, basis
        case t0, t1, now, reference, observed, projection
        case yMax      = "y_max"
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
    /// Absent from a server older than the app; treated as none.
    let alerts: [UsageAlert]?

    enum CodingKeys: String, CodingKey {
        case ok, error, worst, windows, alerts
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
