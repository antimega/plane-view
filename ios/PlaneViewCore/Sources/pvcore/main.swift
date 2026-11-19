import Foundation
import PlaneViewCore

// pvcore: the app's core on the Mac, for checking it against the forecaster and for replaying flights.
//
//   pvcore unpack <file.planeview> <dir>
//   pvcore visible <dir> <lat> <lon> <alt_m> <course> [unix]      what is in view from one position (JSON)
//   pvcore schedule <dir> <path id>                                sightings along a known path (JSON)
//   pvcore replay <dir> <path id> [--side L|R] [--every 300] [--gap 1200-2400]... [--shift km] [--late s] [--nogps]
//          flies a known path as if it were today's flight and prints what the app would show

func fail(_ s: String) -> Never {
    FileHandle.standardError.write((s + "\n").data(using: .utf8)!)
    exit(2)
}

func json(_ rows: [[String: Any]]) {
    let d = try! JSONSerialization.data(withJSONObject: rows, options: [.sortedKeys])
    FileHandle.standardOutput.write(d)
    print()
}

func row(_ s: Sighting, _ pack: Pack) -> [String: Any] {
    let f = pack.features[s.feature]
    return ["id": f.id, "name": f.name, "side": s.side.rawValue, "q": (s.q * 1e4).rounded() / 1e4, "fa": (s.fa * 100).rounded() / 100,
            "dep": (s.dep * 100).rounded() / 100, "d": (s.dKm * 100).rounded() / 100, "t": s.tBest, "t0": s.t0, "t1": s.t1]
}

func mmss(_ s: Double) -> String {
    let a = Int(abs(s).rounded())
    return "\(s < 0 ? "-" : "")\(a / 60):" + String(format: "%02d", a % 60)
}

let args = Array(CommandLine.arguments.dropFirst())
guard let cmd = args.first else { fail("usage: pvcore unpack|visible|schedule|replay ...") }

switch cmd {
case "unpack":
    guard args.count == 3 else { fail("pvcore unpack <file> <dir>") }
    do { try Zip.extract(URL(fileURLWithPath: args[1]), to: URL(fileURLWithPath: args[2])) } catch { fail("\(error.localizedDescription)") }

case "visible":
    guard args.count >= 6, let la = Double(args[2]), let lo = Double(args[3]), let alt = Double(args[4]), let crs = Double(args[5])
    else { fail("pvcore visible <dir> <lat> <lon> <alt_m> <course> [unix]") }
    let pack = try Pack(dir: URL(fileURLWithPath: args[1]))
    let sp = Spotter(features: pack.features, view: pack.manifest.view, terrain: pack.terrain)
    let v = sp.visible(from: AircraftState(t: 0, lat: la, lon: lo, altM: alt, course: crs), unix: args.count > 6 ? Double(args[6]) : nil)
    json(v.sorted { $0.q > $1.q }.map { row($0, pack) })

case "schedule":
    guard args.count == 3 else { fail("pvcore schedule <dir> <path id>") }
    let pack = try Pack(dir: URL(fileURLWithPath: args[1]))
    guard let path = pack.paths.first(where: { $0.id == args[2] }) else { fail("no path \(args[2])") }
    let sp = Spotter(features: pack.features, view: pack.manifest.view, terrain: pack.terrain)
    let t = Date()
    let all = sp.schedule(along: path, unixAt: { pack.flight.takeoff_unix + $0 })
    FileHandle.standardError.write("\(all.count) sightings in \(String(format: "%.2f", Date().timeIntervalSince(t))) s\n".data(using: .utf8)!)
    json(all.sorted { $0.tBest < $1.tBest }.map { row($0, pack) })

case "replay":
    guard args.count >= 3 else { fail("pvcore replay <dir> <path id> [options]") }
    let pack = try Pack(dir: URL(fileURLWithPath: args[1]))
    guard let flown = pack.paths.first(where: { $0.id == args[2] }) else { fail("no path \(args[2])") }
    var side = Side.R, every = 300.0, gaps: [(Double, Double)] = [], shift = 0.0, late = 0.0, noGPS = false, pinAt: Double? = nil
    var i = 3
    while i < args.count {
        switch args[i] {
        case "--side": side = Side(rawValue: args[i + 1]) ?? .R; i += 1
        case "--every": every = Double(args[i + 1]) ?? 300; i += 1
        case "--gap":
            let p = args[i + 1].split(separator: "-").compactMap { Double($0) }
            if p.count == 2 { gaps.append((p[0], p[1])) }
            i += 1
        case "--shift": shift = Double(args[i + 1]) ?? 0; i += 1
        case "--late": late = Double(args[i + 1]) ?? 0; i += 1
        case "--nogps": noGPS = true
        case "--pin": pinAt = Double(args[i + 1]); i += 1
        default: fail("unknown option \(args[i])")
        }
        i += 1
    }
    let model = FlightModel(pack: pack)
    let takeoff = pack.takeoff.addingTimeInterval(late)
    // the truth: the chosen path, moved `shift` km to its right in the cruise (a routing nobody has flown)
    func truth(_ t: Double) -> AircraftState {
        var s = flown.state(at: t)
        if shift != 0 {
            let k = min(1, max(0, (t - 600) / 600)) * min(1, max(0, (flown.end - 900 - t) / 600))
            let p = Geo.destination(s.lat, s.lon, bearing: s.course + 90, km: shift * k)
            s.lat = p.lat; s.lon = p.lon
        }
        return s
    }
    print("\(pack.flight.title) \(pack.flight.route): flying \(flown.id) (\(flown.label ?? flown.kind))"
          + (shift != 0 ? ", \(Int(shift)) km right of it in the cruise" : "") + (late != 0 ? ", \(Int(late / 60)) min late" : "")
          + ", \(side.word) window")
    var t = -120.0
    var lastPrint = -1e9
    var errs: [Double] = []
    while t <= flown.end + 60 {
        let now = takeoff.addingTimeInterval(t)
        let inGap = noGPS || gaps.contains { t >= $0.0 && t < $0.1 }
        if !inGap, t >= 0, t <= flown.end {
            let s = truth(t), s2 = truth(min(flown.end, t + 5))
            let v = Geo.distKm(s.lat, s.lon, s2.lat, s2.lon) * 1000 / 5
            model.navigator.ingest(Fix(time: now, lat: s.lat, lon: s.lon, altM: s.altM, speed: v,
                                       course: v > 5 ? Geo.bearing(s.lat, s.lon, s2.lat, s2.lon) : nil, hAcc: 15))
        }
        if let p = pinAt, abs(t - p) < 2.5 {
            // the user presses "we are here" at the right moment, knowing where they are
            model.navigator.setPin(Pin(at: now, tau: t))
            print("       -- pinned: 'we are \(mmss(t)) into the flight'")
        }
        if model.scheduleStale { model.rebuildSchedule(now: now) }
        if t - lastPrint >= every {
            lastPrint = t
            let b = model.board(side: side, now: now)
            let e = b.estimate
            let tr = truth(min(max(t, 0), flown.end))
            let err = Geo.distKm(tr.lat, tr.lon, e.state.lat, e.state.lon)
            if t >= 0, t <= flown.end { errs.append(err) }
            let src: String
            switch e.source {
            case .gps: src = "GPS"
            case .deadReckoning: src = "est. (fix \(mmss(e.fixAge ?? 0)) old)"
            case .pinned: src = "pinned"
            case .schedule: src = "timetable"
            }
            print(String(format: "T+%@  %@ ±%@  plan %@  pos err %.1f km  %@ x%+.0f km%@%@  pace %.2f  alt %.0f ft",
                         mmss(t), src, mmss(e.sigma), mmss(e.state.t), err, e.pathID, e.crossKm,
                         e.offRoute ? " OFF-ROUTE(\(Int(e.nearestKm)) km)" : "", e.outsideReach ? " OUTSIDE" : "", e.pace, e.state.altFt))
            let names = b.now.prefix(6).map { r -> String in
                let f = pack.features[r.sighting.feature]
                return "\(f.name)\(r.maybe ? "?" : "") [\(whereWords(side: side, fa: r.sighting.fa, dep: r.sighting.dep).clock), \(Int(r.sighting.dKm)) km]"
            }
            print("       now (\(b.now.count)): " + (names.isEmpty ? "-" : names.joined(separator: "; ")))
            let nx = b.next.prefix(4).map { "\(pack.features[$0.headline.main.feature].name) in \(mmss($0.eta))" }
            print("       next: " + (nx.isEmpty ? "-" : nx.joined(separator: "; ")) + (b.over.map { "   | \($0)" } ?? ""))
        }
        t += 5
    }
    let shares = model.navigator.routeShares().prefix(3).map { "\($0.id) \(Int(($0.share * 100).rounded()))%" }
    print("routes at the end: " + shares.joined(separator: ", "))
    if !errs.isEmpty {
        print(String(format: "position error at the printed moments: median %.1f km, worst %.1f km", errs.sorted()[errs.count / 2], errs.max()!))
    }

default:
    fail("unknown command \(cmd)")
}
