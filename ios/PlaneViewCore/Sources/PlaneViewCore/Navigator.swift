import Foundation

/// One position from the phone (or from a replay).
public struct Fix: Codable, Sendable, Equatable {
    public var time: Date
    public var lat: Double
    public var lon: Double
    public var altM: Double?
    public var speed: Double?      // m/s over the ground
    public var course: Double?     // degrees true
    public var hAcc: Double        // metres

    public init(time: Date, lat: Double, lon: Double, altM: Double? = nil, speed: Double? = nil, course: Double? = nil, hAcc: Double = 20) {
        self.time = time; self.lat = lat; self.lon = lon; self.altM = altM; self.speed = speed; self.course = course; self.hAcc = hAcc
    }
}

public enum PositionSource: String, Codable, Sendable {
    case gps            // a fix a few seconds old
    case deadReckoning  // carried forward along the route from the last fix
    case pinned         // the user said "we are here" (or "wheels up")
    case schedule       // nothing but the timetable
}

public enum FlightPhase: String, Codable, Sendable {
    case beforeDeparture, airborne, landed
}

/// The app's best account of where the aircraft is, and how sure it is.
public struct Estimate: Sendable {
    public var state: AircraftState
    public var sigma: Double          // how far out the plan time may be, seconds
    public var source: PositionSource
    public var fixAge: Double?        // seconds since the last usable fix
    public var pace: Double           // plan seconds covered per real second (below 1: running slow)
    public var pathID: String         // the known route or past flight the position is matched to
    public var crossKm: Double        // how far the last fix was from it (+ right)
    public var nearestKm: Double      // how far from the nearest of all known routes
    public var offRoute: Bool         // further out than past flights spread
    public var outsideReach: Bool     // beyond the area the pack's features cover
    public var phase: FlightPhase
    public var speedMS: Double
}

/// A user-set anchor: at `at`, the flight was at plan time `tau`.
public struct Pin: Codable, Sendable, Equatable {
    public var at: Date
    public var tau: Double
    public init(at: Date, tau: Double) { self.at = at; self.tau = tau }
}

/// What must survive the app being put away: enough to carry on from disk with no fix.
public struct NavigatorMemory: Codable, Sendable {
    public var lastFix: Fix?
    public var fixTau: Double
    public var fixCross: Double
    public var fixAltOffset: Double
    public var pace: Double
    public var activeID: String?
    public var pin: Pin?
    public var cost: [String: Double]
    public var flown: [Int: [Double]]
    public var nearestKm: Double
}

/// Works out where the aircraft is on the flight from whatever it has: fresh GPS, an old fix carried forward, a
/// pin, or the timetable. It matches fixes against every likely route and every past flight in the pack, so a
/// flight on the second-likeliest routing - or on one flown once last winter - is followed on that one.
public final class Navigator: @unchecked Sendable {
    public let pack: Pack
    public private(set) var lastFix: Fix?
    public private(set) var pin: Pin?
    public private(set) var activeIndex: Int
    public private(set) var pace: Double = 1
    /// Bumped whenever the active path's shape changes enough that sightings along it should be worked out again.
    public private(set) var generation = 0

    private var cost: [Double]              // per known path: smoothed squared misfit of recent fixes
    private var hint: [Double?]
    private var fixTau = 0.0, fixCross = 0.0, fixAltOffset = 0.0, nearestKm = 0.0
    private var lastMatch: Date?
    private var builtCross = 0.0, builtAt: Date?, builtIndex = -1, builtTau = 0.0
    private var flown: [Int: [Double]] = [:]       // plan-time bucket (15 s) -> lat, lon, alt
    private var cachedActive: FlightPath?
    private let lock = NSRecursiveLock()

    static let sigmaKm = 5.0
    static let strayKm = 400.0
    /// Set when the last fix offered was too far from every route to be this flight (km from the nearest).
    public private(set) var strayKm: Double?
    static let freshSeconds = 20.0

    public init(pack: Pack) {
        self.pack = pack
        cost = [Double](repeating: 0, count: pack.paths.count)
        hint = [Double?](repeating: nil, count: pack.paths.count)
        activeIndex = pack.paths.firstIndex { $0.kind == "variant" } ?? 0
    }

    public var activeBase: FlightPath { pack.paths[activeIndex] }

    // MARK: - taking fixes

    /// Take a fix. Returns false when it is unusable (too vague, or older than the one already held).
    @discardableResult
    public func ingest(_ fix: Fix) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard fix.hAcc >= 0, fix.hAcc <= 5000, abs(fix.lat) <= 90 else { return false }
        if let l = lastFix, fix.time <= l.time { return false }
        let dt = lastFix.map { fix.time.timeIntervalSince($0.time) } ?? 1e9
        let prevTau = fixTau, hadFix = lastFix != nil

        // match against every known path (at most every 2 s; in between only the active one is followed)
        let full = lastMatch == nil || fix.time.timeIntervalSince(lastMatch!) >= 2
        let moving = (fix.speed ?? 0) > 50 && fix.course != nil && fix.course! >= 0
        var found: [(i: Int, pr: FlightPath.Projection)] = []
        for i in pack.paths.indices where full || i == activeIndex {
            let expect = hint[i].map { $0 + pace * min(dt, 3600) }
            found.append((i, pack.paths[i].project(lat: fix.lat, lon: fix.lon, hint: expect)))
        }
        if full {
            let nearest = found.map { $0.pr.distKm }.min() ?? 0
            // a fix nowhere near any route of this flight (the phone is at home, or the wrong flight is open)
            // says nothing about where the aircraft is
            if nearest > Navigator.strayKm {
                strayKm = nearest
                return false
            }
            strayKm = nil
            nearestKm = nearest
        }
        for (i, pr) in found {
            hint[i] = pr.t
            guard full else { continue }
            var c = (pr.distKm / Navigator.sigmaKm) * (pr.distKm / Navigator.sigmaKm)
            if moving {
                let dc = min(90, abs(Geo.wrap180(fix.course! - pack.paths[i].state(at: pr.t).course)))
                c += (dc / 25) * (dc / 25)
            }
            let k = lastMatch == nil ? 1 : 1 - exp(-min(dt, 600) / 180)
            cost[i] += (c - cost[i]) * k
        }
        if full {
            lastMatch = fix.time
            var best = activeIndex
            var bestScore = score(activeIndex) + 0.5      // stay on the present one unless another is clearly better
            for i in pack.paths.indices {
                let s = score(i)
                if s > bestScore { bestScore = s; best = i }
            }
            if best != activeIndex { activeIndex = best }
        }
        let base = pack.paths[activeIndex]
        let pr = base.project(lat: fix.lat, lon: fix.lon, hint: hint[activeIndex])
        fixTau = pr.t
        fixCross = pr.crossKm
        let planAlt = base.state(at: pr.t).altM
        fixAltOffset = (fix.altM ?? planAlt) - planAlt

        // pace: ground speed against the plan's here, else progress between fixes
        let planSpeed = base.speed(at: pr.t)
        var newPace: Double? = nil
        if let v = fix.speed, v >= 0, planSpeed > 60 {
            newPace = v > 40 ? v / planSpeed : nil
        } else if hadFix, dt > 30, dt < 1800 {
            newPace = (pr.t - prevTau) / dt
        }
        if let np = newPace {
            let clamped = min(1.5, max(0.4, np))
            let k = hadFix ? 1 - exp(-min(dt, 300) / 45) : 1
            pace += (clamped - pace) * k
        }
        lastFix = fix
        flown[Int((pr.t / 15).rounded())] = [fix.lat, fix.lon, fix.altM ?? planAlt]

        // is the path the sightings were worked out along still the path we are on?
        // (also every 90 s regardless: the next few minutes follow the present course, and the past is the log)
        if builtIndex != activeIndex || abs(fixCross - builtCross) > 3 || builtAt == nil
            || fix.time.timeIntervalSince(builtAt!) > 90
            || abs((fixTau - builtTau) - pace * fix.time.timeIntervalSince(builtAt!)) > 60 {
            invalidate()
        }
        return true
    }

    private func score(_ i: Int) -> Double {
        -0.5 * cost[i] + 0.3 * log(max(pack.paths[i].weight, 1e-4))
    }

    private func invalidate() {
        cachedActive = nil
        generation += 1
    }

    /// "We are here": anchor the flight at plan time `tau` as of `at`. Used for "wheels up" (tau 0) and for the
    /// scrubber's "I'm here" when there is no GPS. A later fix overrides it.
    public func setPin(_ p: Pin?) {
        lock.lock(); defer { lock.unlock() }
        pin = p
        invalidate()
    }

    /// Forget the fixes (a new replay, or the wrong flight was open).
    public func reset() {
        lock.lock(); defer { lock.unlock() }
        lastFix = nil; pin = nil; pace = 1; flown = [:]; lastMatch = nil
        cost = [Double](repeating: 0, count: pack.paths.count)
        hint = [Double?](repeating: nil, count: pack.paths.count)
        activeIndex = pack.paths.firstIndex { $0.kind == "variant" } ?? 0
        builtAt = nil
        invalidate()
    }

    // MARK: - the estimate

    public func estimate(at now: Date) -> Estimate {
        lock.lock(); defer { lock.unlock() }
        let base = pack.paths[activeIndex]
        let fl = pack.flight
        func make(_ s: AircraftState, sigma: Double, source: PositionSource, age: Double?, phase: FlightPhase, speed: Double) -> Estimate {
            Estimate(state: s, sigma: sigma, source: source, fixAge: age, pace: pace, pathID: base.id,
                     crossKm: lastFix == nil ? 0 : fixCross, nearestKm: lastFix == nil ? 0 : nearestKm,
                     offRoute: lastFix != nil && nearestKm > max(15, 1.5 * base.spreadKm),
                     outsideReach: !inReach(s.lat, s.lon), phase: phase, speedMS: speed)
        }
        let fixIsNewest = lastFix != nil && (pin == nil || lastFix!.time >= pin!.at)
        if fixIsNewest, let fix = lastFix {
            let age = max(0, now.timeIntervalSince(fix.time))
            let slow = (fix.speed ?? 100) < 40
            let nearOrig = Geo.distKm(fix.lat, fix.lon, fl.orig.lat, fl.orig.lon), nearDest = Geo.distKm(fix.lat, fix.lon, fl.dest.lat, fl.dest.lon)
            // not flying: at the gate, taxiing, or still on the way to the airport
            if slow, fixTau < base.start + 900, nearOrig < 80, nearOrig < nearDest {
                var s = base.state(at: base.start)
                s.lat = fix.lat; s.lon = fix.lon
                return make(s, sigma: 0, source: age <= Navigator.freshSeconds ? .gps : .deadReckoning, age: age, phase: .beforeDeparture, speed: fix.speed ?? 0)
            }
            if slow, fixTau > base.end - 900, nearDest < 80 {
                var s = base.state(at: base.end)
                s.lat = fix.lat; s.lon = fix.lon
                return make(s, sigma: 0, source: age <= Navigator.freshSeconds ? .gps : .deadReckoning, age: age, phase: .landed, speed: fix.speed ?? 0)
            }
            let tau = fixTau + pace * age
            let planSpeed = base.speed(at: min(tau, base.end))
            if age <= Navigator.freshSeconds {
                let plan = base.state(at: fixTau)
                let crs = (fix.course ?? -1) >= 0 && (fix.speed ?? 0) > 30 ? fix.course! : plan.course
                // carried forward the few seconds since the fix, so the picture does not lag
                let p = Geo.destination(fix.lat, fix.lon, bearing: crs, km: (fix.speed ?? planSpeed) * age / 1000)
                let s = AircraftState(t: min(tau, base.end), lat: p.lat, lon: p.lon, altM: fix.altM ?? plan.altM, course: crs)
                return make(s, sigma: 5, source: .gps, age: age, phase: .airborne, speed: fix.speed ?? planSpeed)
            }
            if tau >= base.end {
                return make(base.state(at: base.end), sigma: 10 + 0.08 * age, source: .deadReckoning, age: age, phase: .landed, speed: 0)
            }
            let s = active(now: now).state(at: tau)
            return make(s, sigma: 10 + 0.08 * age * pace, source: .deadReckoning, age: age, phase: .airborne, speed: planSpeed * pace)
        }
        if let pin {
            let age = max(0, now.timeIntervalSince(pin.at))
            let tau = pin.tau + age
            if tau >= base.end {
                return make(base.state(at: base.end), sigma: 120 + 0.06 * age, source: .pinned, age: lastFix.map { now.timeIntervalSince($0.time) }, phase: .landed, speed: 0)
            }
            return make(base.state(at: tau), sigma: 60 + 0.06 * age, source: .pinned, age: lastFix.map { now.timeIntervalSince($0.time) },
                        phase: .airborne, speed: base.speed(at: tau))
        }
        // nothing but the timetable: departures run late more often than early, and by an unknown amount
        let tau = now.timeIntervalSince(pack.takeoff)
        if tau < 0 {
            return make(base.state(at: base.start), sigma: 0, source: .schedule, age: nil, phase: .beforeDeparture, speed: 0)
        }
        if tau >= base.end {
            return make(base.state(at: base.end), sigma: 900, source: .schedule, age: nil, phase: .landed, speed: 0)
        }
        return make(base.state(at: tau), sigma: 600 + 0.06 * tau, source: .schedule, age: nil, phase: .airborne, speed: base.speed(at: tau))
    }

    // MARK: - the path being flown

    /// The matched route bent through where the aircraft actually is: the past is what the GPS logged (where it
    /// did), the next few minutes follow the present course, and after that the offset from the known route
    /// eases away. Sightings for the whole flight are worked out along this.
    public func active(now: Date) -> FlightPath {
        lock.lock(); defer { lock.unlock() }
        if let c = cachedActive { return c }
        let base = pack.paths[activeIndex]
        guard let fix = lastFix, pin == nil || fix.time >= pin!.at else {
            builtIndex = activeIndex; builtCross = 0; builtAt = now; builtTau = fixTau
            cachedActive = base
            return base
        }
        let x0 = fixCross, dz = fixAltOffset, tau0 = fixTau
        let ease = max(80, 5 * abs(x0))                       // km over which the offset fades
        let age = max(0, now.timeIntervalSince(fix.time))
        let hold = age <= 120 && (fix.speed ?? 0) > 50 && (fix.course ?? -1) >= 0
        var pts: [AircraftState] = []
        for t in base.sampleTimes() {
            var s = base.state(at: t)
            if let f = flown[Int((t / 15).rounded())], t <= tau0 + 8 {
                s.lat = f[0]; s.lon = f[1]; s.altM = f[2]
            } else {
                let km = abs(t - tau0) * max(base.speed(at: t), 60) / 1000
                let x = x0 * exp(-km / (t >= tau0 ? ease : 300))
                if abs(x) > 0.05 {
                    let p = Geo.destination(s.lat, s.lon, bearing: s.course + 90, km: x)
                    s.lat = p.lat; s.lon = p.lon
                }
                if t >= tau0 {
                    s.altM = max(0, s.altM + dz * exp(-(t - tau0) / 300))
                    if hold {
                        // straight on for now: the bend back to the route is a guess, the present course is not
                        let w = exp(-(t - tau0) / 240)
                        if w > 0.02 {
                            let ahead = Geo.destination(fix.lat, fix.lon, bearing: fix.course!, km: fix.speed! * ((t - tau0) / max(pace, 0.4)) / 1000)
                            let p = Geo.between(s.lat, s.lon, ahead.lat, ahead.lon, w)
                            s.lat = p.lat; s.lon = p.lon
                        }
                    }
                }
            }
            pts.append(s)
        }
        // the fix itself is a point of the path
        if tau0 > base.start, tau0 < base.end {
            let s = AircraftState(t: tau0, lat: fix.lat, lon: fix.lon, altM: fix.altM ?? base.state(at: tau0).altM, course: 0)
            if let k = pts.firstIndex(where: { $0.t >= tau0 }) {
                if abs(pts[k].t - tau0) < 1 { pts[k] = s } else { pts.insert(s, at: k) }
            }
        }
        let path = FlightPath(id: "active:" + base.id, kind: "active", label: base.label, spreadKm: base.spreadKm, points: pts)
        builtIndex = activeIndex; builtCross = x0; builtAt = fix.time; builtTau = tau0
        cachedActive = path
        return path
    }

    /// The share of belief in each likely route, given the fixes so far (the forecast's shares before any).
    public func routeShares() -> [(id: String, label: String?, share: Double)] {
        lock.lock(); defer { lock.unlock() }
        let idx = pack.paths.indices.filter { pack.paths[$0].kind == "variant" }
        guard !idx.isEmpty else { return [] }
        let logs = idx.map { (lastFix == nil ? 0 : -0.5 * cost[$0]) + log(max(pack.paths[$0].weight, 1e-4)) }
        let m = logs.max()!
        let w = logs.map { exp($0 - m) }
        let sum = w.reduce(0, +)
        return zip(idx, w).map { (pack.paths[$0].id, pack.paths[$0].label, $1 / sum) }.sorted { $0.share > $1.share }
    }

    private func inReach(_ lat: Double, _ lon: Double) -> Bool {
        for ring in pack.manifest.corridor.reach where pointInRing(lat, lon, ring) { return true }
        return pack.manifest.corridor.reach.isEmpty
    }

    private func pointInRing(_ lat: Double, _ lon: Double, _ ring: [[Double]]) -> Bool {
        var inside = false
        var j = ring.count - 1
        for i in 0..<ring.count {
            let (yi, xi) = (ring[i][0], ring[i][1]), (yj, xj) = (ring[j][0], ring[j][1])
            if (yi > lat) != (yj > lat), lon < (xj - xi) * (lat - yi) / (yj - yi) + xi { inside.toggle() }
            j = i
        }
        return inside
    }

    // MARK: - memory

    public func memory() -> NavigatorMemory {
        lock.lock(); defer { lock.unlock() }
        var c: [String: Double] = [:]
        for (i, p) in pack.paths.enumerated() { c[p.id] = cost[i] }
        return NavigatorMemory(lastFix: lastFix, fixTau: fixTau, fixCross: fixCross, fixAltOffset: fixAltOffset, pace: pace,
                               activeID: activeBase.id, pin: pin, cost: c, flown: flown, nearestKm: nearestKm)
    }

    public func restore(_ m: NavigatorMemory) {
        lock.lock(); defer { lock.unlock() }
        lastFix = m.lastFix; fixTau = m.fixTau; fixCross = m.fixCross; fixAltOffset = m.fixAltOffset
        pace = m.pace; pin = m.pin; flown = m.flown; nearestKm = m.nearestKm
        for (i, p) in pack.paths.enumerated() {
            cost[i] = m.cost[p.id] ?? 0
            if p.id == m.activeID { activeIndex = i }
        }
        if lastFix != nil { hint[activeIndex] = fixTau }
        lastMatch = nil
        builtAt = nil
        invalidate()
    }
}
