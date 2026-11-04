import Charts
import SwiftUI

/// The burn-up chart for one window, drawn from the server's own series.
///
/// This is the chart the web page used to be the only place to see (the page
/// was removed 2026-11-04). The page computed its lines in JavaScript; this
/// draws the three series panel.py already computes (the same ones Transom's
/// notch reads), so the app and the notch cannot disagree about where a
/// window lands. On a weekly window it sits under that window's DailyChart,
/// on the same seven day columns.
///
///   filled line   what has been used, from the five-minute samples, in the
///                 window's colour, with a dot where it stands now
///   grey dashed   where the current pace lands by reset (red if over 100%)
///   dotted grey   the reference pace: your working hours, or the clock
///   red rule      the cap
///   grey rule     now
///
/// Colour belongs to what happened. The forecast is a guess, so it stays grey
/// until it crosses the cap. Before 2026-09-29 it was drawn in the used
/// line's green, and on the first evening of a week, with 1% used and a
/// forecast built wholly from past weeks, a green dash ran the full width of
/// the chart and read as last week's usage that had failed to reset.
struct BurnChart: View {
    let window: UsageWindow

    private struct Pt: Identifiable {
        let id: Int
        let t: Date
        let v: Double
    }

    private func pts(_ raw: [[Double]]?) -> [Pt] {
        (raw ?? []).enumerated().compactMap { i, p in
            p.count >= 2 ? Pt(id: i, t: Date(timeIntervalSince1970: p[0]), v: p[1]) : nil
        }
    }

    private var observed: [Pt] { pts(window.observed) }
    private var projection: [Pt] { pts(window.projection) }
    private var reference: [Pt] { pts(window.reference) }

    /// Where the forecast lands at reset.
    private var landing: Double? { projection.last?.v }
    private var over: Bool { (landing ?? 0) > 100 }
    /// The used line: the window's own colour.
    private var tint: Color { window.severity == .calm ? .green : window.severity.color }
    /// The forecast: grey, darker and longer-dashed than the pace line so the
    /// two stay apart where they run close, and red once it crosses the cap.
    private var forecastInk: Color { over ? .red : BurnStyle.forecastGrey }

    /// 0-100, unless the landing would be clipped: over the cap, or close
    /// enough to 100 that its label runs off the top. The server sends the
    /// same answer as y_max (panel.axis_top); this agrees with it for a
    /// server that predates the rule.
    private var yTop: Double {
        var top = max(window.yMax ?? 100, 100)
        if let land = landing, land > BurnStyle.headroomFrom {
            top = max(top, ((land + 8) / 25).rounded(.up) * 25)
        }
        return top
    }

    private var isShort: Bool {
        guard let a = window.t0, let b = window.t1 else { return false }
        return b - a < 36 * 3600
    }

    var body: some View {
        if let a = window.t0, let b = window.t1, observed.count > 1 {
            chart(from: Date(timeIntervalSince1970: a), to: Date(timeIntervalSince1970: b))
        }
    }

    private func chart(from start: Date, to end: Date) -> some View {
        Chart {
            ForEach(reference) { p in
                LineMark(x: .value("Time", p.t), y: .value("Used", p.v),
                         series: .value("Line", "pace"))
                    .foregroundStyle(BurnStyle.paceGrey)
                    .lineStyle(BurnStyle.pace)
            }
            ForEach(projection) { p in
                LineMark(x: .value("Time", p.t), y: .value("Used", p.v),
                         series: .value("Line", "forecast"))
                    .foregroundStyle(forecastInk)
                    .lineStyle(BurnStyle.forecast)
            }
            if let last = projection.last {
                PointMark(x: .value("Time", last.t), y: .value("Used", last.v))
                    .foregroundStyle(forecastInk)
                    .symbolSize(24)
                    .annotation(position: .leading, alignment: .center, spacing: 4) {
                        Text(pct(last.v))
                            .font(.system(size: 11, weight: .semibold)).monospacedDigit()
                            .foregroundStyle(over ? Color.red : Color.secondary)
                    }
            }
            // Used goes on top of the guesses, so it is never hidden under them.
            ForEach(observed) { p in
                AreaMark(x: .value("Time", p.t), y: .value("Used", p.v))
                    .foregroundStyle(tint.opacity(0.14))
            }
            ForEach(observed) { p in
                LineMark(x: .value("Time", p.t), y: .value("Used", p.v),
                         series: .value("Line", "used"))
                    .foregroundStyle(tint)
                    .lineStyle(BurnStyle.used)
            }
            // Where you stand now. In the first hours of a window the used
            // line is a few points wide; the dot keeps it findable.
            if let here = observed.last {
                PointMark(x: .value("Time", here.t), y: .value("Used", here.v))
                    .foregroundStyle(tint)
                    .symbolSize(36)
            }
            RuleMark(y: .value("Cap", 100))
                .foregroundStyle(Color.red.opacity(0.7))
                .lineStyle(StrokeStyle(lineWidth: 1, dash: [2, 3]))
            if let n = window.now {
                RuleMark(x: .value("Now", Date(timeIntervalSince1970: n)))
                    .foregroundStyle(Color.primary.opacity(0.45))
                    .lineStyle(StrokeStyle(lineWidth: 1))
            }
        }
        .chartLegend(.hidden)
        .chartXScale(domain: start...end)
        .chartYScale(domain: 0...yTop)
        .chartYAxis {
            AxisMarks(position: .leading, values: Array(stride(from: 0.0, through: yTop, by: 25))) { v in
                AxisGridLine().foregroundStyle(Color.secondary.opacity(0.18))
                AxisValueLabel {
                    // A fixed width, the same as DailyChart's, so the two plots
                    // above each other start at the same x and their day
                    // columns line up.
                    if let d = v.as(Double.self) {
                        Text("\(Int(d))%").font(.system(size: 9))
                            .frame(width: BurnStyle.yLabelWidth, alignment: .trailing)
                    }
                }
            }
        }
        .chartXAxis {
            if isShort {
                AxisMarks(values: .stride(by: .hour)) { _ in
                    AxisGridLine().foregroundStyle(Color.secondary.opacity(0.12))
                    AxisValueLabel(format: .dateTime.hour(), centered: false)
                        .font(.system(size: 9))
                }
            } else {
                // The week's own days, cut at the reset hour and labelled at
                // their middles (WeekAxis), not calendar midnights: the same
                // seven columns as the daily bars drawn above this chart.
                // Before 2026-11-04 the grid sat on midnights, four or five
                // hours off the bars' days.
                let axis = WeekAxis(t0: start.timeIntervalSince1970, t1: end.timeIntervalSince1970)
                AxisMarks(values: axis.bounds) { _ in
                    AxisGridLine().foregroundStyle(Color.secondary.opacity(0.12))
                }
                AxisMarks(values: axis.mids) { v in
                    AxisValueLabel(centered: false, anchor: .topLeading) {
                        if let d = v.as(Date.self) {
                            WeekAxis.centred(Text(d.formatted(.dateTime.weekday(.abbreviated)))
                                .font(.system(size: 9)))
                        }
                    }
                }
            }
        }
        .frame(height: 150)
        .accessibilityLabel("Burn-up chart for \(window.label): \(pct(window.usedPct)) used"
            + (landing.map { ", forecast \(pct($0)) at reset" } ?? ""))
    }

    private func pct(_ v: Double) -> String {
        v >= 10 ? "\(Int(v.rounded()))%" : String(format: "%.1f%%", v)
    }
}

/// One definition of each line, shared by the chart and its legend so the two
/// cannot drift apart.
enum BurnStyle {
    static let used = StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round)
    static let forecast = StrokeStyle(lineWidth: 1.5, dash: [5, 4])
    static let pace = StrokeStyle(lineWidth: 1, dash: [2, 3])

    static let forecastGrey = Color.primary.opacity(0.5)
    static let paceGrey = Color.secondary.opacity(0.7)

    /// panel.HEADROOM_FROM: above this landing the axis grows past 100.
    static let headroomFrom = 94.0

    /// Every chart's y-axis labels get this width, so stacked charts' plots
    /// start at the same x.
    static let yLabelWidth: CGFloat = 26
}

/// What the lines mean, once under all the charts rather than under each.
/// The used swatch stays neutral: the used line takes the window's colour,
/// which may be green, amber or red, and one swatch cannot be all three.
struct BurnLegend: View {
    var body: some View {
        HStack(spacing: 14) {
            key(line(BurnStyle.used), "used")
            key(line(BurnStyle.forecast).foregroundStyle(BurnStyle.forecastGrey), "forecast")
            key(line(BurnStyle.pace).foregroundStyle(BurnStyle.paceGrey), "your pace")
            key(line(StrokeStyle(lineWidth: 1, dash: [2, 3]))
                    .foregroundStyle(Color.red.opacity(0.7)), "cap")
        }
        .font(.system(size: 10))
        .foregroundStyle(.secondary)
    }

    private func line(_ style: StrokeStyle) -> some View {
        Path { p in p.move(to: .init(x: 0, y: 1)); p.addLine(to: .init(x: 14, y: 1)) }
            .stroke(style: style)
            .frame(width: 14, height: 2)
    }

    private func key<V: View>(_ swatch: V, _ text: String) -> some View {
        HStack(spacing: 5) { swatch; Text(text) }
    }
}
