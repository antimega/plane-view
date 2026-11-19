import Foundation

/// Where an aircraft is and which way it is going.
public struct AircraftState: Sendable, Equatable {
    public var t: Double          // seconds after takeoff along the plan ("plan time")
    public var lat: Double
    public var lon: Double
    public var altM: Double
    public var course: Double     // degrees true, over the ground

    public init(t: Double, lat: Double, lon: Double, altM: Double, course: Double) {
        self.t = t; self.lat = lat; self.lon = lon; self.altM = altM; self.course = course
    }
    public var altFt: Double { altM / Geo.ft }
}

/// A timed track: a likely route, a past flight, or the path the app has built for the flight in progress.
public final class FlightPath: @unchecked Sendable {
    public let id: String
    public let kind: String
    public let weight: Double
    public let label: String?
    public let spreadKm: Double
    public let pts: [AircraftState]        // course filled in
    /// cos(latitude) at the middle of each segment and the segment's bounding box, for quick projection
    private let segBox: [(minLat: Double, maxLat: Double, minLon: Double, maxLon: Double)]

    public init?(record r: PathRecord) {
        let raw = r.track.filter { $0.count >= 4 }
        guard raw.count >= 2 else { return nil }
        var p = raw.map { AircraftState(t: $0[0] * 60, lat: $0[1], lon: $0[2], altM: $0[3] * Geo.ft, course: 0) }
        // strictly increasing time, so interpolation never divides by zero
        var out: [AircraftState] = [p[0]]
        for q in p.dropFirst() where q.t > out[out.count - 1].t + 0.5 { out.append(q) }
        p = out
        guard p.count >= 2 else { return nil }
        id = r.id; kind = r.kind; weight = r.p; label = r.label; spreadKm = r.spread_km ?? 10
        pts = FlightPath.withCourse(p)
        segBox = FlightPath.boxes(pts)
    }

    public init(id: String, kind: String, weight: Double = 1, label: String? = nil, spreadKm: Double = 10, points: [AircraftState]) {
        self.id = id; self.kind = kind; self.weight = weight; self.label = label; self.spreadKm = spreadKm
        pts = FlightPath.withCourse(points)
        segBox = FlightPath.boxes(pts)
    }

    private static func boxes(_ p: [AircraftState]) -> [(minLat: Double, maxLat: Double, minLon: Double, maxLon: Double)] {
        (0..<max(0, p.count - 1)).map { i in
            (min(p[i].lat, p[i + 1].lat), max(p[i].lat, p[i + 1].lat), min(p[i].lon, p[i + 1].lon), max(p[i].lon, p[i + 1].lon))
        }
    }

    private static func withCourse(_ p: [AircraftState]) -> [AircraftState] {
        var out = p
        let n = p.count
        for i in 0..<n {
            let a = p[max(0, i - 1)], b = p[min(n - 1, i + 1)]
            if Geo.distKm(a.lat, a.lon, b.lat, b.lon) > 0.02 {
                out[i].course = Geo.bearing(a.lat, a.lon, b.lat, b.lon)
            } else if i > 0 {
                out[i].course = out[i - 1].course
            }
        }
        return out
    }

    public var start: Double { pts[0].t }
    public var end: Double { pts[pts.count - 1].t }
    public var duration: Double { end - start }

    private func index(_ t: Double) -> Int {
        var lo = 0, hi = pts.count - 2
        if t <= pts[0].t { return 0 }
        if t >= pts[hi].t { return hi }
        while lo < hi {
            let mid = (lo + hi + 1) / 2
            if pts[mid].t <= t { lo = mid } else { hi = mid - 1 }
        }
        return lo
    }

    /// The state at plan time t (clamped to the ends). Position along the great circle between samples; the
    /// course is the direction of that segment, eased through the samples.
    public func state(at t: Double) -> AircraftState {
        let i = index(t)
        let a = pts[i], b = pts[i + 1]
        let f = min(1, max(0, (t - a.t) / (b.t - a.t)))
        let p = Geo.between(a.lat, a.lon, b.lat, b.lon, f)
        let crs = a.course + Geo.wrap180(b.course - a.course) * f
        return AircraftState(t: min(max(t, start), end), lat: p.lat, lon: p.lon, altM: a.altM + (b.altM - a.altM) * f,
                             course: Geo.wrap360(crs))
    }

    /// Ground speed (m/s) the plan expects around plan time t.
    public func speed(at t: Double) -> Double {
        let a = state(at: t - 30), b = state(at: t + 30)
        let dt = b.t - a.t
        return dt > 1 ? Geo.distKm(a.lat, a.lon, b.lat, b.lon) * 1000 / dt : 0
    }

    public struct Projection: Sendable {
        public var t: Double            // plan time of the nearest point on the path
        public var crossKm: Double      // + = the position is right of the path
        public var distKm: Double       // how far from the path (>= |crossKm|: beyond an end it is further)
    }

    /// The nearest point of the path to a position. With a hint (the plan time last matched) a place where the
    /// path crosses or doubles back on itself resolves to the part the aircraft is on, not one flown an hour before.
    public func project(lat: Double, lon: Double, hint: Double? = nil) -> Projection {
        let cl = cos(Geo.rad(lat))
        var bestD = Double.infinity, bestT = pts[0].t, bestX = 0.0
        var cands: [(d: Double, t: Double, x: Double)] = []
        let kx = 111.195 * cl, ky = 111.195
        for i in 0..<pts.count - 1 {
            // skip segments that cannot beat the best so far (boxes are in degrees)
            if bestD.isFinite {
                let b = segBox[i]
                let dLat = max(0, max(b.minLat - lat, lat - b.maxLat)) * ky
                if dLat > bestD + 2 { continue }
            }
            let a = pts[i], b = pts[i + 1]
            let ax = Geo.wrap180(a.lon - lon) * kx, ay = (a.lat - lat) * ky
            let bx = Geo.wrap180(b.lon - lon) * kx, by = (b.lat - lat) * ky
            let dx = bx - ax, dy = by - ay
            let len2 = dx * dx + dy * dy
            let f = len2 > 1e-9 ? min(1, max(0, -(ax * dx + ay * dy) / len2)) : 0
            let px = ax + dx * f, py = ay + dy * f
            let d = (px * px + py * py).squareRoot()
            // right of the direction of travel: the path-to-point vector (-p) crossed with the direction
            let cross = dx * (-py) - dy * (-px)
            let x = cross <= 0 ? d : -d
            let t = a.t + (b.t - a.t) * f
            if hint != nil { cands.append((d, t, x)) }
            if d < bestD { bestD = d; bestT = t; bestX = x }
        }
        if let h = hint {
            // among the places about as close as the closest, take the one nearest in time to the hint
            var pick: (d: Double, t: Double, x: Double)? = nil
            for c in cands where c.d <= bestD + max(2, bestD * 0.15) {
                if pick == nil || abs(c.t - h) < abs(pick!.t - h) { pick = c }
            }
            if let p = pick { return Projection(t: p.t, crossKm: p.x, distKm: p.d) }
        }
        return Projection(t: bestT, crossKm: bestX, distKm: bestD)
    }

    /// Plan times at the forecaster's evaluation step: every 15 s below 15,000 ft, 30 s above.
    public func sampleTimes(from: Double? = nil, to: Double? = nil) -> [Double] {
        var out: [Double] = []
        var t = max(start, from ?? start)
        let stop = min(end, to ?? end)
        while t < stop {
            out.append(t)
            t += state(at: t).altFt < 15000 ? 15 : 30
        }
        out.append(stop)
        return out
    }
}
