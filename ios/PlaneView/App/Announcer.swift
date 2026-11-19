import ActivityKit
import Foundation
import PlaneViewCore
import UserNotifications

/// Keeps you told while the app is not in front: a Live Activity with the next sighting and a countdown, and a
/// banner a little before each headline sighting. Both are local; nothing is sent anywhere.
@MainActor
final class Announcer {
    private var activity: Activity<SpottingAttributes>?
    private var lastState: SpottingAttributes.ContentState?
    private var lastPush = Date.distantPast
    private var scheduled: [String: Date] = [:]        // sighting id -> when its banner fires
    private var askedPermission = false
    private static let queue = DispatchQueue(label: "planeview.notifications", qos: .utility)

    /// The notification centre answers over XPC and can take its time: never wait for it on the main thread.
    private func remove(_ ids: [String]) {
        guard !ids.isEmpty else { return }
        Self.queue.async { UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: ids) }
    }

    static let leadSeconds = 75.0
    static let horizonSeconds = 45.0 * 60

    func askForAlerts() {
        guard !askedPermission else { return }
        askedPermission = true
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    /// `rate` is how fast the session's clock runs against the wall clock (1 except in a sped-up rehearsal).
    func update(board: Board, pack: Pack, rate: Double, alerts: Bool, liveActivity: Bool) {
        let est = board.estimate
        let side = board.side
        let next = board.next.first { $0.eta > 0 }
        let inView = board.now.filter { !$0.maybe }.prefix(3).map { pack.features[$0.sighting.feature].name }
        var state = SpottingAttributes.ContentState(nextName: nil, nextWhat: nil, nextWhere: nil, nextAt: nil,
                                                    inView: Array(inView), position: Self.positionText(est), uncertain: est.source != .gps)
        if let n = next, est.phase == .airborne {
            let f = pack.features[n.headline.main.feature]
            let w = whereWords(side: side, fa: n.headline.main.fa, dep: n.headline.main.dep)
            state.nextName = f.name
            state.nextWhat = f.what
            state.nextWhere = "\(w.clock) · \(w.words)"
            state.nextAt = Date().addingTimeInterval(n.eta / rate)
        } else if est.phase == .beforeDeparture, let n = board.next.first {
            state.nextName = pack.features[n.headline.main.feature].name
            state.nextWhere = "first after takeoff"
        }
        pushActivity(state, pack: pack, side: side, on: liveActivity && est.phase != .landed)
        scheduleBanners(board: board, pack: pack, rate: rate, on: alerts && est.phase == .airborne)
    }

    static func positionText(_ e: Estimate) -> String {
        switch e.source {
        case .gps: return "GPS"
        case .deadReckoning: return "Estimated ±\(max(1, Int((e.sigma / 60).rounded()))) min"
        case .pinned: return "Set by you ±\(max(1, Int((e.sigma / 60).rounded()))) min"
        case .schedule: return e.phase == .beforeDeparture ? "Before takeoff" : "Timetable only"
        }
    }

    private func pushActivity(_ state: SpottingAttributes.ContentState, pack: Pack, side: Side, on: Bool) {
        guard on, ActivityAuthorizationInfo().areActivitiesEnabled else {
            end()
            return
        }
        if let a = activity, a.attributes.side != side.rawValue {
            Task { await a.end(nil, dismissalPolicy: .immediate) }
            activity = nil
        }
        // the countdown runs by itself: only a change of content, or a shift of more than 20 s, needs sending
        var changed = lastState == nil || lastState!.nextName != state.nextName || lastState!.inView != state.inView
            || lastState!.position != state.position
        if let a = lastState?.nextAt, let b = state.nextAt, abs(a.timeIntervalSince(b)) > 20 { changed = true }
        guard changed, activity == nil || Date().timeIntervalSince(lastPush) > 4 else { return }
        lastState = state
        lastPush = Date()
        let content = ActivityContent(state: state, staleDate: Date().addingTimeInterval(20 * 60))
        if let a = activity {
            Task { await a.update(content) }
        } else {
            let attr = SpottingAttributes(flight: pack.flight.title, route: pack.flight.subtitle, side: side.rawValue)
            activity = try? Activity.request(attributes: attr, content: content, pushType: nil)
        }
    }

    func end() {
        if let a = activity {
            Task { await a.end(nil, dismissalPolicy: .immediate) }
        }
        activity = nil
        lastState = nil
    }

    private func scheduleBanners(board: Board, pack: Pack, rate: Double, on: Bool) {
        guard on else {
            remove(Array(scheduled.keys))
            scheduled = [:]
            return
        }
        var keep = Set<String>()
        for n in board.next.prefix(8) where n.eta > 0 && n.eta < Self.horizonSeconds {
            let id = "sighting-\(n.id)"
            let fire = Date().addingTimeInterval(max(1, (n.eta - Self.leadSeconds) / rate))
            keep.insert(id)
            if let had = scheduled[id], abs(had.timeIntervalSince(fire)) < 20 { continue }
            if n.eta < 20 { continue }          // too late to be worth a banner: it is on the list
            let f = pack.features[n.headline.main.feature]
            let w = whereWords(side: board.side, fa: n.headline.main.fa, dep: n.headline.main.dep)
            let c = UNMutableNotificationContent()
            let lead = Int((min(n.eta, Self.leadSeconds) / 60).rounded())
            c.title = lead >= 1 ? "\(f.name) in about \(lead == 1 ? "a minute" : "\(lead) minutes")" : "\(f.name) coming into view"
            c.body = "\(board.side == .L ? "Left" : "Right") window, \(w.clock): \(w.words). \(Int(n.headline.main.dKm.rounded())) km away"
                + (f.what.map { " · \($0)" } ?? "")
            c.sound = .default
            c.threadIdentifier = "sightings"
            let trig = UNTimeIntervalNotificationTrigger(timeInterval: max(1, fire.timeIntervalSinceNow), repeats: false)
            let req = UNNotificationRequest(identifier: id, content: c, trigger: trig)
            Self.queue.async { UNUserNotificationCenter.current().add(req) }
            scheduled[id] = fire
        }
        let gone = scheduled.keys.filter { !keep.contains($0) && scheduled[$0]! > Date() }
        remove(gone)
        for g in gone { scheduled[g] = nil }
        scheduled = scheduled.filter { $0.value > Date().addingTimeInterval(-600) }
    }

    func cancelAll() {
        end()
        remove(Array(scheduled.keys))
        scheduled = [:]
    }
}
