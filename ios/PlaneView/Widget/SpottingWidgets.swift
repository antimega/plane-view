import ActivityKit
import SwiftUI
import WidgetKit

@main
struct PlaneViewWidgets: WidgetBundle {
    var body: some Widget {
        SpottingLiveActivity()
    }
}

struct SpottingLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: SpottingAttributes.self) { context in
            LockScreenView(attributes: context.attributes, state: context.state)
                .activityBackgroundTint(Color.black.opacity(0.55))
                .activitySystemActionForegroundColor(.white)
        } dynamicIsland: { context in
            let tint = Palette.side(context.attributes.side)
            return DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    SideBadge(side: context.attributes.side, small: true)
                        .padding(.leading, 4)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Countdown(to: context.state.nextAt)
                        .font(.title3.monospacedDigit().weight(.semibold))
                        .foregroundStyle(tint)
                        .frame(maxWidth: 76, alignment: .trailing)
                }
                DynamicIslandExpandedRegion(.center) {
                    VStack(spacing: 0) {
                        Text(context.attributes.side == "L" ? "LEFT WINDOW" : "RIGHT WINDOW")
                            .font(.system(size: 9, weight: .bold)).tracking(0.6).foregroundStyle(tint)
                        Text(context.state.nextName ?? "Nothing ahead yet")
                            .font(.headline)
                            .lineLimit(1).minimumScaleFactor(0.75)
                    }
                }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(alignment: .leading, spacing: 2) {
                        if let w = context.state.nextWhere {
                            Text(w).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                        }
                        if !context.state.inView.isEmpty {
                            Text("In view: " + context.state.inView.joined(separator: ", "))
                                .font(.caption).lineLimit(1)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 8)
                }
            } compactLeading: {
                SideBadge(side: context.attributes.side, small: true)
            } compactTrailing: {
                Countdown(to: context.state.nextAt)
                    .font(.caption.monospacedDigit().weight(.semibold))
                    .foregroundStyle(tint)
                    .frame(maxWidth: 44)
            } minimal: {
                Image(systemName: "binoculars.fill").foregroundStyle(tint)
            }
        }
    }
}

/// "in 3:20", counted down by the system; "now" once the time has come.
struct Countdown: View {
    var to: Date?
    var body: some View {
        if let to, to > Date() {
            Text(timerInterval: Date()...to, countsDown: true, showsHours: false)
                .multilineTextAlignment(.trailing)
        } else if to != nil {
            Text("now")
        } else {
            Text("–")
        }
    }
}

struct SideBadge: View {
    var side: String
    var small = false
    var body: some View {
        HStack(spacing: 3) {
            Image(systemName: side == "L" ? "arrow.left" : "arrow.right")
            if !small { Text(side == "L" ? "Left" : "Right") }
        }
        .font(small ? .caption2.weight(.bold) : .caption.weight(.bold))
        .padding(.horizontal, small ? 5 : 7).padding(.vertical, 3)
        .background(Palette.side(side), in: Capsule())
        .foregroundStyle(.white)
    }
}

struct LockScreenView: View {
    var attributes: SpottingAttributes
    var state: SpottingAttributes.ContentState

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                SideBadge(side: attributes.side)
                Text("\(attributes.flight)  \(attributes.route)")
                    .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                Spacer()
                Text(state.position)
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(state.uncertain ? Color.orange : Color.secondary)
            }
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(state.nextName ?? "Nothing ahead yet")
                        .font(.title3.weight(.semibold)).lineLimit(1)
                    if let w = state.nextWhere {
                        Text(w).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
                Spacer(minLength: 8)
                Countdown(to: state.nextAt)
                    .font(.title2.monospacedDigit().weight(.semibold))
                    .foregroundStyle(Palette.side(attributes.side))
                    .frame(maxWidth: 90, alignment: .trailing)
            }
            if !state.inView.isEmpty {
                Text("In view now: " + state.inView.joined(separator: ", "))
                    .font(.footnote).lineLimit(1)
            }
        }
        .padding(14)
        .foregroundStyle(.white)
    }
}
