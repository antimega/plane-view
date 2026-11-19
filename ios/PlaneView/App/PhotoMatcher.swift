import Foundation
import Photos
import PlaneViewCore
import UIKit

struct MatchedPhoto: Identifiable {
    var id: String
    var asset: PHAsset
    var taken: Date
    var state: AircraftState
    var exact: Bool               // between two GPS fixes, not carried along the route
    var names: [String]           // what was in view on your side, best first
    var hasLocation: Bool
}

/// Photos taken with the iPhone's own camera during the flight, placed by their time against the GPS track:
/// where the aircraft was, and what was out of your window then.
@MainActor
enum PhotoMatcher {
    static func authorize() async -> Bool {
        let s = await PHPhotoLibrary.requestAuthorization(for: .readWrite)
        return s == .authorized || s == .limited
    }

    static func match(session: FlightSession) -> [MatchedPhoto] {
        let log = session.trackLog
        guard let first = log.rows.first?.wall, let last = log.rows.last?.wall else { return [] }
        let opts = PHFetchOptions()
        opts.predicate = NSPredicate(format: "creationDate >= %@ AND creationDate <= %@",
                                     first.addingTimeInterval(-60) as NSDate, last.addingTimeInterval(1200) as NSDate)
        opts.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: true)]
        let assets = PHAsset.fetchAssets(with: .image, options: opts)
        var out: [MatchedPhoto] = []
        let model = session.model
        let path = model.navigator.active(now: session.now)
        assets.enumerateObjects { asset, _, _ in
            guard let taken = asset.creationDate else { return }
            var state: AircraftState
            var when: Date
            var exact = true
            if let p = log.position(at: taken) {
                state = p.state; when = p.time
            } else {
                // no fix near that moment: carry the nearest one along the route
                guard let near = log.rows.min(by: { abs($0.wall.timeIntervalSince(taken)) < abs($1.wall.timeIntervalSince(taken)) }) else { return }
                let dt = taken.timeIntervalSince(near.wall)
                state = path.state(at: near.tau + dt * model.navigator.pace)
                when = near.fix.time.addingTimeInterval(dt)
                exact = false
            }
            let vis = model.spotter.visible(from: state, unix: when.timeIntervalSince1970)
                .filter { $0.side == session.side && !session.pack.features[$0.feature].broad }
                .sorted { model.spotter.score($0) > model.spotter.score($1) }
            var names: [String] = []
            for s in vis {
                let n = session.pack.features[s.feature].name
                if !names.contains(n) { names.append(n) }
                if names.count >= 6 { break }
            }
            out.append(MatchedPhoto(id: asset.localIdentifier, asset: asset, taken: taken, state: state, exact: exact,
                                    names: names, hasLocation: asset.location != nil))
        }
        return out
    }

    static func thumbnail(_ asset: PHAsset, side: CGFloat) async -> UIImage? {
        await withCheckedContinuation { cont in
            let o = PHImageRequestOptions()
            o.deliveryMode = .highQualityFormat
            o.isNetworkAccessAllowed = false
            o.resizeMode = .fast
            PHImageManager.default().requestImage(for: asset, targetSize: CGSize(width: side, height: side), contentMode: .aspectFill, options: o) { img, _ in
                cont.resume(returning: img)
            }
        }
    }

    /// Write the matched positions into the photos that have none (flight-mode photos often do not).
    static func addLocations(_ photos: [MatchedPhoto]) async -> Int {
        let todo = photos.filter { !$0.hasLocation && $0.exact }
        guard !todo.isEmpty else { return 0 }
        do {
            try await PHPhotoLibrary.shared().performChanges {
                for p in todo {
                    let r = PHAssetChangeRequest(for: p.asset)
                    r.location = CLLocation(coordinate: CLLocationCoordinate2D(latitude: p.state.lat, longitude: p.state.lon),
                                            altitude: p.state.altM, horizontalAccuracy: 50, verticalAccuracy: 50, timestamp: p.taken)
                }
            }
            return todo.count
        } catch {
            return 0
        }
    }

    /// The matches as JSON, for keeping or for other tools.
    static func export(_ photos: [MatchedPhoto], flight: String) -> URL? {
        let iso = ISO8601DateFormatter()
        let rows: [[String: Any]] = photos.map { p in
            let res = PHAssetResource.assetResources(for: p.asset).first
            return ["file": res?.originalFilename ?? p.id, "taken": iso.string(from: p.taken), "lat": p.state.lat, "lon": p.state.lon,
                    "alt_m": p.state.altM.rounded(), "course": p.state.course.rounded(), "from_gps": p.exact, "in_view": p.names]
        }
        guard let d = try? JSONSerialization.data(withJSONObject: ["flight": flight, "photos": rows], options: [.prettyPrinted, .sortedKeys]) else { return nil }
        let out = FileManager.default.temporaryDirectory.appendingPathComponent("\(flight)-photos.json")
        do { try d.write(to: out) } catch { return nil }
        return out
    }
}
