import Foundation
import Metal
import Observation
import PlaneViewCore
import SceneKit
import UIKit

/// A name on a picture, placed in percent of its width and height.
struct ArtLabel {
    var name: String
    var x: Double
    var y: Double
    var main: Bool
}

/// The view from the window towards a sighting: the forecast's rendered picture when it made one, otherwise one
/// the app draws itself from the pack's imagery and terrain.
struct WindowArt {
    var image: UIImage?
    var coast: [[[Double]]] = []
    var lakes: [[[Double]]] = []
    var rivers: [[[Double]]] = []
    var borders: [[[Double]]] = []
    var labels: [ArtLabel] = []
    var drawnHere = false         // made on the phone, from where the aircraft is taken to be

    init(image: UIImage?, labels: [ArtLabel], drawnHere: Bool) {
        self.image = image; self.labels = labels; self.drawnHere = drawnHere
    }

    init?(plan: PlanSighting?, pack: Pack) {
        guard let v = plan?.view, let file = v.file else { return nil }
        image = PictureCache.image(pack.file(file))
        coast = v.coast ?? []; lakes = v.lakes ?? []; rivers = v.rivers ?? []; borders = v.borders ?? []
        labels = (v.labels ?? []).map { ArtLabel(name: $0.n, x: $0.x, y: $0.y, main: $0.main == true) }
    }
}

/// The place from straight above, turned the way you look at it (you are below the bottom edge).
struct MapArt {
    var image: UIImage?
    var km: Double
    var route: [[[Double]]] = []
    var up: Double                // compass bearing at the top of the picture
    var labels: [ArtLabel] = []
    var drawnHere = false

    init(image: UIImage?, km: Double, route: [[[Double]]], up: Double, labels: [ArtLabel], drawnHere: Bool) {
        self.image = image; self.km = km; self.route = route; self.up = up; self.labels = labels; self.drawnHere = drawnHere
    }

    init?(plan: PlanSighting?, pack: Pack) {
        guard let a = plan?.aerial, let file = a.file else { return nil }
        image = PictureCache.image(pack.file(file))
        km = a.km ?? 0
        route = a.route ?? []
        up = a.look?.up ?? 0
        labels = (a.labels ?? []).map { ArtLabel(name: $0.n, x: $0.x, y: $0.y, main: $0.main == true) }
    }
}

/// Draws pictures for sightings the forecast made none for - anything the app found itself, on any routing.
/// Works on its own queue; nothing here touches the screen.
final class ArtGenerator: @unchecked Sendable {
    let pack: Pack
    let spotter: Spotter
    private let queue = DispatchQueue(label: "planeview.art", qos: .userInitiated)
    private var renderer: SCNRenderer?
    private let scene = SCNScene()
    private let cameraNode = SCNNode()
    private var ground: (node: SCNNode, materials: [SCNMaterial], anchor: (lat: Double, lon: Double))?   // main thread only
    private var groundAnchor: (lat: Double, lon: Double)?       // the queue's note of the ground last built
    static let hfov = 56.0
    static let size = CGSize(width: 480, height: 600)       // 4:5, as the forecast's window views

    init(pack: Pack, spotter: Spotter) {
        self.pack = pack
        self.spotter = spotter
        let cam = SCNCamera()
        cam.zNear = 150
        cam.zFar = 700_000
        cam.projectionDirection = .horizontal
        cam.fieldOfView = Self.hfov
        cameraNode.camera = cam
        scene.rootNode.addChildNode(cameraNode)
    }

    func run(_ work: @escaping (ArtGenerator) -> Void) { queue.async { work(self) } }

    /// What a window view needs that can be worked out away from the screen: the ground round the aircraft (if a
    /// new one is due) and the other things in view from there.
    struct WindowJob {
        var sighting: Sighting
        var ac: AircraftState
        var newGround: (node: SCNNode, materials: [SCNMaterial], anchor: (lat: Double, lon: Double))?
        var near: [Sighting]
    }

    /// Off the main thread: build the ground if the aircraft has left the last one, and list what else is in view.
    /// (Nodes are only made here, not put in a scene: everything that touches the scene is done in `render`, on
    /// the main thread. Rendering from a second thread while the screen's own 3D view is coming or going can
    /// freeze the app.)
    func prepare(_ s: Sighting, from ac: AircraftState, unix: Double) -> WindowJob {
        var job = WindowJob(sighting: s, ac: ac, newGround: nil, near: [])
        if groundAnchor == nil || Geo.distKm(groundAnchor!.lat, groundAnchor!.lon, ac.lat, ac.lon) > 30 {
            let a = (lat: ac.lat, lon: ac.lon)
            let (node, mats) = WindowScene.Coordinator.build(pack: pack, anchor: a)
            job.newGround = (node, mats, a)
            groundAnchor = a
        }
        job.near = spotter.visible(from: ac, unix: unix)
            .filter { $0.side == s.side && $0.feature != s.feature && !pack.features[$0.feature].broad }
            .sorted { spotter.score($0) > spotter.score($1) }
        return job
    }

    /// On the main thread: the window view from the aircraft towards the best-seen point of the sighting, framed
    /// as the forecast frames its pictures (the place a little above the middle, the horizon in when it can be).
    @MainActor func render(_ job: WindowJob) -> WindowArt {
        HangWatch.mark("art: render window view")
        let s = job.sighting, ac = job.ac
        if let g = job.newGround {
            ground?.node.removeFromParentNode()
            scene.rootNode.addChildNode(g.node)
            ground = g
        }
        guard let ground else { return WindowArt(image: nil, labels: [], drawnHere: true) }
        WindowScene.Coordinator.style(scene, materials: ground.materials, light: s.light)
        let h = max(ac.altM, pack.groundHeight(ac.lat, ac.lon) + 30)
        let brg = Geo.bearing(ac.lat, ac.lon, s.plat, s.plon)
        let down = min(60, max(2, 0.6 * s.dep + 0.4 * Geo.dipDeg(h) + 8))
        cameraNode.position = WindowScene.Coordinator.place(ac.lat, ac.lon, h, from: ground.anchor)
        cameraNode.eulerAngles = SCNVector3(Float(-Geo.rad(down)), Float(-Geo.rad(brg)), 0)
        if renderer == nil {
            renderer = SCNRenderer(device: MTLCreateSystemDefaultDevice(), options: nil)
            renderer?.scene = scene
            renderer?.pointOfView = cameraNode
        }
        let image = renderer?.snapshot(atTime: 0, with: Self.size, antialiasingMode: .multisampling4X)

        // where things are in the picture (the camera's own arithmetic, so it needs no screen)
        let tanH = tan(Geo.rad(Self.hfov / 2)), tanV = tanH * Double(Self.size.height / Self.size.width)
        func project(_ lat: Double, _ lon: Double) -> (x: Double, y: Double)? {
            let p = cameraNode.convertPosition(WindowScene.Coordinator.place(lat, lon, pack.groundHeight(lat, lon), from: ground.anchor), from: nil)
            guard p.z < -1 else { return nil }
            return (50 + 50 * Double(p.x / -p.z) / tanH, 50 - 50 * Double(p.y / -p.z) / tanV)
        }
        var labels: [ArtLabel] = []
        if let m = project(s.plat, s.plon) { labels.append(ArtLabel(name: pack.features[s.feature].name, x: m.x, y: m.y, main: true)) }
        // a few of the best other things in view from there, for getting your bearings
        for o in job.near.prefix(30) {
            guard labels.count < 5, let q = project(o.plat, o.plon), q.x > 6, q.x < 94, q.y > 14, q.y < 94 else { continue }
            if labels.contains(where: { abs($0.y - q.y) < 9 && abs($0.x - q.x) < 45 }) { continue }
            labels.append(ArtLabel(name: pack.features[o.feature].name, x: q.x, y: q.y, main: false))
        }
        HangWatch.mark("art: window view done")
        return WindowArt(image: image, labels: labels, drawnHere: true)
    }

    /// The place from above, turned so that the way you look is up, with the path being flown across it.
    func map(_ s: Sighting, from ac: AircraftState, path: FlightPath, also: [(name: String, lat: Double, lon: Double)]) -> MapArt {
        let f = pack.features[s.feature]
        var size = 3.0
        if f.kind == "settlement" { size = 0.02 * max(f.pop, 500).squareRoot() + 0.3 }
        if let a = f.area, a > 0 { size = 2 * (a / .pi).squareRoot() }
        if ["coast", "sea", "river"].contains(f.kind) { size = max(size, 8) }
        if f.kind == "peak" { size = 6 }
        // the pack's map is coarser than the forecast's overhead images: show enough ground to recognise the setting
        let km = min(70, max(26, 1.5 * size + 2.5))
        let up = Geo.bearing(ac.lat, ac.lon, s.plat, s.plon)
        let S = 320.0
        let maxZ = pack.manifest.map?.max_z ?? 10, minZ = pack.manifest.map?.min_z ?? 5
        let lat = s.plat, lon = s.plon
        let outMPP = km * 1000 / (S * 2)                          // metres per output pixel (drawn at 2x)
        let zIdeal = Int((log2(156543.03 * cos(Geo.rad(lat)) / outMPP)).rounded(.up))
        let z = min(maxZ, max(minZ, zIdeal))
        func merc(_ la: Double, _ lo: Double, _ zz: Int) -> (x: Double, y: Double) {
            let n = Double(1 << zz) * 256
            let r = Geo.rad(min(85, max(-85, la)))
            return ((lo + 180) / 360 * n, (1 - log(tan(r) + 1 / cos(r)) / .pi) / 2 * n)
        }
        let c = merc(lat, lon, z)
        let k = (156543.03 * cos(Geo.rad(lat)) / Double(1 << z)) / (km * 1000 / S)      // tile pixels -> points
        let theta = -Geo.rad(up)
        let fmt = UIGraphicsImageRendererFormat()
        fmt.scale = 2; fmt.opaque = true
        let dir = pack.file("tiles")
        let image = UIGraphicsImageRenderer(size: CGSize(width: S, height: S), format: fmt).image { ctx in
            let g = ctx.cgContext
            UIColor(red: 0.06, green: 0.11, blue: 0.20, alpha: 1).setFill()
            g.fill(CGRect(x: 0, y: 0, width: S, height: S))
            g.interpolationQuality = .high
            g.translateBy(x: S / 2, y: S / 2)
            g.rotate(by: theta)
            // coarser levels first, so a missing fine tile still has ground under it
            for zz in max(minZ, z - 3)...z {
                let f2 = Double(1 << (z - zz))
                let kk = k * f2
                let cc = (x: c.x / f2, y: c.y / f2)
                let reach = S * 0.72 / kk
                let x0 = Int(((cc.x - reach) / 256).rounded(.down)), x1 = Int(((cc.x + reach) / 256).rounded(.down))
                let y0 = Int(((cc.y - reach) / 256).rounded(.down)), y1 = Int(((cc.y + reach) / 256).rounded(.down))
                guard x1 - x0 < 12, y1 - y0 < 12 else { continue }
                for ty in y0...y1 {
                    for tx in x0...x1 {
                        guard let img = WindowScene.Coordinator.image(dir.appendingPathComponent("\(zz)/\(tx)/\(ty).jpg")) else { continue }
                        // a hair over-size, so no seams show between tiles once turned
                        img.draw(in: CGRect(x: (Double(tx) * 256 - cc.x) * kk - 0.3, y: (Double(ty) * 256 - cc.y) * kk - 0.3,
                                            width: 256 * kk + 0.6, height: 256 * kk + 0.6))
                    }
                }
            }
        }
        func toPct(_ la: Double, _ lo: Double) -> (x: Double, y: Double) {
            let m = merc(la, lo, z)
            let dx = (m.x - c.x) * k, dy = (m.y - c.y) * k
            return ((dx * cos(theta) - dy * sin(theta) + S / 2) / S * 100, (dx * sin(theta) + dy * cos(theta) + S / 2) / S * 100)
        }
        var route: [[[Double]]] = [], seg: [[Double]] = []
        for t in stride(from: max(path.start, s.tBest - 900), through: min(path.end, s.tBest + 900), by: 20) {
            let q = path.state(at: t)
            let p = toPct(q.lat, q.lon)
            if p.x > -20, p.x < 120, p.y > -20, p.y < 120 { seg.append([p.x, p.y]) } else {
                if seg.count > 1 { route.append(seg) }
                seg = []
            }
        }
        if seg.count > 1 { route.append(seg) }
        var labels = [ArtLabel(name: f.name, x: 50, y: 50, main: true)]
        for a in also {
            let p = toPct(a.lat, a.lon)
            guard labels.count < 5, p.x > 8, p.x < 92, p.y > 8, p.y < 90 else { continue }
            if labels.contains(where: { abs($0.y - p.y) < 8 && abs($0.x - p.x) < 34 }) { continue }
            labels.append(ArtLabel(name: a.name, x: p.x, y: p.y, main: false))
        }
        return MapArt(image: image, km: km.rounded(), route: route, up: up, labels: labels, drawnHere: true)
    }
}

/// The pictures for a flight's sightings: the forecast's where it has them, the app's own for everything else,
/// made in the background and kept. A view that reads `version` is redrawn when a picture arrives.
@MainActor @Observable
final class ArtStore {
    private(set) var version = 0
    @ObservationIgnored private let pack: Pack
    @ObservationIgnored private let generator: ArtGenerator
    @ObservationIgnored private var windows: [String: (art: WindowArt, at: AircraftState)] = [:]
    @ObservationIgnored private var maps: [String: (art: MapArt, at: AircraftState)] = [:]
    @ObservationIgnored private var pending = Set<String>()

    init(pack: Pack, spotter: Spotter) {
        self.pack = pack
        generator = ArtGenerator(pack: pack, spotter: spotter)
    }

    /// A picture goes stale when the aircraft's place at that sighting has moved (a new routing, a late fix).
    private func fresh(_ a: AircraftState, _ b: AircraftState) -> Bool {
        Geo.distKm(a.lat, a.lon, b.lat, b.lon) < 8 && abs(a.altM - b.altM) < 600
    }

    /// The window view for a sighting at its best moment (`best`), seen from `ac`. Nil while the first one is drawn.
    func window(_ best: Sighting, plan: PlanSighting?, from ac: AircraftState, unix: Double) -> WindowArt? {
        if let a = WindowArt(plan: plan, pack: pack) { return a }
        let key = "w\(best.id)"
        let had = windows[key]
        if let had, fresh(had.at, ac) { return had.art }
        if !pending.contains(key) {
            pending.insert(key)
            generator.run { g in
                let job = g.prepare(best, from: ac, unix: unix)
                Task { @MainActor in
                    let art = g.render(job)
                    self.windows[key] = (art, ac)
                    self.pending.remove(key)
                    self.version += 1
                }
            }
        }
        return had?.art
    }

    func map(_ best: Sighting, plan: PlanSighting?, from ac: AircraftState, path: FlightPath, also: [Sighting]) -> MapArt? {
        if let a = MapArt(plan: plan, pack: pack) { return a }
        let key = "m\(best.id)"
        let had = maps[key]
        if let had, fresh(had.at, ac) { return had.art }
        if !pending.contains(key) {
            pending.insert(key)
            let names = also.prefix(6).map { (name: pack.features[$0.feature].name, lat: $0.plat, lon: $0.plon) }
            generator.run { g in
                let art = g.map(best, from: ac, path: path, also: names)
                Task { @MainActor in
                    self.maps[key] = (art, ac)
                    self.pending.remove(key)
                    self.version += 1
                }
            }
        }
        return had?.art
    }
}
