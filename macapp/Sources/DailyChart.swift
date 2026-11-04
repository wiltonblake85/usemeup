import Charts
import SwiftUI

/// The week as seven bars, one per day, drawn above the window's burn-up chart.
///
/// Until 2026-11-04 these bars were on the web page only, so seeing a week
/// meant two windows: this drop-down for the forecast, a browser tab for the
/// days. The page is gone and the drop-down draws both.
///
/// The days are the server's (daily.py, panel.daily_view): cut at the hour the
/// week resets, so the seven bars add up to the week's figure, with a seventh
/// of the week as each day's share. The x axis is the burn-up chart's own
/// (WeekAxis over the same t0...t1), so Wednesday's bar sits directly above
/// Wednesday on the line below it.
///
///   solid bar      measured: the figure rose while samples were minutes apart
///   lighter bar    arrived while nothing was sampling, spread evenly over the gap
///   red top        the part of a day above its share, with the amount on it
///   dashed rule    the daily share (a seventh of the week)
///   short dashes   on today's bar: what today may spend, its share plus any
///                  share earlier days left unspent
struct DailyChart: View {
    let window: UsageWindow
    let daily: DailyView

    private var days: [DailyBar] { daily.days ?? [] }
    private var share: Double { daily.sharePct ?? 100.0 / 7 }

    /// The burn-up chart's used colour, except that an alert window's bars go
    /// orange: red is kept for the part of a day over its share, and the two
    /// must never be the same colour.
    private var hue: Color {
        switch window.severity {
        case .calm:  return .green
        case .watch: return Severity.watch.color
        case .alert: return .orange
        }
    }

    /// Follows what was spent, not what could be: after a quiet stretch
    /// today's allowance can be several times the share and would flatten
    /// every bar. An allowance above the top is drawn at the top edge.
    private var yTop: Double {
        max(share * 2.2, (days.map(\.usedPct).max() ?? 0) * 1.22)
    }

    private enum Kind { case measured, unsampled, over }

    private struct Segment: Identifiable {
        let id: String
        let from: Date, to: Date
        let lo: Double, hi: Double
        let kind: Kind
    }

    /// A bar's figure, placed on its own so it can move off the share line.
    private struct Figure: Identifiable {
        let id: Double
        let x: Date
        let y: Double
        let text: String
        let over: Bool
    }

    /// Points of plot height in a 116 pt chart, less the day labels. Used only
    /// to tell whether two labels would touch.
    private static let plotHeight = 100.0
    private func points(_ pct: Double) -> Double { pct / yTop * Self.plotHeight }

    /// A bar's horizontal extent: its day, less a margin each side, so the
    /// seven read as separate bars and not as one block.
    private func span(_ d: DailyBar) -> (Date, Date) {
        let pad = (d.t1 - d.t0) * 0.19
        return (Date(timeIntervalSince1970: d.t0 + pad), Date(timeIntervalSince1970: d.t1 - pad))
    }

    /// Over by at least what one decimal place shows, so a day at 14.30%
    /// against a 14.29% share never reads "+0.0% over".
    private func isOver(_ d: DailyBar) -> Bool { d.usedPct - share >= 0.05 }

    private var segments: [Segment] {
        var out: [Segment] = []
        for d in days where !d.isFuture && d.usedPct > 0 {
            let (a, b) = span(d)
            var parts: [(Double, Double, Kind)] = []
            let measured = min(d.measuredPct, d.usedPct)
            if measured > 0 { parts.append((0, measured, .measured)) }
            if d.usedPct - measured > 0.005 { parts.append((measured, d.usedPct, .unsampled)) }
            if isOver(d) { parts.append((share, d.usedPct, .over)) }
            for (i, p) in parts.enumerated() {
                out.append(Segment(id: "\(d.t0)-\(i)", from: a, to: b, lo: p.0, hi: p.1, kind: p.2))
            }
        }
        return out
    }

    private var figures: [Figure] {
        days.filter { !$0.isFuture && $0.usedPct > 0 }.map { d in
            let over = isOver(d)
            // A figure just under the share line would sit on it, struck
            // through. It goes just above the line instead, still centred
            // over its own bar.
            let crowded = !over && points(share - d.usedPct) < 13
            return Figure(id: d.t0, x: Date(timeIntervalSince1970: (d.t0 + d.t1) / 2),
                          y: crowded ? share : d.usedPct,
                          text: over ? "+\(Self.pct(d.usedPct - share)) over" : Self.pct(d.usedPct),
                          over: over)
        }
    }

    private func fill(_ k: Kind) -> Color {
        switch k {
        case .measured:  return hue
        case .unsampled: return hue.opacity(0.35)
        case .over:      return .red.opacity(0.9)
        }
    }

    var body: some View {
        if let a = window.t0, let b = window.t1, !days.isEmpty {
            chart(WeekAxis(t0: a, t1: b))
        }
    }

    private func chart(_ axis: WeekAxis) -> some View {
        let today = days.first(where: \.isToday)
        return Chart {
            // Every day gets its slot, so the week reads as seven columns even
            // while most of them are still to come.
            ForEach(days) { d in
                let (a, b) = span(d)
                RectangleMark(xStart: .value("From", a), xEnd: .value("To", b),
                              yStart: .value("Low", 0.0), yEnd: .value("High", yTop))
                    .foregroundStyle(Color.secondary.opacity(0.08))
                    .cornerRadius(3)
            }
            // Named in the legend and on the y axis rather than in the plot,
            // where its label would land on the last day's figure.
            RuleMark(y: .value("Share", share))
                .foregroundStyle(DailyStyle.shareInk)
                .lineStyle(DailyStyle.share)
            ForEach(segments) { s in
                RectangleMark(xStart: .value("From", s.from), xEnd: .value("To", s.to),
                              yStart: .value("Low", s.lo), yEnd: .value("High", s.hi))
                    .foregroundStyle(fill(s.kind))
                    .cornerRadius(2)
            }
            ForEach(figures) { f in
                PointMark(x: .value("Day", f.x), y: .value("Figure", f.y))
                    .symbolSize(0)
                    .annotation(position: .top, alignment: .center, spacing: 2) {
                        Text(f.text)
                            .font(.system(size: 10, weight: f.over ? .semibold : .regular))
                            .monospacedDigit()
                            .foregroundStyle(f.over ? Color.red : Color.primary)
                            .fixedSize()
                    }
            }
            if let t = today, let avail = t.availablePct, avail > 0 {
                let (a, b) = span(t)
                let capped = avail > yTop
                RuleMark(xStart: .value("From", a.addingTimeInterval(-1800)),
                         xEnd: .value("To", b.addingTimeInterval(1800)),
                         y: .value("Today may spend", min(avail, yTop)))
                    .foregroundStyle(Color.primary.opacity(0.85))
                    .lineStyle(DailyStyle.allowance)
                    .annotation(position: capped ? .bottom : .top, alignment: .center, spacing: 2) {
                        if allowanceLabelFits(avail: avail, capped: capped,
                                              figure: figures.first { $0.id == t.t0 }?.y ?? 0) {
                            Text("up to \(Self.pct(avail))")
                                .font(.system(size: 9)).foregroundStyle(.secondary)
                                .fixedSize()
                        }
                    }
            }
        }
        .chartLegend(.hidden)
        .chartXScale(domain: axis.start...axis.end)
        .chartYScale(domain: 0...yTop)
        .chartYAxis {
            AxisMarks(position: .leading, values: [0, share, share * 2].filter { $0 <= yTop }) { v in
                AxisGridLine().foregroundStyle(Color.secondary.opacity(0.18))
                AxisValueLabel {
                    if let d = v.as(Double.self) {
                        Text(d == 0 ? "0%" : "\(Int(d.rounded()))%")
                            .font(.system(size: 9))
                            .frame(width: BurnStyle.yLabelWidth, alignment: .trailing)
                    }
                }
            }
        }
        .chartXAxis {
            AxisMarks(values: axis.mids) { v in
                AxisValueLabel(centered: false, anchor: .topLeading) {
                    if let date = v.as(Date.self) {
                        let now = today.map { date.timeIntervalSince1970 >= $0.t0
                            && date.timeIntervalSince1970 < $0.t1 } ?? false
                        WeekAxis.centred(Text(WeekAxis.dayLabel(date))
                            .font(.system(size: 9, weight: now ? .semibold : .regular))
                            .foregroundStyle(now ? Color.primary : Color.secondary))
                    }
                }
            }
        }
        .frame(height: 116)
        .accessibilityElement()
        .accessibilityLabel("\(window.label) by day. " + (daily.today?.sentenceCased ?? ""))
    }

    /// Today's "up to" label only when the allowance stands clear of the
    /// share line and of the bar's own figure; the sentence under the chart
    /// says it either way. `figure` is where that figure sits.
    private func allowanceLabelFits(avail: Double, capped: Bool, figure: Double) -> Bool {
        guard abs(avail - share) > 0.5 else { return false }
        let shown = min(avail, yTop)
        // Above the rule normally; below it when the rule is pinned to the top.
        return points(shown - figure) > (capped ? 27 : 15) && points(abs(shown - share)) > 9
    }

    static func pct(_ v: Double) -> String { String(format: "%.1f%%", v) }
}

/// The seven day columns of a weekly window, shared by DailyChart and BurnChart
/// so a day's bar and the same day on the line below sit in one column.
///
/// Days start at the window's own start, the hour the week resets, not at
/// midnight, and each is labelled by its midpoint: the day that opens at 7 PM
/// on Tuesday is Wednesday, as anyone reading it would call it.
struct WeekAxis {
    let t0: Double
    let t1: Double

    var start: Date { Date(timeIntervalSince1970: t0) }
    var end: Date { Date(timeIntervalSince1970: t1) }
    private var count: Int { max(1, Int(((t1 - t0) / 86400).rounded())) }

    /// Where one day ends and the next begins, both ends included.
    var bounds: [Date] { (0...count).map { Date(timeIntervalSince1970: t0 + Double($0) * 86400) } }
    /// The middle of each day, where its label goes.
    var mids: [Date] { (0..<count).map { Date(timeIntervalSince1970: t0 + (Double($0) + 0.5) * 86400) } }

    /// "Wed 4"
    static func dayLabel(_ d: Date) -> String {
        d.formatted(.dateTime.weekday(.abbreviated).day())
    }

    /// A day label centred on its tick. Charts places an axis label's leading
    /// edge on the tick, and on macOS 27.0 it ignores every `anchor:` value
    /// ("Custom UnitPoint values are not supported", even for .top and
    /// .center). So the label gets a fixed width and is moved back by half of
    /// it, which lands the same way on every macOS. Measured 2026-11-04.
    ///
    /// The width stays under a column's: the last label's box runs half its
    /// width past its tick, and a box that runs past the end of the chart is
    /// dropped (a 60 pt box lost "Tue 10" once a scroll bar took 15 pt). A
    /// longer name overflows the box evenly rather than being cut.
    static func centred<V: View>(_ label: V) -> some View {
        label.fixedSize().frame(width: 40).offset(x: -20)
    }
}

/// The bars' two rules, shared with their legend.
enum DailyStyle {
    static let share = StrokeStyle(lineWidth: 1.2, dash: [4, 3])
    static let shareInk = Color.primary.opacity(0.5)
    static let allowance = StrokeStyle(lineWidth: 1.5, dash: [2, 2])
}

/// What the bars mean, once at the foot of the drop-down beside BurnLegend.
/// Neutral swatches, as there: a bar takes its window's colour, which may be
/// green, amber or orange. "Not sampled" is listed only while some bar has it.
struct DailyLegend: View {
    var unsampled: Bool

    var body: some View {
        HStack(spacing: 14) {
            key(Color.secondary, "measured")
            if unsampled { key(Color.secondary.opacity(0.35), "not sampled, spread evenly") }
            key(Color.red.opacity(0.9), "over the daily share")
            HStack(spacing: 5) {
                Path { p in p.move(to: .init(x: 0, y: 1)); p.addLine(to: .init(x: 14, y: 1)) }
                    .stroke(DailyStyle.shareInk, style: DailyStyle.share)
                    .frame(width: 14, height: 2)
                Text("daily share")
            }
        }
        .font(.system(size: 10))
        .foregroundStyle(.secondary)
    }

    private func key(_ fill: Color, _ text: String) -> some View {
        HStack(spacing: 5) {
            RoundedRectangle(cornerRadius: 2).fill(fill).frame(width: 10, height: 8)
            Text(text)
        }
    }
}
