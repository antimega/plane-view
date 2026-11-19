import CoreLocation
import Foundation
import Observation
import PlaneViewCore
import SwiftUI

/// A rehearsal: one of the pack's tracks flown again as if it were today's flight, at any speed, with the GPS
/// switched off at will. It is how the app is tested without an aircraft, and a way to look through a flight at home.
struct Rehearsal {
    var path: FlightPath
    var rate: Double
    var startedReal: Date
    var startedT: Double          // plan seconds at the start
    var shiftKm = 0.0             // fly this far to the right of the track in the cruise (a routing nobody has flown)
    var gpsOff = false
    var emittedT: Double

    func t(at real: Date) -> Double { startedT + real.timeIntervalSince(startedReal) * rate }

    func truth(_ t: Double) -> AircraftState {
        var s = path.state(at: t)
        if shiftKm != 0 {
            let k = min(1, max(0, (t - 600) / 600)) * min(1, max(0, (path.end - 900 - t) / 600))
            let p = Geo.destination(s.lat, s.lon, bearing: s.course + 90, km: shiftKm * k)
            s.lat = p.lat; s.lon = p.lon
        }
        return s
    }
}

/// One flight open in the app: its pack, where the aircraft is taken to be, and the board being shown.
@MainActor @Observable
final class FlightSession {
    let pack: Pack
    let model: FlightModel
    var board: Board?
    var side: Side { didSet { guard ready else { return }; UserDefaults.standard.set(side.rawValue, forKey: "side." + pack.id); refresh() } }
    /// Plan time being looked at when the user has scrubbed away from the present; nil follows the present.
    var viewT: Double? { didSet { if ready { refresh() } } }
    var rehearsal: Rehearsal?
    var alertsOn: Bool { didSet { guard ready else { return }; UserDefaults.standard.set(alertsOn, forKey: "alerts"); if alertsOn { announcer.askForAlerts() } } }
    var liveActivityOn: Bool { didSet { if ready { UserDefaults.standard.set(liveActivityOn, forKey: "liveActivity") } } }
    var locationStatus: CLAuthorizationStatus = .notDetermined
    var marked: Set<String> = []          // feature ids the user ticked as seen / photographed
    var scheduleVersion = 0               // bumps when sightings along the path have been worked out again
    // slow-changing facts for menus (set only when they change, so an open menu is not rebuilt every second)
    private(set) var hasGPS = false
    private(set) var pinned = false
    private(set) var hasTrack = false

    /// The pictures for sightings: the forecast's, and the app's own for everything it has none for.
    let art: ArtStore
    @ObservationIgnored private var log: TrackLog
    @ObservationIgnored private let feed = LocationFeed()
    @ObservationIgnored private let announcer = Announcer()
    @ObservationIgnored private var timer: Timer?
    @ObservationIgnored private var rebuilding = false
    @ObservationIgnored private var lastSaved = Date.distantPast
    @ObservationIgnored private var started = false
    /// Observed properties run their didSet even from init; nothing should act on them until the session is whole.
    @ObservationIgnored private var ready = false

    init(pack: Pack) {
        self.pack = pack
        model = FlightModel(pack: pack)
        art = ArtStore(pack: pack, spotter: model.spotter)
        let d = UserDefaults.standard
        let seatSide = pack.flight.seat?.side.flatMap { Side(rawValue: $0) }
        side = d.string(forKey: "side." + pack.id).flatMap { Side(rawValue: $0) } ?? seatSide ?? .R
        // (asked through bool(forKey:) so that a launch argument, -liveActivity NO, counts too)
        alertsOn = d.object(forKey: "alerts") == nil ? true : d.bool(forKey: "alerts")
        liveActivityOn = d.object(forKey: "liveActivity") == nil ? true : d.bool(forKey: "liveActivity")
        log = TrackLog(packID: pack.id, replay: false)
        marked = Set(d.stringArray(forKey: "marked." + pack.id) ?? [])
        restore()
        ready = true
    }

    // MARK: - clock

    var rate: Double { rehearsal?.rate ?? 1 }

    /// The session's "now": the wall clock, or the rehearsal's own time.
    var now: Date {
        if let r = rehearsal { return pack.takeoff.addingTimeInterval(r.t(at: Date())) }
        return Date()
    }

    var trackLog: TrackLog { log }

    // MARK: - life cycle

    func start() {
        guard !started else { return }
        started = true
        feed.onFix = { [weak self] fix in Task { @MainActor in self?.take(fix) } }
        feed.onStatus = { [weak self] s in Task { @MainActor in self?.locationStatus = s } }
        locationStatus = feed.status
        if rehearsal == nil { feed.start() }
        if alertsOn { announcer.askForAlerts() }
        let t = Timer(timeInterval: 1, repeats: true) { [weak self] _ in Task { @MainActor in self?.tick() } }
        RunLoop.main.add(t, forMode: .common)
        timer = t
        tick()
    }

    func stop() {
        started = false
        timer?.invalidate()
        timer = nil
        feed.stop()
        announcer.cancelAll()
        save(force: true)
    }

    /// Coming back from Camera or Maps: show the estimate at once, and ask the GPS for a new fix.
    func cameToFront() {
        if rehearsal == nil, started { feed.start() }
        tick()
    }

    func wentToBack() { save(force: true) }

    // MARK: - positions

    private func take(_ fix: Fix) {
        guard rehearsal == nil else { return }
        if model.navigator.ingest(fix) {
            log.add(fix, tau: model.navigator.estimate(at: fix.time).state.t)
        }
    }

    private func tick() {
        if var r = rehearsal {
            let tNow = min(r.t(at: Date()), r.path.end)
            if !r.gpsOff {
                var t = max(r.emittedT + 5, tNow - 60)
                while t <= tNow {
                    let s = r.truth(t), s2 = r.truth(min(r.path.end, t + 5))
                    let v = Geo.distKm(s.lat, s.lon, s2.lat, s2.lon) * 1000 / 5
                    let fix = Fix(time: pack.takeoff.addingTimeInterval(t), lat: s.lat, lon: s.lon, altM: s.altM, speed: v,
                                  course: v > 5 ? Geo.bearing(s.lat, s.lon, s2.lat, s2.lon) : nil, hAcc: 15)
                    if model.navigator.ingest(fix) {
                        log.add(fix, tau: model.navigator.estimate(at: fix.time).state.t, wall: Date())
                    }
                    r.emittedT = t
                    t += 5
                }
            } else {
                r.emittedT = tNow
            }
            rehearsal = r
        }
        if model.scheduleStale, !rebuilding {
            rebuilding = true
            let model = self.model, at = now
            Task.detached(priority: .userInitiated) {
                model.rebuildSchedule(now: at)
                await MainActor.run {
                    self.rebuilding = false
                    self.scheduleVersion += 1
                    self.refresh()
                }
            }
        }
        refresh()
        if let b = board {
            // the Lock Screen and the banners always speak of the present, wherever the scrubber is
            let live = viewT == nil ? b : model.board(side: side, now: now)
            announcer.update(board: live, pack: pack, rate: rate, alerts: alertsOn && rate <= 1.01, liveActivity: liveActivityOn)
        }
        save(force: false)
    }

    private func refresh() {
        HangWatch.mark("session: board")
        let b = model.board(side: side, now: now, viewT: viewT)
        HangWatch.mark("session: board done")
        board = b
        let g = b.estimate.source == .gps, p = model.navigator.pin != nil, t = !log.rows.isEmpty
        if g != hasGPS { hasGPS = g }
        if p != pinned { pinned = p }
        if t != hasTrack { hasTrack = t }
    }

    // MARK: - pictures

    /// Where the aircraft is taken to be at a sighting's best moment, and the time of day then.
    private func vantage(_ best: Sighting) -> (ac: AircraftState, path: FlightPath, unix: Double) {
        let path = model.navigator.active(now: now)
        let est = board?.estimate ?? model.navigator.estimate(at: now)
        let waiting = est.phase == .beforeDeparture && now < pack.takeoff
        let unix = waiting ? pack.flight.takeoff_unix + best.tBest : now.timeIntervalSince1970 + (best.tBest - est.state.t) / max(est.pace, 0.4)
        return (path.state(at: best.tBest), path, unix)
    }

    /// The window view for a sighting (nil for a moment while the app draws one the forecast did not).
    func windowArt(_ best: Sighting, plan: PlanSighting?) -> WindowArt? {
        let v = vantage(best)
        return art.window(best, plan: plan, from: v.ac, unix: v.unix)
    }

    /// The map of a sighting, turned to the way you look at it.
    func mapArt(_ best: Sighting, plan: PlanSighting?, also: [Sighting] = []) -> MapArt? {
        let v = vantage(best)
        return art.map(best, plan: plan, from: v.ac, path: v.path, also: also)
    }

    // MARK: - what the user can tell it

    /// "Wheels up": the flight is at plan time 0 as of `at`.
    func tookOff(at: Date) {
        model.navigator.setPin(Pin(at: at, tau: model.navigator.activeBase.start))
        viewT = nil
        tick()
    }

    /// "We're here": the moment being looked at is the present.
    func pinHere() {
        guard let t = viewT else { return }
        model.navigator.setPin(Pin(at: now, tau: t))
        viewT = nil
        tick()
    }

    func clearPin() {
        model.navigator.setPin(nil)
        tick()
    }

    func scrub(by seconds: Double) {
        guard let b = board else { return }
        let t = (viewT ?? b.estimate.state.t) + seconds
        viewT = min(max(t, 0), b.duration)
    }

    func goLive() { viewT = nil }

    func toggleMark(_ featureID: String) {
        if marked.contains(featureID) { marked.remove(featureID) } else { marked.insert(featureID) }
        UserDefaults.standard.set(Array(marked), forKey: "marked." + pack.id)
    }

    // MARK: - rehearsal

    func startRehearsal(pathID: String, rate: Double, fromT: Double = 0, shiftKm: Double = 0) {
        guard let p = pack.paths.first(where: { $0.id == pathID }) else { return }
        feed.stop()
        announcer.cancelAll()
        model.navigator.reset()
        log = TrackLog(packID: pack.id, replay: true)
        rehearsal = Rehearsal(path: p, rate: rate, startedReal: Date(), startedT: fromT, shiftKm: shiftKm, emittedT: fromT - 5)
        viewT = nil
        tick()
    }

    func setRehearsal(rate: Double? = nil, gpsOff: Bool? = nil) {
        guard var r = rehearsal else { return }
        if let rate {
            r.startedT = r.t(at: Date())
            r.startedReal = Date()
            r.rate = rate
        }
        if let gpsOff { r.gpsOff = gpsOff }
        rehearsal = r
    }

    func endRehearsal() {
        rehearsal = nil
        model.navigator.reset()
        log = TrackLog(packID: pack.id, replay: false)
        restore()
        viewT = nil
        if started { feed.start() }
        tick()
    }

    // MARK: - memory (the app may be put away, or thrown out, while you are in Camera)

    private var memoryURL: URL {
        let dir = URL.applicationSupportDirectory.appendingPathComponent("Sessions", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent(pack.id + ".json")
    }

    private func save(force: Bool) {
        guard rehearsal == nil, force || Date().timeIntervalSince(lastSaved) > 10 else { return }
        lastSaved = Date()
        if let d = try? JSONEncoder().encode(model.navigator.memory()) { try? d.write(to: memoryURL, options: .atomic) }
    }

    private func restore() {
        guard let d = try? Data(contentsOf: memoryURL), let m = try? JSONDecoder().decode(NavigatorMemory.self, from: d) else { return }
        // only if it belongs to this flight: an anchor from within a day of the planned takeoff
        let anchor = [m.lastFix?.time, m.pin?.at].compactMap { $0 }.max()
        guard let a = anchor, abs(a.timeIntervalSince(pack.takeoff)) < 36 * 3600, Date().timeIntervalSince(a) < 24 * 3600 else { return }
        model.navigator.restore(m)
    }

    /// Forget this flight's positions and anchors (wrong flight, or starting again).
    func forget() {
        model.navigator.reset()
        log.clear()
        try? FileManager.default.removeItem(at: memoryURL)
        tick()
    }
}
