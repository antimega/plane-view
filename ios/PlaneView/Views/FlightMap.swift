import MapKit
import PlaneViewCore
import SwiftUI
import UIKit

/// The pack's satellite tiles, read from disk. Where the pack has no tile at a zoom (it carries detail only along
/// the corridor), the nearest coarser tile is enlarged, so the map never goes blank and never asks the network.
final class PackTileOverlay: MKTileOverlay {
    let dir: URL
    let minZ: Int
    let maxZ: Int
    private let cache = NSCache<NSString, NSData>()

    init(pack: Pack) {
        dir = pack.file("tiles")
        minZ = pack.manifest.map?.min_z ?? 5
        maxZ = pack.manifest.map?.max_z ?? 10
        super.init(urlTemplate: nil)
        canReplaceMapContent = true
        minimumZ = 3
        maximumZ = 14
        tileSize = CGSize(width: 256, height: 256)
    }

    private func file(_ z: Int, _ x: Int, _ y: Int) -> URL {
        dir.appendingPathComponent("\(z)/\(x)/\(y).jpg")
    }

    override func loadTile(at path: MKTileOverlayPath, result: @escaping (Data?, Error?) -> Void) {
        let key = "\(path.z)/\(path.x)/\(path.y)" as NSString
        if let d = cache.object(forKey: key) { result(d as Data, nil); return }
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            var out: Data? = nil
            if path.z <= maxZ, let d = try? Data(contentsOf: file(path.z, path.x, path.y)) {
                out = d
            } else {
                // climb to the nearest coarser tile there is, and cut out and enlarge our part of it
                var z = path.z, x = path.x, y = path.y
                while z > minZ {
                    z -= 1; x /= 2; y /= 2
                    guard z <= maxZ, let img = UIImage(contentsOfFile: file(z, x, y).path)?.cgImage else { continue }
                    let n = 1 << (path.z - z)
                    let w = CGFloat(img.width) / CGFloat(n), h = CGFloat(img.height) / CGFloat(n)
                    let rect = CGRect(x: CGFloat(path.x - x * n) * w, y: CGFloat(path.y - y * n) * h, width: w, height: h)
                    if let part = img.cropping(to: rect) {
                        let r = UIGraphicsImageRenderer(size: CGSize(width: 256, height: 256), format: { let f = UIGraphicsImageRendererFormat(); f.scale = 1; f.opaque = true; return f }())
                        out = r.jpegData(withCompressionQuality: 0.8) { ctx in
                            ctx.cgContext.interpolationQuality = .high
                            UIImage(cgImage: part).draw(in: CGRect(x: 0, y: 0, width: 256, height: 256))
                        }
                    }
                    break
                }
            }
            if out == nil { out = PackTileOverlay.blank }
            if let o = out { cache.setObject(o as NSData, forKey: key) }
            result(out, nil)
        }
    }

    /// Outside the pack's map: plain dark sea, not a grid of error tiles.
    static let blank: Data = {
        let f = UIGraphicsImageRendererFormat()
        f.scale = 1; f.opaque = true
        return UIGraphicsImageRenderer(size: CGSize(width: 256, height: 256), format: f).jpegData(withCompressionQuality: 0.7) { ctx in
            UIColor(red: 0.05, green: 0.09, blue: 0.16, alpha: 1).setFill()
            ctx.fill(CGRect(x: 0, y: 0, width: 256, height: 256))
        }
    }()
}

final class TaggedPolyline: MKPolyline { var tag = "" }

final class PlaceAnnotation: NSObject, MKAnnotation {
    @objc dynamic var coordinate: CLLocationCoordinate2D
    var title: String?
    var key: String
    var kind: Kind
    var rank: Float
    enum Kind { case inView, next, around, plane }
    init(key: String, coordinate: CLLocationCoordinate2D, title: String?, kind: Kind, rank: Float) {
        self.key = key; self.coordinate = coordinate; self.title = title; self.kind = kind; self.rank = rank
    }
}

/// A name on the map: text with a dark edge, never a box that hides the ground under it.
final class LabelView: MKAnnotationView {
    var shownAlpha: CGFloat = 1          // what declutter last asked for (alpha itself may be mid-fade)
    private let label = UILabel()
    private let dot = UIView()

    override init(annotation: MKAnnotation?, reuseIdentifier: String?) {
        super.init(annotation: annotation, reuseIdentifier: reuseIdentifier)
        frame = CGRect(x: 0, y: 0, width: 150, height: 30)
        centerOffset = CGPoint(x: 0, y: 9)
        dot.frame = CGRect(x: 71, y: 2, width: 8, height: 8)
        dot.layer.cornerRadius = 4
        dot.layer.borderColor = UIColor.white.cgColor
        dot.layer.borderWidth = 1.5
        addSubview(dot)
        label.frame = CGRect(x: 0, y: 11, width: 150, height: 17)
        label.textAlignment = .center
        label.font = .systemFont(ofSize: 12, weight: .semibold)
        label.textColor = .white
        label.layer.shadowColor = UIColor.black.cgColor
        label.layer.shadowRadius = 2
        label.layer.shadowOpacity = 1
        label.layer.shadowOffset = .zero
        addSubview(label)
        collisionMode = .rectangle
        isUserInteractionEnabled = false
    }

    required init?(coder: NSCoder) { fatalError() }

    func set(_ a: PlaceAnnotation, tint: UIColor) {
        label.text = a.title
        // a place that is merely there (not in view, not next) is lettered smaller and quieter
        label.font = .systemFont(ofSize: a.kind == .around ? 10.5 : 12, weight: a.kind == .around ? .medium : .semibold)
        // the view is as wide as its name and no wider: its frame is what the map keeps other names clear of,
        // and a fixed wide one would crowd out neighbours that would fit
        let w = max(24, min(170, ceil(label.intrinsicContentSize.width) + 6))
        bounds = CGRect(x: 0, y: 0, width: w, height: 30)
        let d: CGFloat = a.kind == .around ? 5 : 8
        dot.frame = CGRect(x: w / 2 - d / 2, y: 6 - d / 2, width: d, height: d)
        dot.layer.cornerRadius = d / 2
        label.frame = CGRect(x: 0, y: 11, width: w, height: 17)
        dot.backgroundColor = a.kind == .inView ? tint : .clear
        dot.layer.borderColor = (a.kind == .inView ? UIColor.white : a.kind == .around ? UIColor(white: 1, alpha: 0.7) : tint).cgColor
        dot.layer.borderWidth = a.kind == .around ? 1 : 1.5
        label.textColor = a.kind == .inView ? .white : UIColor(white: 1, alpha: a.kind == .around ? 0.72 : 0.82)
        // every name is kept by the map; which of them show is decided by the app (declutter), best first -
        // left to itself the map hides far more than overlap requires
        displayPriority = .required
    }
}

final class PlaneMarker: MKAnnotationView {
    private let glyph = UIImageView(image: UIImage(systemName: "airplane", withConfiguration: UIImage.SymbolConfiguration(pointSize: 24, weight: .bold)))

    override init(annotation: MKAnnotation?, reuseIdentifier: String?) {
        super.init(annotation: annotation, reuseIdentifier: reuseIdentifier)
        frame = CGRect(x: 0, y: 0, width: 36, height: 36)
        glyph.frame = bounds
        glyph.contentMode = .center
        glyph.tintColor = .white
        glyph.layer.shadowColor = UIColor.black.cgColor
        glyph.layer.shadowRadius = 2.5
        glyph.layer.shadowOpacity = 1
        glyph.layer.shadowOffset = .zero
        addSubview(glyph)
        displayPriority = .required
        isUserInteractionEnabled = false
    }

    required init?(coder: NSCoder) { fatalError() }

    /// The symbol points east; turn it to the course (the map is always north-up).
    func point(_ course: Double, certain: Bool) {
        glyph.transform = CGAffineTransform(rotationAngle: CGFloat((course - 90) * .pi / 180))
        glyph.alpha = certain ? 1 : 0.75
    }
}

/// The wedge of ground the window faces, and the stretch of path the aircraft might be on, drawn as layers over the
/// map and re-placed every frame. (As map overlays they had to be taken off and put back each time the aircraft
/// moved, and the map redraws overlays in tiles a moment later - which flickered.)
final class WedgeView: UIView {
    let far = CAShapeLayer(), near = CAShapeLayer(), unsure = CAShapeLayer()

    override init(frame: CGRect) {
        super.init(frame: frame)
        isUserInteractionEnabled = false
        backgroundColor = .clear
        clipsToBounds = true
        unsure.fillColor = nil
        unsure.strokeColor = UIColor(red: 0.95, green: 0.70, blue: 0.25, alpha: 0.55).cgColor
        unsure.lineWidth = 12
        unsure.lineCap = .round
        unsure.lineJoin = .round
        far.lineWidth = 1
        near.lineWidth = 1.5
        for l in [unsure, far, near] { layer.addSublayer(l) }
    }

    required init?(coder: NSCoder) { fatalError() }

    func tint(_ c: UIColor) {
        far.fillColor = c.withAlphaComponent(0.07).cgColor
        far.strokeColor = c.withAlphaComponent(0.45).cgColor
        near.fillColor = c.withAlphaComponent(0.20).cgColor
        near.strokeColor = c.withAlphaComponent(0.9).cgColor
    }
}

/// North-up satellite map of the corridor, offline: every route past flights took, the path being flown, the
/// aircraft with the wedge of ground your window faces, and names for what is in view and what comes next.
/// Zoom is the user's (pinch, double-tap, or the buttons) and is kept while the map follows the aircraft.
struct FlightMap: UIViewRepresentable {
    var session: FlightSession
    var board: Board
    var labels = 0          // 0 the best few places named, 1 more, 2 as many as fit

    func makeCoordinator() -> Coordinator { Coordinator(session: session) }

    func makeUIView(context: Context) -> MKMapView {
        HangWatch.mark("map: make")
        let map = MKMapView()
        map.delegate = context.coordinator
        map.isRotateEnabled = false
        map.isPitchEnabled = false
        map.showsCompass = false
        map.showsScale = true
        map.pointOfInterestFilter = .excludingAll
        map.overrideUserInterfaceStyle = .dark
        // not closer than the pack's imagery is worth looking at
        map.cameraZoomRange = MKMapView.CameraZoomRange(minCenterCoordinateDistance: 12_000, maxCenterCoordinateDistance: 6_000_000)
        map.register(LabelView.self, forAnnotationViewWithReuseIdentifier: "label")
        map.register(PlaneMarker.self, forAnnotationViewWithReuseIdentifier: "plane")
        let pack = session.pack
        map.addOverlay(PackTileOverlay(pack: pack), level: .aboveLabels)
        // where past flights went, and how far the pack's knowledge reaches
        for ring in pack.manifest.corridor.reach {
            let c = ring.map { CLLocationCoordinate2D(latitude: $0[0], longitude: $0[1]) }
            let p = TaggedPolyline(coordinates: c, count: c.count)
            p.tag = "reach"
            map.addOverlay(p, level: .aboveLabels)
        }
        for v in pack.paths where v.kind == "variant" {
            let c = v.pts.map { CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon) }
            let p = TaggedPolyline(coordinates: c, count: c.count)
            p.tag = "variant"
            map.addOverlay(p, level: .aboveLabels)
        }
        for v in pack.paths where v.kind == "history" {
            let c = v.pts.map { CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon) }
            let p = TaggedPolyline(coordinates: c, count: c.count)
            p.tag = "history"
            map.addOverlay(p, level: .aboveLabels)
        }
        let c = context.coordinator
        let wedge = WedgeView(frame: map.bounds)
        wedge.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        map.addSubview(wedge)
        c.wedgeView = wedge
        func round(_ symbol: String, _ label: String, _ action: Selector) -> UIButton {
            let b = UIButton(type: .system)
            b.setImage(UIImage(systemName: symbol, withConfiguration: UIImage.SymbolConfiguration(pointSize: 16, weight: .semibold)), for: .normal)
            b.backgroundColor = UIColor.black.withAlphaComponent(0.5)
            b.tintColor = .white
            b.layer.cornerRadius = 21
            b.accessibilityLabel = label
            b.addTarget(c, action: action, for: .touchUpInside)
            b.widthAnchor.constraint(equalToConstant: 42).isActive = true
            b.heightAnchor.constraint(equalToConstant: 42).isActive = true
            return b
        }
        let follow = round("location.north.line.fill", "Follow the aircraft", #selector(Coordinator.recentre))
        let stack = UIStackView(arrangedSubviews: [
            round("plus", "Zoom in", #selector(Coordinator.zoomIn)), round("minus", "Zoom out", #selector(Coordinator.zoomOut)), follow,
        ])
        stack.axis = .vertical
        stack.spacing = 8
        stack.translatesAutoresizingMaskIntoConstraints = false
        map.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.trailingAnchor.constraint(equalTo: map.trailingAnchor, constant: -10),
            stack.bottomAnchor.constraint(equalTo: map.bottomAnchor, constant: -10),
        ])
        c.followButton = follow
        // watch the fingers (alongside the map's own recognisers), so the app never pulls the map from under them
        let pan = UIPanGestureRecognizer(target: c, action: #selector(Coordinator.dragged(_:)))
        pan.maximumNumberOfTouches = 1
        let pinch = UIPinchGestureRecognizer(target: c, action: #selector(Coordinator.pinched(_:)))
        let double = UITapGestureRecognizer(target: c, action: #selector(Coordinator.tapZoomed(_:)))
        double.numberOfTapsRequired = 2
        let twoFinger = UITapGestureRecognizer(target: c, action: #selector(Coordinator.tapZoomed(_:)))
        twoFinger.numberOfTouchesRequired = 2
        for g in [pan, pinch, double, twoFinger] as [UIGestureRecognizer] {
            g.delegate = c
            g.cancelsTouchesInView = false
            map.addGestureRecognizer(g)
        }
        c.map = map
        c.startGliding()
        return map
    }

    func updateUIView(_ map: MKMapView, context: Context) {
        context.coordinator.labelLevel = labels
        context.coordinator.update(map, board: board, version: session.scheduleVersion)
    }

    static func dismantleUIView(_ map: MKMapView, coordinator: Coordinator) {
        HangWatch.mark("map: dismantle")
        coordinator.stopGliding()
    }

    final class Coordinator: NSObject, MKMapViewDelegate, UIGestureRecognizerDelegate {
        let session: FlightSession
        weak var map: MKMapView?
        weak var followButton: UIButton?
        var following = true
        var labelLevel = 0
        private var first = true
        private var plane: PlaceAnnotation?
        private var places: [String: PlaceAnnotation] = [:]
        weak var wedgeView: WedgeView?
        private var active: TaggedPolyline?
        private var unsureCoords: [CLLocationCoordinate2D] = []
        private var activeVersion = -1
        private var side = Side.R
        private var board: Board?
        // zoom: the app's own choice (it follows the height) until the user sets one, then theirs
        private var userZoom = false
        private var widthKm = 0.0            // how much ground the map shows across
        private var applied = 0.0            // the zoom last given to the map
        private var handsOn = false          // fingers are on the map: leave it alone
        private var holdUntil = Date.distantPast
        // the aircraft glides between the once-a-second positions instead of stepping
        private var link: CADisplayLink?
        private var glideFrom: AircraftState?
        private var glideTo: AircraftState?
        private var glideStart = CACurrentMediaTime()
        private var shown: AircraftState?
        private var lastDeclutter = 0.0
        private var aroundKey: (Double, Double, Double, Int)?
        private var aroundList: [Int] = []

        init(session: FlightSession) { self.session = session }

        // MARK: fingers

        func gestureRecognizer(_ g: UIGestureRecognizer, shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool { true }

        /// Touches on the buttons are the buttons' own (two quick presses of + are not a double-tap on the map).
        func gestureRecognizer(_ g: UIGestureRecognizer, shouldReceive touch: UITouch) -> Bool {
            var v = touch.view
            while let x = v {
                if x is UIControl { return false }
                v = x.superview
            }
            return true
        }

        /// A drag takes the map away from the aircraft; the follow button brings it back.
        @objc func dragged(_ g: UIGestureRecognizer) {
            if g.state == .began {
                following = false
                style()
            }
        }

        @objc func pinched(_ g: UIPinchGestureRecognizer) {
            switch g.state {
            case .began: handsOn = true
            case .ended, .cancelled, .failed:
                handsOn = false
                adoptZoom()
            default: break
            }
        }

        /// Double-tap and two-finger tap zoom with an animation: wait for it, then keep what it arrived at.
        @objc func tapZoomed(_ g: UITapGestureRecognizer) {
            holdUntil = Date().addingTimeInterval(0.6)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.55) { [weak self] in self?.adoptZoom() }
        }

        private func visibleWidthKm(_ map: MKMapView) -> Double {
            map.region.span.longitudeDelta * 111.195 * max(0.05, cos(map.region.center.latitude * .pi / 180))
        }

        private func adoptZoom() {
            guard let map else { return }
            userZoom = true
            widthKm = visibleWidthKm(map)
            applied = widthKm
        }

        @objc func zoomIn() { zoom(by: 0.6) }
        @objc func zoomOut() { zoom(by: 1 / 0.6) }

        private func zoom(by k: Double) {
            guard let map else { return }
            widthKm = min(4000, max(6, (widthKm > 0 ? widthKm : visibleWidthKm(map)) * k))
            userZoom = true
            let centre = following ? self.centre(for: shown ?? board?.state) ?? map.centerCoordinate : map.centerCoordinate
            holdUntil = Date().addingTimeInterval(0.4)          // let the zoom animate before following again
            map.setRegion(region(map, centre: centre, widthKm: widthKm), animated: true)
            applied = widthKm
        }

        /// Follow the aircraft again; pressed while already following, hand the zoom back to the app.
        @objc func recentre() {
            if following { userZoom = false; first = true }
            following = true
            if let m = map, let b = board { update(m, board: b, version: activeVersion) }
        }

        private func style() {
            followButton?.tintColor = following ? .white : UIColor(red: 1, green: 0.62, blue: 0.82, alpha: 1)
            followButton?.backgroundColor = following ? UIColor.black.withAlphaComponent(0.5) : UIColor(red: 0.71, green: 0.07, blue: 0.42, alpha: 0.9)
        }

        /// A region showing `widthKm` of ground across the map, whatever its shape.
        private func region(_ map: MKMapView, centre: CLLocationCoordinate2D, widthKm: Double) -> MKCoordinateRegion {
            let aspect = map.bounds.width > 0 ? Double(map.bounds.height / map.bounds.width) : 1.3
            return MKCoordinateRegion(center: centre, latitudinalMeters: widthKm * 1000 * aspect, longitudinalMeters: widthKm * 1000)
        }

        /// Where the map is centred while following: the aircraft sits towards the far edge, so the side the window
        /// faces has most of the map.
        private func centre(for s: AircraftState?) -> CLLocationCoordinate2D? {
            guard let s, widthKm > 0 else { return nil }
            let sign: Double = side == .R ? 1 : -1
            let mid = Geo.destination(s.lat, s.lon, bearing: s.course + sign * 70, km: 0.30 * widthKm)
            return CLLocationCoordinate2D(latitude: mid.lat, longitude: mid.lon)
        }

        // MARK: once a second

        func update(_ map: MKMapView, board: Board, version: Int) {
            HangWatch.mark("map: update")
            defer { HangWatch.mark("map: update done") }
            let pack = session.pack
            let s = board.state
            let sideChanged = side != board.side
            side = board.side
            self.board = board

            // the path being flown (replaced when it has been worked out afresh)
            if activeVersion != version || active == nil {
                activeVersion = version
                if let a = active { map.removeOverlay(a) }
                let path = session.model.navigator.active(now: session.now)
                let c = path.pts.map { CLLocationCoordinate2D(latitude: $0.lat, longitude: $0.lon) }
                let p = TaggedPolyline(coordinates: c, count: c.count)
                p.tag = "active"
                map.addOverlay(p, level: .aboveLabels)
                active = p
            }
            // how far along it the aircraft might really be
            unsureCoords = []
            if board.live, board.estimate.sigma > 45, board.estimate.phase == .airborne {
                let path = session.model.navigator.active(now: session.now)
                let t0 = board.estimate.state.t - board.estimate.sigma, t1 = board.estimate.state.t + board.estimate.sigma
                unsureCoords = stride(from: t0, through: t1, by: max(15, (t1 - t0) / 24)).map { t -> CLLocationCoordinate2D in
                    let q = path.state(at: t)
                    return CLLocationCoordinate2D(latitude: q.lat, longitude: q.lon)
                }
            }

            // names: what is in view (filled dot), what comes next (ring)
            var want: [String: (CLLocationCoordinate2D, String, PlaceAnnotation.Kind, Float)] = [:]
            // (the map itself drops a name that would sit on a better one, so "as many as fit" fills in as you zoom)
            let level = min(2, max(0, labelLevel))
            for (i, r) in board.now.filter({ !$0.maybe }).prefix([14, 40, 120][level]).enumerated() {
                let f = pack.features[r.sighting.feature]
                want["n" + f.id] = (CLLocationCoordinate2D(latitude: r.sighting.plat, longitude: r.sighting.plon), f.name, .inView, Float(900 - min(i, 250)))
            }
            for (i, r) in board.next.prefix([5, 10, 20][level]).enumerated() {
                let m = r.headline.main
                let f = pack.features[m.feature]
                if want["n" + f.id] == nil {
                    want["x" + f.id] = (CLLocationCoordinate2D(latitude: m.plat, longitude: m.plon), f.name, .next, Float(600 - i))
                }
            }
            // "more": the notable places on the map whether or not the window shows them, to get your bearings by
            if level > 0 {
                for (i, fi) in around(map, count: [0, 60, 220][level]).enumerated() {
                    let f = pack.features[fi]
                    if want["n" + f.id] == nil, want["x" + f.id] == nil {
                        want["a" + f.id] = (CLLocationCoordinate2D(latitude: f.lat, longitude: f.lon), f.name, .around, Float(400 - min(i, 390)))
                    }
                }
            }
            for (k, a) in places where want[k] == nil {
                map.removeAnnotation(a)
                places[k] = nil
            }
            for (k, w) in want {
                if let a = places[k] {
                    if a.coordinate.latitude != w.0.latitude || a.coordinate.longitude != w.0.longitude { a.coordinate = w.0 }
                    a.rank = w.3
                } else {
                    let a = PlaceAnnotation(key: k, coordinate: w.0, title: w.1, kind: w.2, rank: w.3)
                    places[k] = a
                    map.addAnnotation(a)
                }
            }

            declutter(force: true)

            // where the aircraft is going next: glide there over the second until the next position comes. A big
            // step (the scrub bar, a new fix after a long gap) is a jump, not a glide.
            let from = shown ?? s
            let jump = Geo.distKm(from.lat, from.lon, s.lat, s.lon) > 40 || shown == nil || sideChanged
            glideFrom = jump ? s : from
            glideTo = s
            glideStart = CACurrentMediaTime()
            if jump { place(s, on: map, force: true) }
            if plane == nil {
                let p = PlaceAnnotation(key: "plane", coordinate: CLLocationCoordinate2D(latitude: s.lat, longitude: s.lon), title: nil, kind: .plane, rank: 1000)
                plane = p
                map.addAnnotation(p)
            }

            // zoom: the app's while it is left alone (it widens with height), the user's once they set it
            style()
            guard map.bounds.width > 0 else { return }
            let near = min(70, max(8, 12 * s.altFt / 10000))
            let auto = max(70, min(300, 1.7 * min(150, max(20, 3 * near))))
            if first || widthKm == 0 || !userZoom { widthKm = auto }
            guard following, !handsOn, Date() >= holdUntil else { return }
            if first || abs(widthKm - applied) > 0.03 * applied, let c = centre(for: shown ?? s) {
                holdUntil = Date().addingTimeInterval(first ? 0 : 0.4)
                map.setRegion(region(map, centre: c, widthKm: widthKm), animated: !first)
                applied = widthKm
            }
            first = false
            declutter(force: true)
        }

        /// The most notable features on the part of the Earth the map shows (worked out again only when the map
        /// has moved or zoomed appreciably: a long-haul pack has a hundred thousand).
        private func around(_ map: MKMapView, count: Int) -> [Int] {
            let r = map.region
            let key = (r.center.latitude, r.center.longitude, r.span.latitudeDelta, count)
            if let k = aroundKey, k.3 == count, abs(k.2 - key.2) < 0.1 * key.2,
               abs(k.0 - key.0) < 0.15 * key.2, abs(k.1 - key.1) < 0.15 * r.span.longitudeDelta { return aroundList }
            let la0 = r.center.latitude - r.span.latitudeDelta * 0.7, la1 = r.center.latitude + r.span.latitudeDelta * 0.7
            let lo0 = r.center.longitude - r.span.longitudeDelta * 0.7, lo1 = r.center.longitude + r.span.longitudeDelta * 0.7
            var hits: [(Int, Double)] = []
            for (i, f) in session.pack.features.enumerated() where f.lat > la0 && f.lat < la1 && f.lon > lo0 && f.lon < lo1 && !f.broad {
                // towns are what you get your bearings by: they come before a lighthouse of the same renown
                hits.append((i, f.salience + (f.kind == "settlement" && f.pop >= 20000 ? 1.5 : 0)))
            }
            hits.sort { $0.1 > $1.1 }
            aroundKey = key
            aroundList = hits.prefix(count).map { $0.0 }
            return aroundList
        }

        /// Show the names that fit, best first: one that would sit on a better one fades out, and comes back when
        /// zooming in has made room.
        private func declutter(force: Bool = false) {
            guard let map, force || CACurrentMediaTime() - lastDeclutter > 0.15 else { return }
            lastDeclutter = CACurrentMediaTime()
            var used: [CGRect] = []
            if let p = plane { let c = map.convert(p.coordinate, toPointTo: map); used.append(CGRect(x: c.x - 14, y: c.y - 14, width: 28, height: 28)) }
            // keep names out from under the buttons (the zoom stack, and the two at the top right)
            for v in map.subviews where v is UIStackView { used.append(v.frame.insetBy(dx: -4, dy: -4)) }
            used.append(CGRect(x: map.bounds.width - 116, y: 0, width: 116, height: 62))
            for a in places.values.sorted(by: { $0.rank > $1.rank }) {
                guard let v = map.view(for: a) as? LabelView else { continue }
                let pt = map.convert(a.coordinate, toPointTo: map)
                let w = v.bounds.width
                let r = CGRect(x: pt.x - w / 2, y: pt.y - 5, width: w, height: 28)
                let clash = used.contains { $0.insetBy(dx: -3, dy: -1).intersects(r) }
                if !clash { used.append(r) }
                let target: CGFloat = clash ? 0 : 1
                if v.shownAlpha != target {
                    v.shownAlpha = target
                    UIView.animate(withDuration: 0.2, delay: 0, options: [.beginFromCurrentState]) { v.alpha = target }
                }
            }
        }

        func mapView(_ mapView: MKMapView, regionDidChangeAnimated animated: Bool) { declutter() }

        func mapView(_ mapView: MKMapView, didAdd views: [MKAnnotationView]) {
            for v in views { if let l = v as? LabelView { l.alpha = 0; l.shownAlpha = 0 } }
            declutter(force: true)
        }

        // MARK: every frame

        func startGliding() {
            let l = CADisplayLink(target: self, selector: #selector(glide))
            l.preferredFrameRateRange = CAFrameRateRange(minimum: 15, maximum: 30, preferred: 30)
            l.add(to: .main, forMode: .common)
            link = l
        }

        func stopGliding() {
            link?.invalidate()
            link = nil
        }

        @objc private func glide() {
            guard let map, let a = glideFrom, let b = glideTo else { return }
            let f = min(1, max(0, (CACurrentMediaTime() - glideStart) / 1.0))
            if f >= 1, let sh = shown, sh == b { return }           // arrived: nothing to move
            let s = AircraftState(t: a.t + (b.t - a.t) * f, lat: a.lat + (b.lat - a.lat) * f, lon: a.lon + Geo.wrap180(b.lon - a.lon) * f,
                                  altM: a.altM + (b.altM - a.altM) * f, course: Geo.wrap360(a.course + Geo.wrap180(b.course - a.course) * f))
            place(s, on: map, force: f >= 1)
        }

        /// Put the aircraft, its wedge and (while following) the map at one position.
        private func place(_ s: AircraftState, on map: MKMapView, force: Bool) {
            shown = s
            let here = CLLocationCoordinate2D(latitude: s.lat, longitude: s.lon)
            if let p = plane {
                p.coordinate = here
                if let v = map.view(for: p) as? PlaneMarker, let b = board {
                    v.point(s.course, certain: !b.live || b.estimate.source == .gps)
                }
            }
            if following, !handsOn, Date() >= holdUntil, applied > 0, let c = centre(for: s) {
                map.setCenter(c, animated: false)
            }
            drawWedge(s, on: map)
        }

        /// The ground the window faces: the forecast's cone. The inner part is where most named things are
        /// recognised (12 km per 10,000 ft); the faint outer part is as far as big things carry.
        private func drawWedge(_ s: AircraftState, on map: MKMapView) {
            guard let w = wedgeView else { return }
            CATransaction.begin()
            CATransaction.setDisableActions(true)
            defer { CATransaction.commit() }
            w.tint(UIColor(Theme.side(side)).resolvedColor(with: UITraitCollection(userInterfaceStyle: .dark)))
            func point(_ c: CLLocationCoordinate2D) -> CGPoint { map.convert(c, toPointTo: w) }
            if unsureCoords.count > 1 {
                let p = CGMutablePath()
                p.addLines(between: unsureCoords.map(point))
                w.unsure.path = p
            } else {
                w.unsure.path = nil
            }
            guard let b = board, !b.live || b.estimate.phase == .airborne else {
                w.far.path = nil; w.near.path = nil
                return
            }
            let v = session.pack.manifest.view
            let near = min(70, max(8, 12 * s.altFt / 10000))
            let far = min(150, max(20, 3 * near))
            let sign: Double = side == .R ? 1 : -1
            let here = point(CLLocationCoordinate2D(latitude: s.lat, longitude: s.lon))
            for (reach, layer) in [(far, w.far), (near, w.near)] {
                let p = CGMutablePath()
                p.move(to: here)
                for fa in stride(from: v.fa_max, through: v.fa_min, by: -4) {
                    let q = Geo.destination(s.lat, s.lon, bearing: s.course + sign * (90 - fa), km: reach)
                    p.addLine(to: point(CLLocationCoordinate2D(latitude: q.lat, longitude: q.lon)))
                }
                p.closeSubpath()
                layer.path = p
            }
        }

        /// The map is moving under the fingers (or zooming): keep the wedge on the ground.
        func mapViewDidChangeVisibleRegion(_ mapView: MKMapView) {
            if let s = shown ?? board?.state { drawWedge(s, on: mapView) }
        }

        // MARK: drawing

        func mapView(_ mapView: MKMapView, rendererFor overlay: MKOverlay) -> MKOverlayRenderer {
            if let t = overlay as? MKTileOverlay { return MKTileOverlayRenderer(tileOverlay: t) }
            if let p = overlay as? TaggedPolyline {
                let r = MKPolylineRenderer(polyline: p)
                switch p.tag {
                case "active":
                    r.strokeColor = UIColor(red: 1, green: 0.35, blue: 0.68, alpha: 1); r.lineWidth = 3
                case "variant":
                    r.strokeColor = UIColor(red: 1, green: 0.35, blue: 0.68, alpha: 0.55); r.lineWidth = 1.5; r.lineDashPattern = [6, 5]
                case "history":
                    r.strokeColor = UIColor(white: 1, alpha: 0.06); r.lineWidth = 1
                default:
                    r.strokeColor = UIColor(white: 1, alpha: 0.45); r.lineWidth = 1; r.lineDashPattern = [2, 6]
                }
                return r
            }
            return MKOverlayRenderer(overlay: overlay)
        }

        func mapView(_ mapView: MKMapView, viewFor annotation: MKAnnotation) -> MKAnnotationView? {
            guard let a = annotation as? PlaceAnnotation else { return nil }
            if a.kind == .plane {
                let v = mapView.dequeueReusableAnnotationView(withIdentifier: "plane", for: a) as! PlaneMarker
                if let b = board { v.point((shown ?? b.state).course, certain: !b.live || b.estimate.source == .gps) }
                return v
            }
            let v = mapView.dequeueReusableAnnotationView(withIdentifier: "label", for: a) as! LabelView
            v.set(a, tint: UIColor(Theme.side(side)).resolvedColor(with: UITraitCollection(userInterfaceStyle: .dark)))
            return v
        }
    }
}
