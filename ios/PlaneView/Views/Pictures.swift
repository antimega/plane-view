import PlaneViewCore
import SwiftUI
import UIKit

/// Where to look, at a glance: the aircraft from above, nose up, with a mark on your side. The angle round the
/// circle is the clock direction; the distance from the middle is how far down - at the rim it is on the horizon,
/// near the aircraft it is almost underneath.
struct DirectionGlyph: View {
    var side: Side
    var fa: Double
    var dep: Double
    var size: CGFloat = 44

    var body: some View {
        Canvas { ctx, sz in
            let c = CGPoint(x: sz.width / 2, y: sz.height / 2)
            let R = min(sz.width, sz.height) / 2 - 1.5
            ctx.stroke(Path(ellipseIn: CGRect(x: c.x - R, y: c.y - R, width: 2 * R, height: 2 * R)), with: .color(Theme.line), lineWidth: 1.2)
            // the half of the sky your window faces
            var half = Path()
            half.move(to: c)
            half.addArc(center: c, radius: R, startAngle: .degrees(-90), endAngle: .degrees(90), clockwise: side == .L)
            half.closeSubpath()
            ctx.fill(half, with: .color(Theme.side(side).opacity(0.10)))
            // the aircraft
            var plane = Path()
            plane.move(to: CGPoint(x: c.x, y: c.y - R * 0.36))
            plane.addLine(to: CGPoint(x: c.x, y: c.y + R * 0.32))
            plane.move(to: CGPoint(x: c.x - R * 0.30, y: c.y + R * 0.02))
            plane.addLine(to: CGPoint(x: c.x + R * 0.30, y: c.y + R * 0.02))
            plane.move(to: CGPoint(x: c.x - R * 0.13, y: c.y + R * 0.30))
            plane.addLine(to: CGPoint(x: c.x + R * 0.13, y: c.y + R * 0.30))
            ctx.stroke(plane, with: .color(Theme.muted), style: StrokeStyle(lineWidth: 1.6, lineCap: .round))
            // the mark
            let rel = (90 - fa) * (side == .R ? 1 : -1)             // degrees clockwise from the nose
            let a = (rel - 90) * .pi / 180
            let r = R * CGFloat(min(1, max(0.42, 1 - dep / 75)))
            let p = CGPoint(x: c.x + r * CGFloat(cos(a)), y: c.y + r * CGFloat(sin(a)))
            var ray = Path()
            ray.move(to: c)
            ray.addLine(to: p)
            ctx.stroke(ray, with: .color(Theme.side(side).opacity(0.55)), lineWidth: 1.2)
            ctx.fill(Path(ellipseIn: CGRect(x: p.x - 4.2, y: p.y - 4.2, width: 8.4, height: 8.4)), with: .color(Theme.side(side)))
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

/// One of the pack's pictures, by its path inside the pack.
private func packImage(_ pack: Pack, _ file: String?) -> UIImage? {
    guard let file else { return nil }
    return PictureCache.image(pack.file(file))
}

/// The pack's pictures, decoded once and kept. The screen redraws every second, and a sighting's picture should be
/// there the moment its card grows - so pictures are held ready to draw, and the ones about to be wanted are
/// decoded ahead of time off the main thread.
enum PictureCache {
    private static let cache: NSCache<NSString, UIImage> = {
        let c = NSCache<NSString, UIImage>()
        c.totalCostLimit = 96 << 20        // decoded bytes: half a megabyte a picture, two in a high-detail pack
        return c
    }()
    private static let queue = DispatchQueue(label: "planeview.pictures", qos: .userInitiated)

    static func image(_ url: URL) -> UIImage? {
        let key = url.path as NSString
        if let hit = cache.object(forKey: key) { return hit }
        guard let raw = UIImage(contentsOfFile: url.path) else { return nil }
        let ready = raw.preparingForDisplay() ?? raw       // decode now, not at the first draw
        cache.setObject(ready, forKey: key, cost: Int(ready.size.width * ready.scale * ready.size.height * ready.scale) * 4)
        return ready
    }

    /// Decode these in the background so they are ready when asked for.
    static func warm(_ urls: [URL]) {
        let need = urls.filter { cache.object(forKey: $0.path as NSString) == nil }
        guard !need.isEmpty else { return }
        queue.async { for u in need { _ = image(u) } }
    }
}

/// The view from the window towards a sighting - the forecast's rendered picture, or the app's own - with coast,
/// lake shores, rivers and borders drawn on it where the forecast traced them, and the sighting ringed and named.
struct WindowPicture: View {
    var art: WindowArt?
    var side: Side
    var name: String
    var labels = true

    /// The labels to letter: a neighbour whose name would run across the sighting's own mark or name is left out
    /// (its dot stays), so the thing you are looking for is never written over.
    private var clearLabels: [ArtLabel] {
        let all = art?.labels ?? []
        guard let m = all.first(where: { $0.main }) else { return all }
        return all.filter { l in
            if l.main { return true }
            // a name is lettered centred under its dot, about 1.5% of the width a letter
            let x = min(80, max(20, l.x)), half = Double(l.name.count) * 1.5 + 6
            return !(abs(x - min(80, max(20, m.x))) < half && l.y > m.y - 9 && l.y < m.y + 7)
        }
    }

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width, h = geo.size.height
            let scale = min(1.25, max(1, w / 180))            // lettering grows a little with a bigger picture
            ZStack(alignment: .topLeading) {
                if let img = art?.image {
                    Image(uiImage: img).resizable().frame(width: w, height: h)
                } else {
                    Rectangle().fill(Theme.chip)
                    ProgressView().position(x: w / 2, y: h / 2)
                }
                Canvas { ctx, sz in
                    guard let v = art else { return }
                    let sets: [([[[Double]]], Color, CGFloat, [CGFloat])] = [
                        (v.coast, .white, 1.0, []), (v.lakes, Color(red: 0.84, green: 0.93, blue: 1), 0.9, []),
                        (v.rivers, Color(red: 0.66, green: 0.83, blue: 0.96), 0.8, []),
                        (v.borders, Color(red: 1, green: 0.88, blue: 0.54), 0.9, [3, 2]),
                    ]
                    for (lines, color, width, dash) in sets {
                        for ln in lines where ln.count > 1 {
                            var p = Path()
                            for (i, pt) in ln.enumerated() where pt.count >= 2 {
                                let q = CGPoint(x: pt[0] / 100 * sz.width, y: pt[1] / 100 * sz.height)
                                if i == 0 { p.move(to: q) } else { p.addLine(to: q) }
                            }
                            ctx.stroke(p, with: .color(color.opacity(0.9)), style: StrokeStyle(lineWidth: width, dash: dash))
                        }
                    }
                    for l in v.labels {
                        let q = CGPoint(x: l.x / 100 * sz.width, y: l.y / 100 * sz.height)
                        if l.main {
                            // the sighting itself: a ring in your side's colour inside a white one
                            ctx.stroke(Path(ellipseIn: CGRect(x: q.x - 6, y: q.y - 6, width: 12, height: 12)), with: .color(.white), lineWidth: 2.4)
                            ctx.stroke(Path(ellipseIn: CGRect(x: q.x - 6, y: q.y - 6, width: 12, height: 12)), with: .color(Theme.side(side)), lineWidth: 1.2)
                        } else {
                            ctx.fill(Path(ellipseIn: CGRect(x: q.x - 1.6, y: q.y - 1.6, width: 3.2, height: 3.2)), with: .color(.white))
                        }
                    }
                }
                if labels, art?.image != nil {
                    // names sit under their marks as plain lettering with a dark edge: no box, which would hide
                    // the very ground it names
                    ForEach(Array(clearLabels.enumerated()), id: \.offset) { _, l in
                        Text(l.name)
                            .font(.system(size: (l.main ? 12.5 : 10) * scale, weight: l.main ? .bold : .semibold))
                            .lineLimit(1)
                            .foregroundStyle(.white)
                            .shadow(color: .black.opacity(0.95), radius: 1.2)
                            .shadow(color: .black.opacity(0.6), radius: 2.5)
                            .frame(maxWidth: w * 0.86)
                            .fixedSize()
                            .position(x: min(0.8, max(0.2, l.x / 100)) * w, y: min(0.93, l.y / 100 + (l.main ? 0.05 : 0.04)) * h)
                    }
                    Text(side == .L ? "NOSE ›" : "‹ NOSE")
                        .font(.system(size: 9, weight: .semibold)).tracking(0.5)
                        .foregroundStyle(.white).shadow(color: .black, radius: 1.5)
                        .position(x: side == .L ? w * 0.72 : w * 0.28, y: h * 0.075)
                }
            }
            .clipShape(RoundedRectangle(cornerSize: CGSize(width: w * 0.30, height: h * 0.24), style: .continuous))
            .overlay(RoundedRectangle(cornerSize: CGSize(width: w * 0.30, height: h * 0.24), style: .continuous).stroke(Theme.line, lineWidth: 1))
        }
        .aspectRatio(4 / 5, contentMode: .fit)
        .accessibilityLabel("View from the window towards \(name)")
    }
}

/// The place from straight above, turned the way you will be looking at it: you are below the bottom edge.
struct AerialPicture: View {
    var art: MapArt?
    var name: String
    var compact = false           // small, on the main card: no names but the place's own ring

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            ZStack {
                if let img = art?.image {
                    Image(uiImage: img).resizable().frame(width: w, height: w)
                } else {
                    Rectangle().fill(Theme.chip)
                    ProgressView()
                }
                Canvas { ctx, sz in
                    guard let a = art, a.image != nil else { return }
                    for ln in a.route where ln.count > 1 {
                        var p = Path()
                        for (i, pt) in ln.enumerated() where pt.count >= 2 {
                            let q = CGPoint(x: pt[0] / 100 * sz.width, y: pt[1] / 100 * sz.height)
                            if i == 0 { p.move(to: q) } else { p.addLine(to: q) }
                        }
                        ctx.stroke(p, with: .color(Color(red: 1, green: 0.35, blue: 0.68)), style: StrokeStyle(lineWidth: compact ? 1.4 : 1.8, dash: [5, 4]))
                    }
                    let k: CGFloat = compact ? 0.75 : 1
                    let c = CGPoint(x: sz.width / 2, y: sz.height / 2)
                    ctx.stroke(Path(ellipseIn: CGRect(x: c.x - 6 * k, y: c.y - 6 * k, width: 12 * k, height: 12 * k)), with: .color(.white), lineWidth: 1.5)
                    // you, at the bottom edge, looking up the picture
                    var eye = Path()
                    let e = CGPoint(x: sz.width / 2, y: sz.height * 0.93)
                    eye.move(to: CGPoint(x: e.x, y: e.y - 9 * k)); eye.addLine(to: CGPoint(x: e.x + 6 * k, y: e.y + 4 * k))
                    eye.addLine(to: CGPoint(x: e.x, y: e.y + 1 * k)); eye.addLine(to: CGPoint(x: e.x - 6 * k, y: e.y + 4 * k)); eye.closeSubpath()
                    ctx.fill(eye, with: .color(.white))
                    ctx.stroke(eye, with: .color(.black.opacity(0.7)), lineWidth: 0.6)
                    // north
                    let n = CGPoint(x: sz.width * (compact ? 0.84 : 0.86), y: sz.height * (compact ? 0.17 : 0.15))
                    ctx.fill(Path(ellipseIn: CGRect(x: n.x - 11 * k, y: n.y - 11 * k, width: 22 * k, height: 22 * k)), with: .color(.black.opacity(0.4)))
                    ctx.draw(Text("N").font(.system(size: 10 * k, weight: .bold)).foregroundStyle(.white), at: n)
                    var arrow = Path()
                    arrow.move(to: CGPoint(x: 0, y: -19 * k)); arrow.addLine(to: CGPoint(x: 3.4 * k, y: -10 * k))
                    arrow.addLine(to: CGPoint(x: 0, y: -12 * k)); arrow.addLine(to: CGPoint(x: -3.4 * k, y: -10 * k)); arrow.closeSubpath()
                    let tr = CGAffineTransform(translationX: n.x, y: n.y).rotated(by: -a.up * .pi / 180)
                    ctx.fill(arrow.applying(tr), with: .color(.white))
                }
                if !compact, art?.image != nil {
                    ForEach(Array((art?.labels ?? []).enumerated()), id: \.offset) { _, l in
                        Text(l.name)
                            .font(.system(size: l.main ? 11.5 : 10, weight: .semibold))
                            .foregroundStyle(.white).shadow(color: .black, radius: 1.5).shadow(color: .black.opacity(0.8), radius: 0.5)
                            .lineLimit(1).fixedSize()
                            .position(x: min(0.78, max(0.22, l.x / 100)) * w, y: min(0.88, l.y / 100 + (l.main ? 0.07 : 0.04)) * w)
                    }
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: compact ? 8 : 10, style: .continuous))
        }
        .aspectRatio(1, contentMode: .fit)
        .accessibilityLabel("\(name) from above, turned to the way you look")
    }
}

/// One of a sighting's two pictures, opened to the full width of the screen until dismissed.
struct ZoomedPicture: Identifiable {
    enum Kind {
        case window(WindowArt?)
        case map(MapArt?)
    }
    var kind: Kind
    var side: Side
    var name: String
    var id: String {
        switch kind { case .window: return "w" + name; case .map: return "m" + name }
    }
}

extension View {
    /// Shows `picture` over this view, as wide as the screen, until it is tapped (or the close button is).
    func pictureZoom(_ picture: Binding<ZoomedPicture?>) -> some View {
        overlay {
            if let z = picture.wrappedValue {
                ZStack {
                    Color.black.opacity(0.96).ignoresSafeArea()
                    VStack(spacing: 10) {
                        Group {
                            switch z.kind {
                            case .window(let art): WindowPicture(art: art, side: z.side, name: z.name)
                            case .map(let art): AerialPicture(art: art, name: z.name)
                            }
                        }
                        .padding(.horizontal, 6)
                        .frame(maxWidth: 900)
                        Text(caption(z))
                            .font(.footnote).foregroundStyle(.white.opacity(0.8)).multilineTextAlignment(.center)
                            .padding(.horizontal, 20)
                    }
                }
                .overlay(alignment: .topTrailing) {
                    Image(systemName: "xmark")
                        .font(.body.weight(.semibold)).foregroundStyle(.white)
                        .frame(width: 40, height: 40)
                        .background(.white.opacity(0.18), in: Circle())
                        .padding(14)
                }
                .contentShape(Rectangle())
                .onTapGesture { withAnimation(.easeOut(duration: 0.18)) { picture.wrappedValue = nil } }
                .transition(.opacity)
                .accessibilityAddTraits(.isButton)
                .accessibilityLabel("\(z.name), enlarged. Tap to close.")
            }
        }
    }

    private func caption(_ z: ZoomedPicture) -> String {
        switch z.kind {
        case .window(let a):
            return "\(z.name) from the \(z.side.word) window" + (a?.drawnHere == true ? " · drawn on the phone" : "")
        case .map(let a):
            let km = (a?.km ?? 0) > 0 ? " · \(Int(a!.km)) km across" : ""
            return "\(z.name) from above, turned the way you face it\(km)" + (a?.drawnHere == true ? " · drawn on the phone" : "")
        }
    }
}
