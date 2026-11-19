import PlaneViewCore
import SwiftUI

/// The flight as a tape. The pin is where the app takes you to be; drag the handle, or step a minute at a time,
/// to look back at what that was or ahead at what is coming. If the app has the present wrong and there is no
/// GPS to correct it (a late departure, a slow cruise), go to where you really are and say "We're here".
struct ScrubBar: View {
    var session: FlightSession
    var board: Board
    @State private var dragging = false

    var body: some View {
        let e = board.estimate
        let dur = max(board.duration, 1)
        let offset = board.viewT - e.state.t
        VStack(spacing: 6) {
            HStack(spacing: 8) {
                if board.live {
                    Text(liveCaption(e)).font(.footnote.weight(.medium)).foregroundStyle(Theme.muted).lineLimit(1)
                    Spacer(minLength: 0)
                } else {
                    Text(lookCaption(offset / max(e.pace, 0.4)))
                        .font(.footnote.weight(.semibold)).foregroundStyle(Theme.ink).lineLimit(1).minimumScaleFactor(0.8)
                    Spacer(minLength: 0)
                    if e.source != .gps {
                        Button("We're here") { session.pinHere() }
                            .buttonStyle(.bordered).tint(Theme.ink).controlSize(.small)
                    }
                    Button("Back to now") { session.goLive() }
                        .buttonStyle(.borderedProminent).tint(Theme.track).controlSize(.small)
                }
            }
            HStack(spacing: 6) {
                step("gobackward.minus", -60, "Back a minute")
                GeometryReader { geo in
                    let w = geo.size.width
                    let x = { (t: Double) in CGFloat(min(1, max(0, t / dur))) * w }
                    ZStack(alignment: .leading) {
                        Capsule().fill(Theme.chip).frame(height: 8)
                        // flown so far
                        Capsule().fill(Theme.track.opacity(0.35)).frame(width: max(8, x(e.state.t)), height: 8)
                        // how unsure the present is
                        if e.sigma > 45 {
                            let a = x(e.state.t - e.sigma), b = x(e.state.t + e.sigma)
                            Capsule().fill(Theme.warn.opacity(0.45)).frame(width: max(4, b - a), height: 14).offset(x: a)
                        }
                        // headline sightings on this side
                        Canvas { ctx, sz in
                            for h in session.model.headlines(board.side) {
                                let px = CGFloat(min(1, max(0, h.main.tBest / dur))) * sz.width
                                ctx.fill(Path(CGRect(x: px - 0.75, y: sz.height / 2 - 9, width: 1.5, height: 4)), with: .color(Theme.side(board.side).opacity(0.8)))
                            }
                        }
                        .allowsHitTesting(false)
                        // the present
                        Image(systemName: "airplane")
                            .font(.system(size: 13, weight: .bold)).foregroundStyle(Theme.track)
                            .offset(x: x(e.state.t) - 8, y: 13)
                        // the moment shown
                        Circle().fill(board.live ? Theme.track : Theme.ink)
                            .frame(width: dragging ? 24 : 18, height: dragging ? 24 : 18)
                            .overlay(Circle().stroke(Theme.paper, lineWidth: 2))
                            .offset(x: x(board.viewT) - (dragging ? 12 : 9))
                    }
                    .frame(height: 40)
                    .contentShape(Rectangle())
                    .gesture(DragGesture(minimumDistance: 0)
                        .onChanged { g in
                            dragging = true
                            session.viewT = Double(min(1, max(0, g.location.x / w))) * dur
                        }
                        .onEnded { _ in
                            dragging = false
                            // let go on the present: follow it again
                            if let t = session.viewT, abs(t - e.state.t) < dur * 0.012 { session.goLive() }
                        })
                }
                .frame(height: 40)
                .accessibilityElement()
                .accessibilityLabel("Flight timeline")
                .accessibilityValue("\(Int(board.viewT / 60)) minutes after takeoff")
                .accessibilityAdjustableAction { d in session.scrub(by: d == .increment ? 60 : -60) }
                step("goforward.plus", 60, "Forward a minute")
            }
        }
        .padding(.horizontal, 12).padding(.top, 8).padding(.bottom, 6)
        .background(Theme.card.shadow(.drop(color: .black.opacity(0.12), radius: 6, y: -2)))
    }

    private func step(_ icon: String, _ by: Double, _ label: String) -> some View {
        Button { session.scrub(by: by) } label: {
            Image(systemName: icon).font(.title3).frame(width: 40, height: 40)
        }
        .buttonRepeatBehavior(.enabled)
        .tint(Theme.ink)
        .accessibilityLabel(label)
    }

    private func liveCaption(_ e: Estimate) -> String {
        let tz = session.pack.flight.orig.tz
        switch e.phase {
        case .beforeDeparture: return "Drag to look through the flight"
        case .landed: return "Drag back to look over the flight"
        case .airborne:
            let o = board.over.map { " · \($0)" } ?? ""
            return "\(Fmt.span(e.state.t)) in · \(Fmt.clock(session.now, tz: tz)) at \(session.pack.flight.orig.code)\(o)"
        }
    }

    private func lookCaption(_ real: Double) -> String {
        if abs(real) < 30 { return "Looking at about now" }
        return real > 0 ? "Looking \(Fmt.span(real)) ahead" : "Looking \(Fmt.span(real)) back"
    }
}
