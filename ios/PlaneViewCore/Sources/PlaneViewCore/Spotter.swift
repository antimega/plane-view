import Foundation

/// One thing in view from one side, at its best moment along a path (or right now).
public struct Sighting: Sendable, Identifiable {
    public var feature: Int
    public var side: Side
    public var tBest: Double      // plan time (s) of the best view
    public var t0: Double         // first and last plan time it is in view
    public var t1: Double
    public var q: Double          // quality of the view, 0-1 (the forecaster's measure)
    public var fa: Double         // degrees ahead of abeam (+ towards the nose)
    public var dep: Double        // degrees below the horizontal
    public var dKm: Double
    public var plat: Double       // the point of the feature that is seen best
    public var plon: Double
    public var altM: Double       // aircraft height at the best moment
    public var light: Light
    public var score: Double
    public var id: String { "\(feature)\(side.rawValue)" }

    public var chance: Double { min(1, max(0, q / 0.45)) }
    public var confidence: String { chance >= 0.55 ? "likely" : chance >= 0.25 ? "possible" : "if lucky" }
}

/// A sighting worth a line of its own, with the lesser things around it at the same time.
public struct Headline: Sendable, Identifiable {
    public var main: Sighting
    public var also: [Sighting]
    public var id: String { main.id }
}

/// What can be seen from a window: planeview/visibility.py's `evaluate`, for one aircraft position at a time.
/// Feature sample points are kept in latitude order so a position only looks at the band of them within reach.
public final class Spotter: @unchecked Sendable {
    public let features: [Feature]
    public let view: ViewParams
    public let terrain: Terrain?
    public var wing: String?

    private let ptLat: [Double], ptLon: [Double], owner: [Int32], ptDark: [Bool]
    private var ptZ: [Float]                 // ground height under each sample point (NaN until asked for)
    private let minDeg: [Double], isPeak: [Bool], isArea: [Bool], isSettlement: [Bool]
    private let zLock = NSLock()
    static let maxRangeKm = 260.0

    public init(features: [Feature], view: ViewParams, terrain: Terrain?, wing: String? = nil) {
        self.features = features
        self.view = view
        self.terrain = terrain
        self.wing = wing
        var la: [Double] = [], lo: [Double] = [], ow: [Int32] = [], dk: [Bool] = []
        for (i, f) in features.enumerated() {
            if let p = f.pts, p.count >= 4 {
                let flags = f.ptsDark.map { Array($0.utf8) }
                var k = 0
                while k + 1 < p.count {
                    la.append(p[k]); lo.append(p[k + 1]); ow.append(Int32(i))
                    dk.append(flags.map { k / 2 < $0.count && $0[k / 2] == 49 } ?? f.dark)
                    k += 2
                }
            } else {
                la.append(f.lat); lo.append(f.lon); ow.append(Int32(i)); dk.append(f.dark)
            }
        }
        let order = la.indices.sorted { la[$0] < la[$1] }
        ptLat = order.map { la[$0] }
        ptLon = order.map { lo[$0] }
        owner = order.map { ow[$0] }
        ptDark = order.map { dk[$0] }
        ptZ = [Float](repeating: .nan, count: order.count)
        minDeg = features.map { view.min_deg[$0.kind] ?? 0.3 }
        isPeak = features.map { $0.kind == "peak" }
        isArea = features.map { $0.kind == "region" || $0.kind == "range" || $0.kind == "sea" }
        isSettlement = features.map { $0.kind == "settlement" }
    }

    private func lowerBound(_ lat: Double) -> Int {
        var lo = 0, hi = ptLat.count
        while lo < hi {
            let mid = (lo + hi) / 2
            if ptLat[mid] < lat { lo = mid + 1 } else { hi = mid }
        }
        return lo
    }

    private func haze(_ hM: Double) -> Double {
        let a = min(1, max(0, (hM - view.haze_h1_m) / (view.haze_h2_m - view.haze_h1_m)))
        return view.haze_low_km + a * (view.haze_high_km - view.haze_low_km)
    }

    private func ground(_ j: Int) -> Double {
        zLock.lock(); defer { zLock.unlock() }
        if ptZ[j].isNaN { ptZ[j] = Float(terrain?.sample(ptLat[j], ptLon[j]) ?? 0) }
        return Double(ptZ[j])
    }

    /// Everything in view from one position, best sample point per feature and side.
    /// `unix` is the time of day there (for the sun); nil means daylight.
    public func visible(from s: AircraftState, unix: Double?) -> [Sighting] {
        var best: [Int: Sighting] = [:]
        scan(s, unix: unix) { sg in
            let key = sg.feature * 2 + (sg.side == .R ? 1 : 0)
            if let b = best[key], b.q >= sg.q { return }
            best[key] = sg
        }
        return Array(best.values)
    }

    private func scan(_ s: AircraftState, unix: Double?, _ emit: (Sighting) -> Void) {
        let v = view
        let h = max(s.altM, 30)
        let band = Spotter.maxRangeKm / 111.195
        let cl = max(0.05, cos(Geo.rad(s.lat)))
        let dip = Geo.dipDeg(h)
        let horizonH = Geo.horizonKm(h)
        let low = terrain != nil && h < v.los_alt_m
        let visKm = haze(min(h, 9000))
        var sunEl = 90.0
        if let u = unix { sunEl = Geo.sun(unix: u, lat: s.lat, lon: s.lon).el }
        let night = unix != nil
        let light = Light(sunEl: sunEl)
        let lightK = sunEl >= 4 ? 1.0 : sunEl >= 0 ? 0.75 : sunEl >= -6 ? 0.35 : 0.0
        let dusk = min(1, max(0, -sunEl / 4))
        let j0 = lowerBound(s.lat - band), j1 = lowerBound(s.lat + band)
        var j = j0
        while j < j1 {
            defer { j += 1 }
            let fi = Int(owner[j])
            let f = features[fi]
            let r = f.rangeKm
            // quick box test before any trigonometry
            let dy = (ptLat[j] - s.lat) * 111.195
            if abs(dy) > r { continue }
            let dx = Geo.wrap180(ptLon[j] - s.lon) * 111.195 * cl
            if abs(dx) > r * 1.3 + 20 { continue }
            let d = Geo.distKm(s.lat, s.lon, ptLat[j], ptLon[j])
            if d > r { continue }
            let brg = Geo.bearing(s.lat, s.lon, ptLat[j], ptLon[j])
            let rel = Geo.wrap180(brg - s.course)
            let side: Side = rel >= 0 ? .R : .L
            let fa = 90 - abs(rel)
            if fa < v.fa_min || fa > v.fa_max { continue }
            var zt = f.z
            if low { zt = max(zt, ground(j)) }
            let dep = Geo.depression(hM: h, zM: zt, dKm: d)
            if d > horizonH + Geo.horizonKm(max(zt, 0)) * 0.95 { continue }
            let floor = (low && zt > h) ? -v.up_max_deg : dip + v.dep_floor
            if dep < floor || dep > v.dep_max { continue }
            if wing == "over" { if !(dep <= v.wing_dep_max || fa > 25) { continue } }
            else if wing == "behind" { if fa > 30 && dep > 25 { continue } }
            let ca = (fa - v.fa_best) / v.fa_spread, cd = (dep - v.dep_best) / v.dep_spread
            let cone = 0.25 + 0.75 * exp(-ca * ca) * exp(-cd * cd)
            let slant = (d * d + (h - zt) * (h - zt) / 1e6).squareRoot()
            var det: Double
            if isPeak[fi] {
                let rng = max(25, (f.elev - 800) / 100 * v.peak_km_per_100m)
                det = min(1, max(0, 1.2 - slant / rng))
            } else {
                let ang = Geo.deg(f.sizeKm / max(slant, 0.2))
                det = min(1.5, max(0, ang / max(minDeg[fi], 1e-3))) / 1.5
            }
            let det0 = det
            det *= exp(-(slant / visKm) * (slant / visKm))
            var q = cone * det
            if night {
                var qn = 0.0
                if isSettlement[fi] && f.pop >= v.night_pop_min {
                    qn = cone * min(1, max(0, (log10(max(f.pop, 1)) - 4) / 2)) * exp(-(slant / v.night_range_km) * (slant / v.night_range_km))
                }
                if f.lit > 0 { qn = max(qn, cone * f.lit * exp(-(slant / v.night_lit_km) * (slant / v.night_lit_km))) }
                if ptDark[j] { qn = max(qn, 0.7 * cone * det0 * exp(-(slant / v.night_dark_km) * (slant / v.night_dark_km))) }
                q = max(q * lightK, qn * dusk)
            }
            if q < v.q_min { continue }
            if low && d > 3 && !isArea[fi] && blocked(s, h: h, j: j, zt: zt, d: d) { continue }
            emit(Sighting(feature: fi, side: side, tBest: s.t, t0: s.t, t1: s.t, q: q, fa: fa, dep: dep, dKm: d,
                          plat: ptLat[j], plon: ptLon[j], altM: h, light: light, score: 0))
        }
    }

    /// Is the straight line to a sample point cut by the ground in between?
    private func blocked(_ s: AircraftState, h: Double, j: Int, zt: Double, d: Double) -> Bool {
        guard let terrain else { return false }
        let n = min(60, max(6, Int((d / view.los_step_km).rounded(.up))))
        let f0 = min(0.9, max(0.03, 1.5 / d))
        let dm = d * 1000
        let dLat = ptLat[j] - s.lat, dLon = Geo.wrap180(ptLon[j] - s.lon)
        for k in 0..<n {
            let f = f0 + (0.94 - f0) * Double(k) / Double(n - 1)
            let g = terrain.sample(s.lat + dLat * f, s.lon + dLon * f)
            let ray = h * (1 - f) + (zt + 15) * f - dm * dm * f * (1 - f) / (2 * Geo.rEff)
            if g > ray + view.los_margin_m { return true }
        }
        return false
    }

    /// Where a named feature is from a position, whether or not it counts as in view (for pointing at something
    /// the list still shows): its nearest sample point on the given side, or nearest of all.
    public func direction(to fi: Int, from s: AircraftState, side: Side) -> (fa: Double, dep: Double, dKm: Double, side: Side) {
        let f = features[fi]
        var pts: [(Double, Double)] = []
        if let p = f.pts, p.count >= 4 {
            var k = 0
            while k + 1 < p.count { pts.append((p[k], p[k + 1])); k += 2 }
        } else { pts = [(f.lat, f.lon)] }
        let h = max(s.altM, 30)
        var best: (fa: Double, dep: Double, dKm: Double, side: Side)? = nil
        var bestKey = Double.infinity
        for (la, lo) in pts {
            let d = Geo.distKm(s.lat, s.lon, la, lo)
            let rel = Geo.wrap180(Geo.bearing(s.lat, s.lon, la, lo) - s.course)
            let sd: Side = rel >= 0 ? .R : .L
            let key = d + (sd == side ? 0 : 1000)
            if key < bestKey {
                bestKey = key
                best = (90 - abs(rel), Geo.depression(hM: h, zM: f.z, dKm: d), d, sd)
            }
        }
        return best!
    }

    // MARK: - along a path

    /// Every sighting along a path: the best moment and the first and last moment in view, per feature and side.
    /// `unixAt` gives the time of day at a plan time (nil: daylight throughout).
    public func schedule(along path: FlightPath, from: Double? = nil, to: Double? = nil,
                         unixAt: ((Double) -> Double)? = nil) -> [Sighting] {
        var acc: [Int: Sighting] = [:]
        for t in path.sampleTimes(from: from, to: to) {
            let s = path.state(at: t)
            scan(s, unix: unixAt?(t)) { sg in
                let key = sg.feature * 2 + (sg.side == .R ? 1 : 0)
                if var a = acc[key] {
                    a.t0 = min(a.t0, t); a.t1 = max(a.t1, t)
                    if sg.q > a.q {
                        let (t0, t1) = (a.t0, a.t1)
                        a = sg; a.t0 = t0; a.t1 = t1
                    }
                    acc[key] = a
                } else {
                    acc[key] = sg
                }
            }
        }
        return acc.values.map { s in
            var s = s
            s.score = score(s)
            return s
        }
    }

    /// How much a sighting deserves attention: how notable the thing is, and how good the view of it.
    public func score(_ s: Sighting) -> Double {
        features[s.feature].salience * s.chance * (0.5 + s.q)
    }

    // MARK: - choosing what to show

    static let minorKinds: Set<String> = ["island", "coast", "lake", "sea", "landmark", "peak"]
    static let distinctKinds: Set<String> = ["coast", "island", "lake", "sea", "landmark", "river"]

    private func minor(_ s: Sighting) -> Bool {
        let f = features[s.feature]
        return Spotter.minorKinds.contains(f.kind) && f.fame < 2 && (f.area ?? 0) < 1 && s.dKm > 10
    }

    /// Thin one side's sightings into a readable list: one headline per slot of time (3 min low down, 5 min in
    /// the cruise; two where the view is rich), the rest of a slot as companions of the nearest headline.
    /// The forecast's own headlines (`preferred`, by feature index) win ties, since they have pictures.
    public func headlines(_ all: [Sighting], side: Side, preferred: Set<Int> = [], alsoMax: Int = 10) -> [Headline] {
        var evs = all.filter { $0.side == side && !features[$0.feature].broad }
        for i in evs.indices where preferred.contains(evs[i].feature) { evs[i].score *= 1.35 }
        evs.sort { $0.score > $1.score }
        guard let top = evs.first?.score else { return [] }
        let rich = evs.count >= 10 ? evs[evs.count / 5].score : top
        var heads: [Headline] = []
        var slots: [Int: [Int]] = [:]            // slot key -> indices into heads
        func norm(_ s: String) -> String { s.lowercased().filter { $0.isLetter || $0.isNumber } }
        for e in evs {
            if e.score < 0.02 * top && e.score < 0.3 { break }
            let f = features[e.feature]
            let low = e.altM / Geo.ft < 15000
            let slotLen = low ? 180.0 : 300.0
            let key = Int(e.tBest / slotLen) * 2 + (low ? 1 : 0)
            if heads.contains(where: { norm(features[$0.main.feature].name) == norm(f.name) && abs($0.main.tBest - e.tBest) < 600 }) { continue }
            let inSlot = slots[key] ?? []
            let isRich = e.score >= rich && inSlot.allSatisfy { heads[$0].main.score >= rich }
            let cap = isRich ? 2 : 1
            if inSlot.count < cap && !minor(e) {
                slots[key, default: []].append(heads.count)
                heads.append(Headline(main: e, also: []))
                continue
            }
            // a companion of the nearest headline in time and direction
            var pick = -1
            var pickKey = Double.infinity
            for (hi, h) in heads.enumerated() where abs(h.main.tBest - e.tBest) <= slotLen && abs(h.main.fa - e.fa) < 35 {
                let k = abs(h.main.tBest - e.tBest) / 60 + abs(h.main.fa - e.fa) / 10
                if k < pickKey { pickKey = k; pick = hi }
            }
            guard pick >= 0 else { continue }
            let distinct = Spotter.distinctKinds.contains(f.kind)
            guard e.score >= 0.1 * heads[pick].main.score,
                  f.salience >= 0.9 || f.fame >= 2 || (distinct && e.chance >= 0.3) else { continue }
            if heads[pick].also.count >= alsoMax { continue }
            if heads[pick].also.filter({ features[$0.feature].kind == f.kind }).count >= 6 { continue }
            if heads[pick].also.contains(where: { norm(features[$0.feature].name) == norm(f.name) }) { continue }
            heads[pick].also.append(e)
        }
        return heads.sorted { $0.main.tBest < $1.main.tBest }
    }
}
