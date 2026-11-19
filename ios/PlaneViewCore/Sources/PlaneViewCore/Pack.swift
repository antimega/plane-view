import Foundation

/// A flight pack as planeview/pack.py writes it (schema plane-view/pack/1): pack.json and features.json, with
/// pictures, map tiles and terrain beside them in the unpacked folder.
public struct PackManifest: Codable, Sendable {
    public var schema: String
    public var id: String
    public var generated: String?
    public var flight: FlightInfo
    public var view: ViewParams
    public var paths: [PathRecord]
    public var corridor: CorridorRecord
    public var context: [ContextItem]
    public var timeline: [String: [PlanSighting]]
    public var map: MapInfo?
    public var dem: DemInfo?
    public var relief: DemInfo?        // coarse terrain for the whole corridor (older packs have none)
    public var detail: String?         // "high" for a pack made with --detail high (sharper map, bigger pictures)
    public var sources: [String: String]?
    public var counts: [String: Int]?
}

public struct FlightInfo: Codable, Sendable {
    public struct Airport: Codable, Sendable {
        public var code: String
        public var icao: String?
        public var name: String?
        public var city: String?
        public var lat: Double
        public var lon: Double
        public var tz: String?
    }
    public struct Seat: Codable, Sendable {
        public var side: String?
        public var window: Bool?
        public var row: Int?
        public var wing: String?
        public var seat: String?
        public var note: String?
    }
    public var number: String?
    public var callsign: String?
    public var airline: String?
    public var date: String
    public var orig: Airport
    public var dest: Airport
    public var aircraft: String?
    public var aircraft_name: String?
    public var takeoff_unix: Double
    public var takeoff_local: String?
    public var takeoff_source: String?
    public var duration_min: Double
    public var seat: Seat?
    public var sides: [String]

    public var title: String { number ?? "\(orig.code)–\(dest.code)" }
    public var route: String { "\(orig.code) → \(dest.code)" }
    /// The line under the title: the airports when the title is a flight number, the cities when it is the airports.
    public var subtitle: String { number != nil ? route : "\(orig.city ?? orig.code) → \(dest.city ?? dest.code)" }
}

/// The window-view model: every number planeview/visibility.py uses, carried in the pack so the two cannot drift.
public struct ViewParams: Codable, Sendable {
    public var fa_min, fa_max, fa_best, fa_spread: Double
    public var dep_max, dep_best, dep_spread, dep_floor: Double
    public var haze_low_km, haze_high_km, haze_h1_m, haze_h2_m: Double
    public var min_deg: [String: Double]
    public var peak_km_per_100m, peak_max_km: Double
    public var night_pop_min, night_range_km, night_lit_km, night_dark_km: Double
    public var q_min, wing_dep_max: Double
    public var los_alt_m, los_step_km, los_margin_m, up_max_deg: Double
}

public struct PathRecord: Codable, Sendable {
    public var id: String
    public var kind: String            // variant | history
    public var p: Double               // prior weight (variants: share of past flights; history: leg weight)
    public var label: String?
    public var dep_rwy: String?
    public var arr_rwy: String?
    public var spread_km: Double?
    public var track: [[Double]]       // [minutes after takeoff, lat, lon, alt_ft]
}

public struct CorridorRecord: Codable, Sendable {
    public var reach_km: Double
    public var flown: [[[Double]]]     // rings of [lat, lon]: where past flights went
    public var reach: [[[Double]]]     // the area the pack's features cover the view from
}

public struct ContextItem: Codable, Sendable, Identifiable {
    public var type: String
    public var t: Double
    public var text: String
    public var lat: Double?
    public var lon: Double?
    public var t_end: Double?
    public var side: String?
    public var id: String { "\(type)-\(t)-\(text)" }
}

public struct PlanSighting: Codable, Sendable, Identifiable {
    public struct Also: Codable, Sendable {
        public var name: String
        public var kind: String
        public var what: String?
        public var lat: Double
        public var lon: Double
    }
    public struct Label: Codable, Sendable {
        public var n: String
        public var x: Double
        public var y: Double
        public var main: Bool?
    }
    public struct ViewOverlay: Codable, Sendable {
        public var file: String?
        public var coast: [[[Double]]]?
        public var lakes: [[[Double]]]?
        public var rivers: [[[Double]]]?
        public var borders: [[[Double]]]?
        public var labels: [Label]?
        public var horizon: Double?
    }
    public struct Look: Codable, Sendable {
        public var up: Double
        public var compass: String?
    }
    public struct AerialOverlay: Codable, Sendable {
        public var file: String?
        public var km: Double?
        public var route: [[[Double]]]?
        public var look: Look?
        public var labels: [Label]?
    }
    public var id: String
    public var name: String
    public var local: String?
    public var kind: String
    public var what: String?
    public var side: String
    public var t: Double
    public var t_lo: Double?
    public var t_hi: Double?
    public var t_end: Double?
    public var fa: Double
    public var dep: Double
    public var d_km: Double
    public var alt_ft: Double?
    public var p_see: Double?
    public var confidence: String?
    public var light: String?
    public var ac: [Double]?
    public var brg: Double?
    public var lat: Double
    public var lon: Double
    public var also: [Also]?
    public var view: ViewOverlay?
    public var aerial: AerialOverlay?
}

public struct MapInfo: Codable, Sendable {
    public var min_z: Int
    public var max_z: Int
    public var attribution: String?
    public var bounds: [Double]?       // south, west, north, east
    public var tiles: Int?
}

public struct DemInfo: Codable, Sendable {
    public var n: Int
    public var tiles: [String]
}

/// One named thing that might be seen. Short keys keep features.json small (a European flight has ~15,000).
public struct Feature: Codable, Sendable {
    public var id: String
    public var name: String
    public var local: String?
    public var kind: String
    public var sub: String?
    public var what: String?
    public var lat: Double
    public var lon: Double
    public var z: Double
    public var sizeKm: Double
    public var pop: Double
    public var elev: Double
    public var area: Double?
    public var fame: Double
    public var salience: Double
    public var rangeKm: Double
    public var lit: Double
    public var dark: Bool
    public var broad: Bool
    public var pts: [Double]?          // lat, lon, lat, lon ... when the feature has more than one sample point
    public var ptsDark: String?        // per sample point, "1" where it lies inside a big city's lights (a dark gap at night)
    public var pL: Double?             // share of past flights that saw it from the left window
    public var pR: Double?

    enum CodingKeys: String, CodingKey {
        case id = "i", name = "n", local = "l", kind = "k", sub = "s", what = "w", lat = "la", lon = "lo", z
        case sizeKm = "sz", pop, elev = "el", area = "ar", fame = "fm", salience = "sal", rangeKm = "r", lit
        case dark = "dk", broad = "br", pts = "p", ptsDark = "pd", pL, pR
    }

    public init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = try c.decode(String.self, forKey: .name)
        local = try c.decodeIfPresent(String.self, forKey: .local)
        kind = try c.decode(String.self, forKey: .kind)
        sub = try c.decodeIfPresent(String.self, forKey: .sub)
        what = try c.decodeIfPresent(String.self, forKey: .what)
        lat = try c.decode(Double.self, forKey: .lat)
        lon = try c.decode(Double.self, forKey: .lon)
        z = try c.decodeIfPresent(Double.self, forKey: .z) ?? 0
        sizeKm = try c.decodeIfPresent(Double.self, forKey: .sizeKm) ?? 1
        pop = try c.decodeIfPresent(Double.self, forKey: .pop) ?? 0
        elev = try c.decodeIfPresent(Double.self, forKey: .elev) ?? 0
        area = try c.decodeIfPresent(Double.self, forKey: .area)
        fame = try c.decodeIfPresent(Double.self, forKey: .fame) ?? 0
        salience = try c.decodeIfPresent(Double.self, forKey: .salience) ?? 0.5
        rangeKm = try c.decodeIfPresent(Double.self, forKey: .rangeKm) ?? 30
        lit = try c.decodeIfPresent(Double.self, forKey: .lit) ?? 0
        dark = (try c.decodeIfPresent(Int.self, forKey: .dark) ?? 0) != 0
        broad = (try c.decodeIfPresent(Int.self, forKey: .broad) ?? 0) != 0
        pts = try c.decodeIfPresent([Double].self, forKey: .pts)
        ptsDark = try c.decodeIfPresent(String.self, forKey: .ptsDark)
        pL = try c.decodeIfPresent(Double.self, forKey: .pL)
        pR = try c.decodeIfPresent(Double.self, forKey: .pR)
    }

    public func prior(_ side: Side) -> Double? { side == .L ? pL : pR }
}

struct FeatureFile: Codable {
    var features: [Feature]
}

/// An unpacked flight pack on disk.
public final class Pack: @unchecked Sendable {
    public let dir: URL
    public let manifest: PackManifest
    public let features: [Feature]
    public let paths: [FlightPath]
    public let terrain: Terrain?
    public let relief: Terrain?
    /// Plan sightings (the forecast's headlines, which have pictures) by feature id, per side.
    public let plan: [Side: [String: PlanSighting]]

    public enum Failure: Error, LocalizedError {
        case schema(String)
        public var errorDescription: String? {
            switch self {
            case .schema(let s): return "This flight pack is in a format this app does not know (\(s))."
            }
        }
    }

    public init(dir: URL) throws {
        self.dir = dir
        let dec = JSONDecoder()
        let m = try dec.decode(PackManifest.self, from: Data(contentsOf: dir.appendingPathComponent("pack.json")))
        guard m.schema == "plane-view/pack/1" else { throw Failure.schema(m.schema) }
        manifest = m
        features = try dec.decode(FeatureFile.self, from: Data(contentsOf: dir.appendingPathComponent("features.json"))).features
        paths = m.paths.compactMap { FlightPath(record: $0) }
        terrain = m.dem.map { Terrain(dir: dir.appendingPathComponent("dem"), info: $0) }
        relief = m.relief.map { Terrain(dir: dir.appendingPathComponent("relief"), info: $0) }
        var plan: [Side: [String: PlanSighting]] = [.L: [:], .R: [:]]
        for (k, list) in m.timeline {
            guard let sd = Side(rawValue: k) else { continue }
            for e in list { plan[sd]?[e.id] = e }
        }
        self.plan = plan
    }

    public var id: String { manifest.id }
    public var isHighDetail: Bool { manifest.detail == "high" }
    public var flight: FlightInfo { manifest.flight }
    public var takeoff: Date { Date(timeIntervalSince1970: manifest.flight.takeoff_unix) }
    public var variants: [FlightPath] { paths.filter { $0.kind == "variant" } }
    public var mainPath: FlightPath { variants.first ?? paths[0] }

    public func file(_ rel: String) -> URL { dir.appendingPathComponent(rel) }

    /// Height of the ground in metres for drawing: the fine terrain where the pack has it, else the coarse relief,
    /// else sea level.
    public func groundHeight(_ lat: Double, _ lon: Double) -> Double {
        terrain?.height(lat, lon) ?? relief?.height(lat, lon) ?? 0
    }

    /// Read just the manifest of an unpacked pack (for the list of flights).
    public static func manifest(in dir: URL) -> PackManifest? {
        guard let d = try? Data(contentsOf: dir.appendingPathComponent("pack.json")) else { return nil }
        return try? JSONDecoder().decode(PackManifest.self, from: d)
    }
}

/// Terrain heights for sight lines near the ground: the forecaster's ~360 m Copernicus cells as 16-bit tiles.
public final class Terrain: @unchecked Sendable {
    let dir: URL
    let n: Int
    let have: Set<String>
    private var tiles: [Int: [Int16]?] = [:]
    private let lock = NSLock()

    init(dir: URL, info: DemInfo) {
        self.dir = dir
        n = info.n
        have = Set(info.tiles)
    }

    private func tile(_ a: Int, _ b: Int) -> [Int16]? {
        let key = a * 1000 + b
        lock.lock(); defer { lock.unlock() }
        if let t = tiles[key] { return t }
        let name = "\(a >= 0 ? "N" : "S")\(String(format: "%02d", abs(a)))\(b >= 0 ? "E" : "W")\(String(format: "%03d", abs(b)))"
        var t: [Int16]? = nil
        if have.contains(name), let d = try? Data(contentsOf: dir.appendingPathComponent("\(name).i16")), d.count == n * n * 2 {
            t = d.withUnsafeBytes { raw in Array(raw.bindMemory(to: Int16.self)).map { Int16(littleEndian: $0) } }
        }
        tiles[key] = t
        return t
    }

    /// Height in metres of the ground (nearest cell); the sea and anything outside the pack count as 0.
    public func sample(_ lat: Double, _ lon: Double) -> Double { height(lat, lon) ?? 0 }

    /// Height in metres of the ground, or nil where this terrain has no tile.
    public func height(_ lat: Double, _ lon: Double) -> Double? {
        let a = Int(lat.rounded(.down)), b = Int(lon.rounded(.down))
        guard let t = tile(a, b) else { return nil }
        let r = min(n - 1, max(0, Int((Double(a + 1) - lat) * Double(n))))
        let c = min(n - 1, max(0, Int((lon - Double(b)) * Double(n))))
        return max(0, Double(t[r * n + c]))
    }
}
