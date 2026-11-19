import Foundation

/// Spherical geometry, sight lines and the sun. A port of planeview/geo.py: the same Earth radius, the same
/// refraction (k = 0.13), the same NOAA sun approximation, so the phone and the forecast agree.
public enum Geo {
    public static let R = 6_371_008.8
    public static let rKm = R / 1000
    public static let kRefr = 0.13
    public static let rEff = R / (1 - kRefr)
    public static let ft = 0.3048

    @inline(__always) public static func rad(_ d: Double) -> Double { d * .pi / 180 }
    @inline(__always) public static func deg(_ r: Double) -> Double { r * 180 / .pi }

    @inline(__always) public static func wrap180(_ a: Double) -> Double {
        var x = (a + 540).truncatingRemainder(dividingBy: 360)
        if x < 0 { x += 360 }
        return x - 180
    }

    @inline(__always) public static func wrap360(_ a: Double) -> Double {
        let x = a.truncatingRemainder(dividingBy: 360)
        return x < 0 ? x + 360 : x
    }

    public static func distKm(_ lat1: Double, _ lon1: Double, _ lat2: Double, _ lon2: Double) -> Double {
        let p1 = rad(lat1), p2 = rad(lat2)
        let dp = p2 - p1, dl = rad(lon2 - lon1)
        let a = sin(dp / 2) * sin(dp / 2) + cos(p1) * cos(p2) * sin(dl / 2) * sin(dl / 2)
        return 2 * rKm * asin(min(1, max(0, a).squareRoot()))
    }

    public static func bearing(_ lat1: Double, _ lon1: Double, _ lat2: Double, _ lon2: Double) -> Double {
        let p1 = rad(lat1), p2 = rad(lat2), dl = rad(lon2 - lon1)
        let y = sin(dl) * cos(p2)
        let x = cos(p1) * sin(p2) - sin(p1) * cos(p2) * cos(dl)
        return wrap360(deg(atan2(y, x)) + 360)
    }

    public static func destination(_ lat: Double, _ lon: Double, bearing b: Double, km: Double) -> (lat: Double, lon: Double) {
        let d = km / rKm, la = rad(lat), lo = rad(lon), br = rad(b)
        let la2 = asin(sin(la) * cos(d) + cos(la) * sin(d) * cos(br))
        let lo2 = lo + atan2(sin(br) * sin(d) * cos(la), cos(d) - sin(la) * sin(la2))
        return (deg(la2), wrap180(deg(lo2)))
    }

    /// A point the fraction f of the way along the great circle from 1 to 2.
    public static func between(_ lat1: Double, _ lon1: Double, _ lat2: Double, _ lon2: Double, _ f: Double) -> (lat: Double, lon: Double) {
        let d = distKm(lat1, lon1, lat2, lon2)
        if d < 0.05 { return (lat1 + (lat2 - lat1) * f, lon1 + wrap180(lon2 - lon1) * f) }
        return destination(lat1, lon1, bearing: bearing(lat1, lon1, lat2, lon2), km: d * f)
    }

    /// Angle below the horizontal from height h to a point at height z, ground distance d (curvature + refraction).
    @inline(__always) public static func depression(hM: Double, zM: Double, dKm: Double) -> Double {
        let d = dKm * 1000
        return deg(atan2(hM - zM + d * d / (2 * rEff), max(d, 1)))
    }

    @inline(__always) public static func horizonKm(_ hM: Double) -> Double { (2 * rEff * max(hM, 0)).squareRoot() / 1000 }
    @inline(__always) public static func dipDeg(_ hM: Double) -> Double { deg((2 * max(hM, 0) / rEff).squareRoot()) }

    /// Sun azimuth and elevation in degrees for a unix time and a position.
    public static func sun(unix t: Double, lat: Double, lon: Double) -> (az: Double, el: Double) {
        let jd = t / 86400 + 2440587.5
        let n = jd - 2451545.0
        let L = wrap360(280.460 + 0.9856474 * n)
        let g = rad(wrap360(357.528 + 0.9856003 * n))
        let lam = rad(L + 1.915 * sin(g) + 0.020 * sin(2 * g))
        let eps = rad(23.439 - 0.0000004 * n)
        let ra = atan2(cos(eps) * sin(lam), cos(lam))
        let dec = asin(sin(eps) * sin(lam))
        var gmst = (18.697374558 + 24.06570982441908 * n).truncatingRemainder(dividingBy: 24)
        if gmst < 0 { gmst += 24 }
        let ha = rad(gmst * 15 + lon - deg(ra))
        let la = rad(lat)
        let el = asin(sin(la) * sin(dec) + cos(la) * cos(dec) * cos(ha))
        let az = atan2(-sin(ha), tan(dec) * cos(la) - sin(la) * cos(ha))
        return (wrap360(deg(az) + 360), deg(el))
    }
}

public enum Side: String, Codable, CaseIterable, Sendable {
    case L, R
    public var word: String { self == .L ? "left" : "right" }
    public var other: Side { self == .L ? .R : .L }
}

public enum Light: String, Codable, Sendable {
    case day, twilight, night
    public init(sunEl: Double) { self = sunEl >= 0 ? .day : sunEl >= -6 ? .twilight : .night }
}

/// Where to look, in the words the forecast uses: "right, ahead, a little below the horizon" and "2 o'clock".
public func whereWords(side: Side, fa: Double, dep: Double) -> (words: String, clock: String) {
    let ahead = fa > 35 ? "well ahead" : fa > 10 ? "ahead" : fa >= -10 ? "straight out" : "behind"
    let down = dep < 0 ? "above you" : dep < 7 ? "near the horizon" : dep < 18 ? "a little below the horizon"
        : dep < 32 ? "well below the horizon" : "steeply down"
    let rel = 90 - fa
    var hour = side == .R ? Int((rel / 30).rounded()) : 12 - Int((rel / 30).rounded())
    hour = ((hour - 1) % 12 + 12) % 12 + 1
    return ("\(ahead), \(down)", "\(hour) o'clock")
}
