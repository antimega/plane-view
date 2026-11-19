import PlaneViewCore
import SceneKit
import SwiftUI
import UIKit

/// The view out of your window in 3D, from where the aircraft is: the pack's satellite imagery laid over its
/// terrain on a curved Earth, seen from the aircraft's height in the direction the window faces, with the names
/// of what is in view. Drag to look round, pinch to zoom, double-tap to look straight out again.
///
/// The ground is built from the map tiles as a quadtree round the aircraft - fine tiles close by, coarse ones out
/// to the horizon - each a small mesh with its corners lifted to the terrain and dropped by the Earth's curve
/// (with refraction, as the forecast does). It is rebuilt when the aircraft has moved 30 km.
struct WindowScene: UIViewRepresentable {
    var session: FlightSession
    var board: Board
    var labels = 0          // 0 the best few places named, 1 more, 2 as many as fit

    func makeCoordinator() -> Coordinator { Coordinator(session: session) }

    func makeUIView(context: Context) -> SCNView {
        HangWatch.mark("3d: make")
        let v = SCNView(frame: .zero)
        v.antialiasingMode = .multisampling4X
        v.isUserInteractionEnabled = true
        v.backgroundColor = .black
        context.coordinator.setUp(v)
        return v
    }

    func updateUIView(_ v: SCNView, context: Context) {
        HangWatch.mark("3d: update")
        context.coordinator.labelLevel = labels
        context.coordinator.update(board)
        HangWatch.mark("3d: update done")
    }

    static func dismantleUIView(_ v: SCNView, coordinator: Coordinator) {
        // stop it drawing before it goes: nothing should be rendering this scene while it is taken apart
        HangWatch.mark("3d: dismantle")
        v.delegate = nil
        v.isPlaying = false
        v.scene = nil
        HangWatch.mark("3d: dismantle done")
    }

    final class Coordinator: NSObject, SCNSceneRendererDelegate, UIGestureRecognizerDelegate {
        let session: FlightSession
        private weak var view: SCNView?
        private let scene = SCNScene()
        private let cameraNode = SCNNode()
        private var ground: SCNNode?
        private var anchor: (lat: Double, lon: Double)?
        private var building = false
        private var board: Board?
        private var shown: AircraftState?
        private var materials: [SCNMaterial] = []
        private var light = Light.day
        // where the user has turned the view from straight out of the window
        private var yawOff = 0.0, pitchOff = 0.0, fov = 56.0
        private var labels: [String: UILabel] = [:]
        private var showing = Set<String>()
        var labelLevel = 0
        private let nose = UILabel()
        private let caption = UILabel()
        private static let queue = DispatchQueue(label: "planeview.scene", qos: .userInitiated)
        private static let images = NSCache<NSString, UIImage>()

        init(session: FlightSession) { self.session = session }

        func setUp(_ v: SCNView) {
            view = v
            v.scene = scene
            v.delegate = self
            let cam = SCNCamera()
            cam.zNear = 150
            cam.zFar = 700_000
            cam.projectionDirection = .horizontal
            cam.fieldOfView = fov
            cameraNode.camera = cam
            scene.rootNode.addChildNode(cameraNode)
            v.pointOfView = cameraNode
            applyLight(.day)
            let dt = UITapGestureRecognizer(target: self, action: #selector(reset))
            dt.numberOfTapsRequired = 2
            for g in [UIPanGestureRecognizer(target: self, action: #selector(panned(_:))),
                      UIPinchGestureRecognizer(target: self, action: #selector(pinched(_:))), dt] as [UIGestureRecognizer] {
                g.delegate = self
                v.addGestureRecognizer(g)
            }
            // the same buttons as the flat map, in the same place: zoom in, zoom out, and back to straight out
            func round(_ symbol: String, _ label: String, _ action: Selector) -> UIButton {
                let b = UIButton(type: .system)
                b.setImage(UIImage(systemName: symbol, withConfiguration: UIImage.SymbolConfiguration(pointSize: 16, weight: .semibold)), for: .normal)
                b.backgroundColor = UIColor.black.withAlphaComponent(0.5)
                b.tintColor = .white
                b.layer.cornerRadius = 21
                b.accessibilityLabel = label
                b.addTarget(self, action: action, for: .touchUpInside)
                b.widthAnchor.constraint(equalToConstant: 42).isActive = true
                b.heightAnchor.constraint(equalToConstant: 42).isActive = true
                return b
            }
            let stack = UIStackView(arrangedSubviews: [
                round("plus", "Zoom in", #selector(zoomIn)), round("minus", "Zoom out", #selector(zoomOut)),
                round("scope", "Look straight out of the window", #selector(reset)),
            ])
            stack.axis = .vertical
            stack.spacing = 8
            stack.translatesAutoresizingMaskIntoConstraints = false
            v.addSubview(stack)
            NSLayoutConstraint.activate([
                stack.trailingAnchor.constraint(equalTo: v.trailingAnchor, constant: -10),
                stack.bottomAnchor.constraint(equalTo: v.bottomAnchor, constant: -10),
            ])
            for l in [nose, caption] {
                l.font = .systemFont(ofSize: 10, weight: .semibold)
                l.textColor = .white
                l.layer.shadowColor = UIColor.black.cgColor
                l.layer.shadowRadius = 2
                l.layer.shadowOpacity = 1
                l.layer.shadowOffset = .zero
                v.addSubview(l)
            }
        }

        // MARK: light

        private func applyLight(_ l: Light) {
            light = l
            Self.style(scene, materials: materials, light: l)
        }

        /// Sky, haze and how bright the ground is, for the time of day.
        static func style(_ scene: SCNScene, materials: [SCNMaterial], light l: Light) {
            let haze: UIColor, top: UIColor, dim: CGFloat
            switch l {
            case .day: haze = UIColor(red: 0.80, green: 0.86, blue: 0.91, alpha: 1); top = UIColor(red: 0.33, green: 0.52, blue: 0.78, alpha: 1); dim = 1
            case .twilight: haze = UIColor(red: 0.55, green: 0.50, blue: 0.60, alpha: 1); top = UIColor(red: 0.16, green: 0.20, blue: 0.38, alpha: 1); dim = 0.55
            case .night: haze = UIColor(red: 0.05, green: 0.07, blue: 0.13, alpha: 1); top = UIColor(red: 0.02, green: 0.03, blue: 0.07, alpha: 1); dim = 0.22
            }
            scene.fogColor = haze
            scene.fogStartDistance = 25_000
            scene.fogEndDistance = 340_000
            scene.fogDensityExponent = 1.4
            let size = CGSize(width: 4, height: 256)
            let f = UIGraphicsImageRendererFormat()
            f.scale = 1; f.opaque = true
            scene.background.contents = UIGraphicsImageRenderer(size: size, format: f).image { ctx in
                let g = CGGradient(colorsSpace: nil, colors: [top.cgColor, haze.cgColor, haze.cgColor] as CFArray, locations: [0, 0.42, 1])!
                ctx.cgContext.drawLinearGradient(g, start: .zero, end: CGPoint(x: 0, y: size.height), options: [])
            }
            for m in materials { m.multiply.contents = UIColor(white: dim, alpha: 1) }
        }

        // MARK: geometry

        /// East-up-south metres from the anchor, dropped by the curve of the Earth.
        static func place(_ lat: Double, _ lon: Double, _ h: Double, from a: (lat: Double, lon: Double)) -> SCNVector3 {
            let d = Geo.distKm(a.lat, a.lon, lat, lon) * 1000
            let b = Geo.rad(Geo.bearing(a.lat, a.lon, lat, lon))
            return SCNVector3(Float(d * sin(b)), Float(h - d * d / (2 * Geo.rEff)), Float(-d * cos(b)))
        }

        static func tileLatLon(_ z: Int, _ x: Double, _ y: Double) -> (lat: Double, lon: Double) {
            let n = Double(1 << z)
            return (Geo.deg(atan(sinh(.pi * (1 - 2 * y / n)))), x / n * 360 - 180)
        }

        /// The tiles to draw round a position: split while a tile is close for its size, so detail falls off with distance.
        static func leaves(lat: Double, lon: Double, maxZ: Int, radiusKm: Double) -> [(z: Int, x: Int, y: Int)] {
            var out: [(Int, Int, Int)] = []
            func visit(_ z: Int, _ x: Int, _ y: Int) {
                let n = 1 << z
                guard y >= 0, y < n else { return }
                let c = tileLatLon(z, Double(x) + 0.5, Double(y) + 0.5)
                let size = 40075 * cos(Geo.rad(c.lat)) / Double(n)
                let d = max(0, Geo.distKm(lat, lon, c.lat, c.lon) - size * 0.71)
                if d > radiusKm { return }
                if z < maxZ, d < size * 1.5 {
                    for (dx, dy) in [(0, 0), (1, 0), (0, 1), (1, 1)] { visit(z + 1, x * 2 + dx, y * 2 + dy) }
                } else {
                    out.append((z, ((x % n) + n) % n, y))
                }
            }
            let z0 = 6, n0 = 1 << z0
            let cx = Int((lon + 180) / 360 * Double(n0))
            let la = Geo.rad(lat)
            let cy = Int((1 - log(tan(la) + 1 / cos(la)) / .pi) / 2 * Double(n0))
            for dx in -1...1 { for dy in -1...1 { visit(z0, cx + dx, cy + dy) } }
            return out
        }

        static func image(_ url: URL) -> UIImage? {
            let key = url.path as NSString
            if let i = images.object(forKey: key) { return i }
            guard let i = UIImage(contentsOfFile: url.path) else { return nil }
            images.setObject(i, forKey: key)
            return i
        }

        /// Build the ground round an anchor (off the main thread).
        static func build(pack: Pack, anchor: (lat: Double, lon: Double)) -> (SCNNode, [SCNMaterial]) {
            let root = SCNNode()
            let tilesDir = pack.file("tiles")
            let minZ = pack.manifest.map?.min_z ?? 5, maxZ = pack.manifest.map?.max_z ?? 10
            var byImage: [String: SCNMaterial] = [:]
            let sea = SCNMaterial()
            sea.lightingModel = .constant
            sea.diffuse.contents = UIColor(red: 0.06, green: 0.11, blue: 0.20, alpha: 1)
            sea.isDoubleSided = true
            var all = [sea]
            let N = 12
            for t in leaves(lat: anchor.lat, lon: anchor.lon, maxZ: maxZ, radiusKm: 340) {
                // the tile's own picture, or the part of the nearest coarser one that covers it
                var mat = sea
                var u0 = 0.0, v0 = 0.0, du = 1.0
                var zz = t.z, xx = t.x, yy = t.y
                while zz >= minZ {
                    let path = "\(zz)/\(xx)/\(yy).jpg"
                    if let m = byImage[path] { mat = m } else if zz <= maxZ, let img = image(tilesDir.appendingPathComponent(path)) {
                        let m = SCNMaterial()
                        m.lightingModel = .constant
                        m.diffuse.contents = img
                        m.diffuse.wrapS = .clamp; m.diffuse.wrapT = .clamp
                        m.diffuse.mipFilter = .linear
                        m.diffuse.intensity = 1.25                 // the mosaic is dark; the forecast's pictures lift it too
                        m.isDoubleSided = true
                        byImage[path] = m
                        all.append(m)
                        mat = m
                    }
                    if mat !== sea {
                        let k = Double(1 << (t.z - zz))
                        du = 1 / k
                        u0 = Double(t.x - (xx << (t.z - zz))) / k
                        v0 = Double(t.y - (yy << (t.z - zz))) / k
                        break
                    }
                    zz -= 1; xx /= 2; yy /= 2
                }
                var verts: [SCNVector3] = [], uvs: [CGPoint] = [], idx: [Int32] = []
                verts.reserveCapacity((N + 1) * (N + 1))
                for j in 0...N {
                    for i in 0...N {
                        let u = Double(i) / Double(N), v = Double(j) / Double(N)
                        let p = tileLatLon(t.z, Double(t.x) + u, Double(t.y) + v)
                        verts.append(place(p.lat, p.lon, mat === sea ? 0 : pack.groundHeight(p.lat, p.lon), from: anchor))
                        uvs.append(CGPoint(x: u0 + u * du, y: v0 + v * du))
                    }
                }
                for j in 0..<N {
                    for i in 0..<N {
                        let a = Int32(j * (N + 1) + i), b = a + 1, c = a + Int32(N + 1), d = c + 1
                        idx += [a, c, b, b, c, d]
                    }
                }
                let geo = SCNGeometry(sources: [SCNGeometrySource(vertices: verts), SCNGeometrySource(textureCoordinates: uvs)],
                                      elements: [SCNGeometryElement(indices: idx, primitiveType: .triangles)])
                geo.materials = [mat]
                root.addChildNode(SCNNode(geometry: geo))
            }
            return (root, all)
        }

        // MARK: once a second

        func update(_ b: Board) {
            board = b
            let s = b.state
            if b.light != light { applyLight(b.light) }
            // (re)build the ground when there is none, or the aircraft has left the middle of it
            if !building, anchor == nil || Geo.distKm(anchor!.lat, anchor!.lon, s.lat, s.lon) > 30 {
                building = true
                let pack = session.pack, a = (lat: s.lat, lon: s.lon)
                Self.queue.async { [weak self] in
                    let (node, mats) = Self.build(pack: pack, anchor: a)
                    DispatchQueue.main.async {
                        guard let self else { return }
                        self.ground?.removeFromParentNode()
                        self.scene.rootNode.addChildNode(node)
                        self.ground = node
                        self.materials = mats
                        self.anchor = a
                        self.building = false
                        self.applyLight(self.light)
                        self.shown = nil                     // the origin moved: place the camera afresh
                        if let b = self.board { self.pose(b.state, animated: false) }
                    }
                }
            }
            let jump = shown == nil || Geo.distKm(shown!.lat, shown!.lon, s.lat, s.lon) > 40
            pose(s, animated: !jump)
            caption.text = "from \(Fmt.feet(s.altFt)) · drag to look round"
            nose.text = b.side == .L ? "NOSE ›" : "‹ NOSE"
            layoutChrome()
        }

        /// Put the camera at the aircraft, looking out of the window (turned by whatever the user has dragged).
        private func pose(_ s: AircraftState, animated: Bool, duration: Double = 1.0) {
            guard let anchor, let b = board else { return }
            shown = s
            let sign: Double = b.side == .R ? 1 : -1
            let heading = s.course + sign * 70 + yawOff
            let down = min(60, max(-8, 13 + pitchOff))
            SCNTransaction.begin()
            SCNTransaction.animationDuration = animated ? duration : 0
            SCNTransaction.animationTimingFunction = CAMediaTimingFunction(name: .linear)
            cameraNode.position = Self.place(s.lat, s.lon, max(s.altM, pack.groundHeight(s.lat, s.lon) + 30), from: anchor)
            cameraNode.eulerAngles = SCNVector3(Float(-Geo.rad(down)), Float(-Geo.rad(heading)), 0)
            cameraNode.camera?.fieldOfView = fov
            SCNTransaction.commit()
        }

        private var pack: Pack { session.pack }

        // MARK: fingers

        /// Touches on the buttons are the buttons' own (two quick presses of + are not a double-tap on the view).
        func gestureRecognizer(_ g: UIGestureRecognizer, shouldReceive touch: UITouch) -> Bool {
            var v = touch.view
            while let x = v {
                if x is UIControl { return false }
                v = x.superview
            }
            return true
        }

        @objc private func panned(_ g: UIPanGestureRecognizer) {
            guard let v = view, v.bounds.width > 0 else { return }
            let t = g.translation(in: v)
            g.setTranslation(.zero, in: v)
            let perPoint = fov / Double(v.bounds.width)
            yawOff = min(60, max(-60, yawOff - Double(t.x) * perPoint))
            pitchOff = min(47, max(-21, pitchOff - Double(t.y) * perPoint))
            if let s = shown { pose(s, animated: false) }
        }

        @objc private func pinched(_ g: UIPinchGestureRecognizer) {
            fov = min(75, max(14, fov / Double(g.scale)))
            g.scale = 1
            if let s = shown { pose(s, animated: false) }
        }

        @objc private func reset() {
            yawOff = 0; pitchOff = 0; fov = 56
            if let s = shown { pose(s, animated: true, duration: 0.3) }
        }

        @objc private func zoomIn() { zoom(by: 0.7) }
        @objc private func zoomOut() { zoom(by: 1 / 0.7) }

        /// Zoom is the camera's angle of view: 14 degrees (a long lens) to 75 (wider than the window).
        private func zoom(by k: Double) {
            fov = min(75, max(14, fov * k))
            if let s = shown { pose(s, animated: true, duration: 0.3) }
        }

        // MARK: names

        func renderer(_ renderer: SCNSceneRenderer, didRenderScene scene: SCNScene, atTime time: TimeInterval) {
            DispatchQueue.main.async { [weak self] in self?.layoutLabels() }
        }

        private func layoutChrome() {
            guard let v = view else { return }
            nose.sizeToFit(); caption.sizeToFit()
            nose.center = CGPoint(x: board?.side == .L ? v.bounds.width - 152 : 40, y: 31)     // clear of the two buttons
            caption.center = CGPoint(x: v.bounds.midX, y: v.bounds.height - 14)
        }

        /// Names for the sighting in focus and the best of what is in view. Each name stays fixed to its place and
        /// runs on and off the edge of the picture with it - never pushed along the edge to stay readable. It
        /// fades only when the thing goes out of view or a better name needs the room.
        private func layoutLabels() {
            guard let v = view, let b = board, let anchor, ground != nil else { return }
            v.clipsToBounds = true
            let inView = b.now.filter { !$0.maybe }
            var want: [(key: String, name: String, lat: Double, lon: Double, main: Bool)] = []
            var keys = Set<String>()
            func add(_ s: Sighting, main: Bool) {
                let k = "f\(s.feature)"
                guard !keys.contains(k) else { return }
                keys.insert(k)
                want.append((k, pack.features[s.feature].name, s.plat, s.plon, main))
            }
            if let f = b.focus, b.timeline[f].state == .inView { add(b.timeline[f].look, main: true) }
            // names already up keep their place while their thing is still in view, so the picture does not
            // reshuffle every second; then the best of the rest
            let most = [12, 30, 90][min(2, max(0, labelLevel))]
            for r in inView.prefix(most * 3) where showing.contains("f\(r.sighting.feature)") && want.count < most { add(r.sighting, main: false) }
            for r in inView.prefix(most) { add(r.sighting, main: false) }

            let tint = UIColor(Theme.side(b.side)).resolvedColor(with: UITraitCollection(userInterfaceStyle: .dark))
            var used: [CGRect] = []
            var on = Set<String>()
            for w in want {
                let p = v.projectPoint(Self.place(w.lat, w.lon, pack.groundHeight(w.lat, w.lon), from: anchor))
                guard p.z > 0, p.z < 1 else { continue }                      // behind the camera
                let pt = CGPoint(x: CGFloat(p.x), y: CGFloat(p.y))
                let l = labels[w.key] ?? {
                    let l = UILabel()
                    l.textColor = .white
                    l.alpha = 0
                    l.layer.shadowOpacity = 1
                    l.layer.shadowOffset = .zero
                    v.addSubview(l)
                    labels[w.key] = l
                    return l
                }()
                l.font = .systemFont(ofSize: w.main ? 14 : 11.5, weight: w.main ? .bold : .semibold)
                l.text = (w.main ? "◎ " : "· ") + w.name
                l.layer.shadowColor = (w.main ? tint : UIColor.black).cgColor
                l.layer.shadowRadius = w.main ? 3 : 2
                l.sizeToFit()
                // the mark at the start of the text sits on the place itself, wherever that is
                var frame = l.frame
                frame.origin = CGPoint(x: pt.x - (w.main ? 7 : 4), y: pt.y - frame.height / 2)
                l.frame = frame
                // a name is there for as long as any of it is in the picture: it slides off the edge with its
                // place, as it slid on, instead of going out the moment the place itself crosses the edge
                guard v.bounds.intersects(frame) else { continue }
                if used.contains(where: { $0.insetBy(dx: -4, dy: -2).intersects(frame) }) { continue }
                used.append(frame)
                on.insert(w.key)
            }
            for (k, l) in labels {
                let show = on.contains(k)
                if (l.tag == 1) != show {
                    l.tag = show ? 1 : 0
                    UIView.animate(withDuration: show ? 0.25 : 0.4, delay: 0, options: [.beginFromCurrentState, .allowUserInteraction]) {
                        l.alpha = show ? 1 : 0
                    }
                }
                if !show, !keys.contains(k), l.alpha < 0.02 {
                    l.removeFromSuperview()
                    labels[k] = nil
                }
            }
            showing = on
        }
    }
}
