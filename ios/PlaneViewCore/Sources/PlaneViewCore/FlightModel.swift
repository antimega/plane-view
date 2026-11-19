import Foundation

/// One line of the "in view" list.
public struct NowRow: Sendable, Identifiable {
    public var sighting: Sighting        // direction and distance as of the moment shown
    public var remaining: Double?        // seconds (real time) it should stay in view; nil when not known
    public var maybe: Bool               // listed only because the position is uncertain: it may have gone, or be coming
    public var plan: PlanSighting?       // the forecast's entry, when it has pictures
    public var id: String { sighting.id }
}

/// One line of "coming up" or "passed".
public struct NextRow: Sendable, Identifiable {
    public var headline: Headline
    public var eta: Double               // seconds of real time until it comes into view (negative: since it went)
    public var etaBest: Double           // ... until the best view
    public var plan: PlanSighting?
    public var id: String { headline.id }
}

/// One headline sighting on the flight's timeline, wherever it stands against the moment shown: gone, in view,
/// or still to come. The Window screen is this list, in order, with one of them in focus.
public struct TimelineRow: Sendable, Identifiable {
    public enum State: Sendable { case passed, inView, coming }
    public var headline: Headline
    public var plan: PlanSighting?
    public var state: State
    public var start: Double             // seconds of real time until it comes into view (negative: since it did)
    public var best: Double              // ... until the best view
    public var end: Double               // ... until it goes out of view (negative: since it went)
    public var live: Sighting?           // direction and distance as of the moment shown, while it is in view
    public var maybe: Bool               // the position is uncertain enough that it may be in view already, or still
    public var id: String { headline.id }
    /// Where to point: as it is now when in view, otherwise as it will be (or was) at its best.
    public var look: Sighting { live ?? headline.main }
}

public struct ContextRow: Sendable, Identifiable {
    public var item: ContextItem
    public var eta: Double
    public var id: String { item.id }
}

/// Everything a screen needs for one moment of the flight and one window.
public struct Board: Sendable {
    public var estimate: Estimate
    public var side: Side
    public var live: Bool                // showing the present (as estimated), not a moment the user scrubbed to
    public var viewT: Double             // plan time shown, seconds after takeoff
    public var state: AircraftState      // where the aircraft is taken to be at that moment
    public var unix: Double              // the time of day there and then
    public var light: Light
    public var now: [NowRow]
    public var next: [NextRow]
    public var passed: [NextRow]
    public var timeline: [TimelineRow]   // every headline on this side, in the order they come
    public var focus: Int?               // the one to look at: the in-view one nearest its best, else the next to come
    public var context: [ContextRow]     // coast and border crossings, top of descent ... still ahead
    public var over: String?             // "Over France", "Beside the Bay of Biscay"
    public var duration: Double          // plan time of landing
    public var scheduleReady: Bool
}

/// The flight in progress: the navigator's estimate, the sightings along the path being flown, and the board for
/// any moment. Sightings along the whole path are worked out off the main thread and replaced when the path moves.
public final class FlightModel: @unchecked Sendable {
    public let pack: Pack
    public let navigator: Navigator
    public let spotter: Spotter
    private var sightings: [Sighting] = []
    private var heads: [Side: [Headline]] = [:]
    private var builtGeneration = -1
    private var builtPath: FlightPath?
    private var planIndex: [Side: [Int: PlanSighting]] = [.L: [:], .R: [:]]
    private let lock = NSLock()

    public init(pack: Pack) {
        self.pack = pack
        navigator = Navigator(pack: pack)
        spotter = Spotter(features: pack.features, view: pack.manifest.view, terrain: pack.terrain, wing: pack.flight.seat?.wing)
        var byID: [String: Int] = [:]
        for (i, f) in pack.features.enumerated() { byID[f.id] = i }
        for sd in Side.allCases {
            for (id, e) in pack.plan[sd] ?? [:] {
                if let i = byID[id] { planIndex[sd]?[i] = e }
            }
        }
    }

    /// True when the sightings were worked out for a path the aircraft is no longer taken to be on.
    public var scheduleStale: Bool {
        lock.lock(); defer { lock.unlock() }
        return builtGeneration != navigator.generation
    }

    /// Work out the sightings along the path being flown (a second or so; call off the main thread).
    public func rebuildSchedule(now: Date) {
        let gen = navigator.generation
        let path = navigator.active(now: now)
        let est = navigator.estimate(at: now)
        // before the flight has begun, the time of day along it is the planned one - not tonight's, which would
        // have a morning flight looked through in the dark
        let waiting = est.phase == .beforeDeparture && now < pack.takeoff
        let t0 = waiting ? 0 : est.state.t, u0 = waiting ? pack.flight.takeoff_unix : now.timeIntervalSince1970, pace = max(est.pace, 0.4)
        let all = spotter.schedule(along: path, unixAt: { u0 + ($0 - t0) / pace })
        var h: [Side: [Headline]] = [:]
        for sd in Side.allCases {
            h[sd] = spotter.headlines(all, side: sd, preferred: Set(planIndex[sd]?.keys.map { $0 } ?? []))
        }
        lock.lock()
        sightings = all; heads = h; builtGeneration = gen; builtPath = path
        lock.unlock()
    }

    public func headlines(_ side: Side) -> [Headline] {
        lock.lock(); defer { lock.unlock() }
        return heads[side] ?? []
    }

    public func plan(for s: Sighting) -> PlanSighting? { planIndex[s.side]?[s.feature] }

    /// The board for `now`, or for plan time `viewT` when the user has scrubbed away from the present.
    public func board(side: Side, now: Date, viewT: Double? = nil) -> Board {
        let est = navigator.estimate(at: now)
        lock.lock()
        let all = sightings, hl = heads[side] ?? [], ready = builtGeneration >= 0
        let path = builtPath ?? navigator.activeBase
        lock.unlock()
        let live = viewT == nil
        let pace = max(est.pace, 0.4)
        let T = min(max(viewT ?? est.state.t, path.start), path.end)
        let state = live ? est.state : path.state(at: T)
        let waiting = est.phase == .beforeDeparture && now < pack.takeoff
        let unix = waiting ? pack.flight.takeoff_unix + T : now.timeIntervalSince1970 + (T - est.state.t) / pace
        let sigma = live ? est.sigma : 0
        let airborne = !live || est.phase == .airborne

        // in view from there, by how much it deserves a look
        var rows: [NowRow] = []
        var listed = Set<Int>()
        if airborne {
            let mine = all.filter { $0.side == side }
            var window: [Int: Sighting] = [:]
            for s in mine { window[s.feature] = s }
            var vis = spotter.visible(from: state, unix: unix).filter { $0.side == side && !pack.features[$0.feature].broad }
            for i in vis.indices { vis[i].score = spotter.score(vis[i]) * (planIndex[side]?[vis[i].feature] != nil ? 1.35 : 1) }
            vis.sort { $0.score > $1.score }
            var names = Set<String>()
            for s in vis {
                let name = pack.features[s.feature].name.lowercased()
                if names.contains(name) { continue }
                names.insert(name)
                listed.insert(s.feature)
                let rem = window[s.feature].map { max(0, ($0.t1 - T) / pace) }
                rows.append(NowRow(sighting: s, remaining: rem, maybe: false, plan: planIndex[side]?[s.feature]))
            }
            if sigma > 45 {
                // the position may be minutes out: what is in view a little earlier or later belongs on the list too
                for h in hl where !listed.contains(h.main.feature) && h.main.t0 <= T + sigma && h.main.t1 >= T - sigma {
                    listed.insert(h.main.feature)
                    rows.append(NowRow(sighting: h.main, remaining: nil, maybe: true, plan: planIndex[side]?[h.main.feature]))
                }
            }
        }

        // the whole flight in order, for the carousel
        var liveNow: [Int: Sighting] = [:]
        for r in rows where !r.maybe { liveNow[r.sighting.feature] = r.sighting }
        var timeline: [TimelineRow] = []
        for h in hl {
            let m = h.main
            let live = liveNow[m.feature]
            let start = (m.t0 - T) / pace, end = (m.t1 - T) / pace
            let state: TimelineRow.State = !airborne ? .coming : live != nil ? .inView : end < 0 ? .passed : start > 0 ? .coming : .inView
            let maybe = airborne && live == nil && sigma > 45 && m.t0 <= T + sigma && m.t1 >= T - sigma
            timeline.append(TimelineRow(headline: h, plan: planIndex[side]?[m.feature], state: state, start: start,
                                        best: (m.tBest - T) / pace, end: end, live: live, maybe: maybe))
        }
        var focus: Int? = nil
        let inView = timeline.indices.filter { timeline[$0].state == .inView }
        if let i = inView.min(by: { abs(timeline[$0].best) < abs(timeline[$1].best) }) {
            focus = i
        } else if let i = timeline.indices.filter({ timeline[$0].state == .coming }).min(by: { timeline[$0].start < timeline[$1].start }) {
            focus = i
        } else if !timeline.isEmpty {
            focus = timeline.count - 1
        }

        var next: [NextRow] = [], passed: [NextRow] = []
        for h in hl where !listed.contains(h.main.feature) {
            let row = NextRow(headline: h, eta: (h.main.t0 - T) / pace, etaBest: (h.main.tBest - T) / pace,
                              plan: planIndex[side]?[h.main.feature])
            if h.main.t1 < T { passed.append(row) } else if h.main.t0 > T || !airborne { next.append(row) }
        }
        next.sort { $0.eta < $1.eta }
        passed.sort { $0.headline.main.t1 > $1.headline.main.t1 }

        var ctx: [ContextRow] = []
        var over: String? = nil
        for c in pack.manifest.context {
            if let sd = c.side, sd != side.rawValue { continue }
            let t = c.t * 60
            if c.type == "sun_side" { continue }
            if t > T { ctx.append(ContextRow(item: c, eta: (t - T) / pace)) }
            else if c.type == "country" || c.type == "sea" || c.type == "coast_in" || c.type == "coast_out" {
                if c.type == "sea", let e = c.t_end, e * 60 < T { continue }
                over = c.text
            }
        }
        let sunEl = Geo.sun(unix: unix, lat: state.lat, lon: state.lon).el
        return Board(estimate: est, side: side, live: live, viewT: T, state: state, unix: unix, light: Light(sunEl: sunEl),
                     now: rows, next: next, passed: passed, timeline: timeline, focus: focus, context: ctx, over: over, duration: path.end, scheduleReady: ready)
    }
}
