import Foundation
import PlaneViewCore

/// The positions of this flight, kept on disk: what photos are placed against afterwards, what "rewind" replays,
/// and what is exported as a GPX track.
final class TrackLog {
    struct Row {
        var wall: Date        // the phone's clock when the fix was taken (what photo times compare with)
        var fix: Fix
        var tau: Double       // plan time it was matched to
    }

    private(set) var rows: [Row] = []
    let url: URL
    private var handle: FileHandle?
    private var lastWritten: Date?

    init(packID: String, replay: Bool) {
        let dir = URL.applicationSupportDirectory.appendingPathComponent("Logs", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        url = dir.appendingPathComponent(packID + (replay ? ".replay" : "") + ".csv")
        if replay { try? FileManager.default.removeItem(at: url) }
        load()
    }

    private func load() {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return }
        for line in text.split(separator: "\n") {
            let p = line.split(separator: ",", omittingEmptySubsequences: false).map { Double($0) }
            guard p.count >= 9, let w = p[0], let t = p[1], let la = p[2], let lo = p[3], let tau = p[8] else { continue }
            rows.append(Row(wall: Date(timeIntervalSince1970: w),
                            fix: Fix(time: Date(timeIntervalSince1970: t), lat: la, lon: lo, altM: p[4], speed: p[5], course: p[6], hAcc: p[7] ?? 20),
                            tau: tau))
        }
    }

    /// Keep a fix (one every two seconds is plenty).
    func add(_ fix: Fix, tau: Double, wall: Date = Date()) {
        if let l = lastWritten, wall.timeIntervalSince(l) < 2 { return }
        lastWritten = wall
        rows.append(Row(wall: wall, fix: fix, tau: tau))
        func n(_ v: Double?, _ d: Int) -> String { v.map { String(format: "%.\(d)f", $0) } ?? "" }
        let line = "\(n(wall.timeIntervalSince1970, 1)),\(n(fix.time.timeIntervalSince1970, 1)),\(n(fix.lat, 6)),\(n(fix.lon, 6)),"
            + "\(n(fix.altM, 0)),\(n(fix.speed, 1)),\(n(fix.course, 1)),\(n(fix.hAcc, 0)),\(n(tau, 1))\n"
        if handle == nil {
            if !FileManager.default.fileExists(atPath: url.path) { FileManager.default.createFile(atPath: url.path, contents: nil) }
            handle = try? FileHandle(forWritingTo: url)
            _ = try? handle?.seekToEnd()
        }
        try? handle?.write(contentsOf: Data(line.utf8))
    }

    func clear() {
        try? handle?.close()
        handle = nil
        rows = []
        lastWritten = nil
        try? FileManager.default.removeItem(at: url)
    }

    /// Where the aircraft was at a moment of the phone's clock: between two logged fixes when they are close
    /// enough in time, nil otherwise (the caller falls back on the route).
    func position(at wall: Date, maxGap: TimeInterval = 180) -> (state: AircraftState, time: Date)? {
        guard !rows.isEmpty else { return nil }
        var lo = 0, hi = rows.count - 1
        while lo < hi {
            let mid = (lo + hi + 1) / 2
            if rows[mid].wall <= wall { lo = mid } else { hi = mid - 1 }
        }
        let a = rows[lo]
        let b = rows[min(rows.count - 1, lo + 1)]
        if a.wall > wall {
            guard a.wall.timeIntervalSince(wall) < 30 else { return nil }
            return (state(a, a), a.fix.time)
        }
        let span = b.wall.timeIntervalSince(a.wall)
        if lo + 1 >= rows.count || span <= 0 {
            guard wall.timeIntervalSince(a.wall) < 30 else { return nil }
            return (state(a, a), a.fix.time)
        }
        guard span <= maxGap else { return nil }
        let f = wall.timeIntervalSince(a.wall) / span
        let p = Geo.between(a.fix.lat, a.fix.lon, b.fix.lat, b.fix.lon, f)
        let alt = (a.fix.altM ?? 0) + ((b.fix.altM ?? a.fix.altM ?? 0) - (a.fix.altM ?? 0)) * f
        let crs = Geo.distKm(a.fix.lat, a.fix.lon, b.fix.lat, b.fix.lon) > 0.05
            ? Geo.bearing(a.fix.lat, a.fix.lon, b.fix.lat, b.fix.lon) : (a.fix.course ?? 0)
        return (AircraftState(t: a.tau + (b.tau - a.tau) * f, lat: p.lat, lon: p.lon, altM: alt, course: crs),
                a.fix.time.addingTimeInterval(b.fix.time.timeIntervalSince(a.fix.time) * f))
    }

    private func state(_ a: Row, _ b: Row) -> AircraftState {
        AircraftState(t: a.tau, lat: a.fix.lat, lon: a.fix.lon, altM: a.fix.altM ?? 0, course: a.fix.course ?? 0)
    }

    /// The track as GPX 1.1, which most mapping and photo-geotagging tools read.
    func gpx(name: String) -> URL? {
        guard !rows.isEmpty else { return nil }
        let iso = ISO8601DateFormatter()
        var s = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<gpx version=\"1.1\" creator=\"Plane View\" xmlns=\"http://www.topografix.com/GPX/1/1\">\n"
        s += "<trk><name>\(name)</name><trkseg>\n"
        for r in rows {
            s += String(format: "<trkpt lat=\"%.6f\" lon=\"%.6f\">", r.fix.lat, r.fix.lon)
            if let a = r.fix.altM { s += String(format: "<ele>%.0f</ele>", a) }
            s += "<time>\(iso.string(from: r.wall))</time></trkpt>\n"
        }
        s += "</trkseg></trk>\n</gpx>\n"
        let out = FileManager.default.temporaryDirectory.appendingPathComponent("\(name).gpx")
        do { try s.write(to: out, atomically: true, encoding: .utf8) } catch { return nil }
        return out
    }
}
