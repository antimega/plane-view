import PlaneViewCore
import SwiftUI

/// The flight in progress: where the app thinks you are, what is out of your window, what is coming, and a bar
/// along the bottom to look back or ahead.
struct FlightView: View {
    @Bindable var session: FlightSession
    var close: () -> Void

    enum Tab: String, CaseIterable { case window = "Window", map = "Map" }
    static let wideWidth: CGFloat = 700          // from here up, the list and the map sit side by side
    static let listWidth: CGFloat = 410
    @State private var tab: Tab = UserDefaults.standard.string(forKey: "tab") == "map" ? .map : .window
    @State private var detail: DetailItem?
    @State private var zoomed: ZoomedPicture?
    @State private var sheet: SheetKind?

    enum SheetKind: String, Identifiable {
        case takeoff, rehearse, photos, about, routes
        var id: String { rawValue }
    }

    // The board changes every second. Only the small views that show it read it, so that the tab picker, the menu
    // and the sheets are not rebuilt under the finger.
    var body: some View {
        GeometryReader { geo in
            // on an iPad (or anything as wide) the list and the map share the screen; on a phone they are two tabs
            let wide = geo.size.width >= FlightView.wideWidth
            VStack(spacing: 0) {
                header
                BoardTop(session: session, sheet: $sheet)
                if !wide {
                    Picker("View", selection: $tab) {
                        ForEach(Tab.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .padding(.horizontal, 16).padding(.vertical, 8)
                }
                BoardBody(session: session, tab: tab, wide: wide, detail: $detail, zoomed: $zoomed)
                BoardScrub(session: session)
            }
        }
        .background(Theme.paper.ignoresSafeArea())
        .pictureZoom($zoomed)
        .onAppear { session.start() }
        .sheet(item: $detail) { d in
            SightingDetail(session: session, item: d)
                .presentationDetents([.large])
        }
        .sheet(item: $sheet) { k in
            switch k {
            case .takeoff: TakeoffSheet(session: session)
            case .rehearse: RehearseSheet(session: session)
            case .photos: PhotosSheet(session: session)
            case .about: AboutSheet(pack: session.pack)
            case .routes: RoutesSheet(session: session)
            }
        }
        .sheet(item: $shareURL) { item in
            ActivitySheet(url: item.url).presentationDetents([.medium, .large])
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Button(action: close) {
                Image(systemName: "chevron.left").font(.body.weight(.semibold)).frame(width: 36, height: 36)
            }
            .accessibilityLabel("All flights")
            VStack(alignment: .leading, spacing: 0) {
                Text(session.pack.flight.title).font(Theme.display(24)).foregroundStyle(Theme.ink)
                Text(session.pack.flight.subtitle).font(.caption.weight(.medium)).foregroundStyle(Theme.muted)
            }
            Spacer()
            SidePicker(side: $session.side)
            Menu {
                Section {
                    if !session.hasGPS {
                        Button { session.tookOff(at: session.now) } label: { Label("Wheels up now", systemImage: "airplane.departure") }
                        Button { sheet = .takeoff } label: { Label("We took off at…", systemImage: "clock") }
                    }
                    if session.pinned {
                        Button { session.clearPin() } label: { Label("Forget my \"we're here\"", systemImage: "pin.slash") }
                    }
                }
                Section {
                    Toggle(isOn: $session.alertsOn) { Label("Alert before sightings", systemImage: "bell") }
                    Toggle(isOn: $session.liveActivityOn) { Label("Show on Lock Screen", systemImage: "platter.filled.top.iphone") }
                }
                Section {
                    Button { sheet = .photos } label: { Label("Photos from this flight", systemImage: "photo.on.rectangle") }
                    Button { exportTrack() } label: { Label("Export the GPS track", systemImage: "square.and.arrow.up") }
                        .disabled(!session.hasTrack)
                    Button { sheet = .routes } label: { Label("Which route are we on?", systemImage: "point.topleft.down.to.point.bottomright.curvepath") }
                }
                Section {
                    Button { sheet = .rehearse } label: { Label("Rehearse this flight", systemImage: "play.circle") }
                    Button { sheet = .about } label: { Label("About the data", systemImage: "info.circle") }
                    Button(role: .destructive) { session.forget() } label: { Label("Forget this flight's positions", systemImage: "trash") }
                }
            } label: {
                Image(systemName: "ellipsis.circle").font(.title3).frame(width: 36, height: 36)
            }
            .accessibilityLabel("More")
        }
        .tint(Theme.ink)
        .padding(.horizontal, 10).padding(.top, 4)
    }

    @State private var shareURL: ShareItem?

    private func exportTrack() {
        if let u = session.trackLog.gpx(name: session.pack.id) { sheet = nil; shareURL = ShareItem(url: u) }
    }
}

struct ShareItem: Identifiable {
    var url: URL
    var id: String { url.path }
}

/// The system share sheet (AirDrop, Files, Mail) for a file the app has just written.
struct ActivitySheet: UIViewControllerRepresentable {
    var url: URL
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [url], applicationActivities: nil)
    }
    func updateUIViewController(_ vc: UIActivityViewController, context: Context) {}
}

/// The status line and whatever needs saying about the position, kept current.
struct BoardTop: View {
    var session: FlightSession
    @Binding var sheet: FlightView.SheetKind?

    var body: some View {
        if let b = session.board {
            StatusStrip(session: session, board: b)
            banners(b)
        }
    }

    @ViewBuilder
    private func banners(_ b: Board) -> some View {
        let e = b.estimate
        VStack(spacing: 6) {
            if let r = session.rehearsal {
                RehearsalBar(session: session, rehearsal: r)
            }
            if let far = session.model.navigator.strayKm, session.rehearsal == nil, e.source != .gps {
                Banner(icon: "location.slash", tint: Theme.muted,
                       text: "GPS puts this phone \(Int(far.rounded()).formatted()) km from this flight's routes, so it is not being used.")
            }
            if e.source == .schedule, e.phase == .airborne, session.rehearsal == nil {
                Banner(icon: "clock.badge.questionmark", tint: Theme.warn,
                       text: "No GPS yet, so this goes by the timetable, which is often 10-30 min out.",
                       action: ("Wheels up now", { session.tookOff(at: session.now) }), second: ("Took off at…", { sheet = .takeoff }))
            } else if e.source == .schedule, e.phase == .beforeDeparture, session.rehearsal == nil {
                Banner(icon: "airplane.departure", tint: Theme.muted,
                       text: "Before takeoff. With GPS it starts by itself; without, tap when the wheels leave the ground.",
                       action: ("Wheels up now", { session.tookOff(at: session.now) }))
            }
            if e.offRoute, e.phase == .airborne {
                Banner(icon: "arrow.triangle.branch", tint: Theme.warn,
                       text: "\(Int(e.nearestKm.rounded())) km from any route past flights took. What is in view now is worked out from where you are; later sightings assume you rejoin the usual route.")
            }
            if e.outsideReach, e.phase == .airborne {
                Banner(icon: "exclamationmark.triangle", tint: Theme.bad,
                       text: "Outside the area this flight's data covers: the list may be missing things.")
            }
        }
        .padding(.horizontal, 12)
        .padding(.top, 6)
    }
}

struct BoardBody: View {
    var session: FlightSession
    var tab: FlightView.Tab
    var wide = false
    @Binding var detail: DetailItem?
    @Binding var zoomed: ZoomedPicture?

    var body: some View {
        if let b = session.board {
            if wide {
                HStack(spacing: 0) {
                    WindowTab(session: session, board: b, detail: $detail, zoomed: $zoomed)
                        .frame(width: FlightView.listWidth)
                    MapPane(session: session, board: b)
                        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                        .padding(.trailing, 10).padding(.vertical, 6)
                }
                .padding(.top, 4)
            } else {
                switch tab {
                case .window: WindowTab(session: session, board: b, detail: $detail, zoomed: $zoomed)
                case .map: MapPane(session: session, board: b)
                }
            }
        } else {
            Spacer()
            ProgressView("Reading the flight…")
            Spacer()
        }
    }
}

/// The map, or the view out of the window in 3D, with the two buttons that choose between them and how many
/// places are named.
struct MapPane: View {
    var session: FlightSession
    var board: Board
    /// The two faces: the flat map, north up, and the view out of the window in 3D.
    @AppStorage("map3D") private var map3D = false
    /// How many places are named: 0 the best of what is in view, 1 also notable places around, 2 as many as fit.
    @AppStorage("mapLabels") private var labels = 0

    var body: some View {
        Group {
            if map3D { WindowScene(session: session, board: board, labels: labels) } else { FlightMap(session: session, board: board, labels: labels) }
        }
        .overlay(alignment: .topLeading) {
            Text("Imagery: EOxCloudless, Sentinel-2 2024")
                .font(.system(size: 9)).foregroundStyle(.white.opacity(0.85))
                .padding(.horizontal, 6).padding(.vertical, 2)
                .background(.black.opacity(0.35), in: Capsule())
                .padding(6)
                .opacity(map3D ? 0 : 1)
        }
        .overlay(alignment: .topTrailing) {
            HStack(spacing: 8) {
                Menu {
                    Picker("Places named", selection: $labels) {
                        Label("What is in view", systemImage: "tag").tag(0)
                        Label("And notable places around", systemImage: "tag.fill").tag(1)
                        Label("As many as fit", systemImage: "tag.circle.fill").tag(2)
                    }
                } label: {
                    Image(systemName: ["tag", "tag.fill", "tag.circle.fill"][min(2, max(0, labels))])
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 42, height: 42)
                        .background(.black.opacity(0.5), in: Circle())
                }
                .accessibilityLabel("How many places are named")
                Button { map3D.toggle() } label: {
                    Text(map3D ? "Map" : "3D")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundStyle(.white)
                        .frame(minWidth: 42, minHeight: 42)
                        .padding(.horizontal, map3D ? 6 : 0)
                        .background(.black.opacity(0.5), in: Capsule())
                }
                .accessibilityLabel(map3D ? "Flat map, north up" : "The view out of the window in 3D")
            }
            .padding(10)
        }
    }
}

struct BoardScrub: View {
    var session: FlightSession
    var body: some View {
        if let b = session.board { ScrubBar(session: session, board: b) }
    }
}

struct SidePicker: View {
    @Binding var side: Side
    var body: some View {
        HStack(spacing: 0) {
            ForEach(Side.allCases, id: \.self) { s in
                Button {
                    side = s
                } label: {
                    Text(s == .L ? "Left" : "Right")
                        .font(.subheadline.weight(.semibold))
                        .padding(.horizontal, 12).padding(.vertical, 7)
                        .foregroundStyle(side == s ? Color.white : Theme.muted)
                        .background(side == s ? Theme.side(s) : Color.clear, in: Capsule())
                }
                .accessibilityLabel("\(s.word) window")
                .accessibilityAddTraits(side == s ? .isSelected : [])
            }
        }
        .padding(2)
        .background(Theme.chip, in: Capsule())
    }
}

struct Banner: View {
    var icon: String
    var tint: Color
    var text: String
    var action: (String, () -> Void)? = nil
    var second: (String, () -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: icon).foregroundStyle(tint).font(.subheadline.weight(.semibold))
                Text(text).font(.footnote).foregroundStyle(Theme.ink).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            if action != nil || second != nil {
                HStack(spacing: 8) {
                    if let a = action {
                        Button(a.0, action: a.1).buttonStyle(.borderedProminent).tint(Theme.track)
                    }
                    if let s = second {
                        Button(s.0, action: s.1).buttonStyle(.bordered).tint(Theme.ink)
                    }
                }
                .controlSize(.small)
                .padding(.leading, 26)
            }
        }
        .padding(10)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(tint.opacity(0.5), lineWidth: 1))
    }
}

/// Where the position comes from and how good it is - the thing to glance at after coming back from Camera.
struct StatusStrip: View {
    var session: FlightSession
    var board: Board

    var body: some View {
        let e = board.estimate
        HStack(spacing: 8) {
            HStack(spacing: 5) {
                Circle().fill(dot(e)).frame(width: 8, height: 8)
                Text(source(e)).font(.footnote.weight(.semibold)).foregroundStyle(Theme.ink)
            }
            .padding(.horizontal, 9).padding(.vertical, 5)
            .background(Theme.chip, in: Capsule())
            Text(facts(e)).font(.footnote).foregroundStyle(Theme.muted).lineLimit(1).minimumScaleFactor(0.8)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 14).padding(.top, 6)
        .accessibilityElement(children: .combine)
    }

    private func dot(_ e: Estimate) -> Color {
        switch e.source {
        case .gps: return Theme.ok
        case .deadReckoning: return (e.fixAge ?? 0) < 600 ? Theme.warn : Theme.bad
        case .pinned: return Theme.water
        case .schedule: return Theme.muted
        }
    }

    private func source(_ e: Estimate) -> String {
        let pm = "±\(max(1, Int((e.sigma / 60).rounded()))) min"
        switch e.source {
        case .gps: return "GPS"
        case .deadReckoning:
            let age = e.fixAge ?? 0
            return e.phase == .airborne ? "GPS \(Fmt.span(age)) old · est. \(pm)" : "GPS \(Fmt.span(age)) old"
        case .pinned: return "Set by you · \(pm)"
        case .schedule: return e.phase == .beforeDeparture ? "Before takeoff" : "Timetable · \(pm)"
        }
    }

    private func facts(_ e: Estimate) -> String {
        switch e.phase {
        case .beforeDeparture:
            let t = session.pack.takeoff.timeIntervalSince(session.now)
            return t > 0 ? "planned takeoff \(Fmt.until(t))" : "planned takeoff was \(Fmt.ago(-t))"
        case .landed:
            return "landed at \(session.pack.flight.dest.code)"
        case .airborne:
            let left = (board.duration - e.state.t) / max(e.pace, 0.4)
            var s = "lands \(Fmt.until(left)) · \(Fmt.feet(e.state.altFt))"
            if abs(e.pace - 1) > 0.06, e.source == .gps || e.source == .deadReckoning {
                s += e.pace < 1 ? " · slower than usual" : " · faster than usual"
            }
            return s
        }
    }
}

struct RehearsalBar: View {
    var session: FlightSession
    var rehearsal: Rehearsal

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "play.circle.fill").foregroundStyle(Theme.track)
            Text("Rehearsal · \(rehearsal.path.id)").font(.footnote.weight(.semibold)).foregroundStyle(Theme.ink)
            Spacer(minLength: 0)
            ForEach([1.0, 10, 60], id: \.self) { r in
                Button("\(Int(r))×") { session.setRehearsal(rate: r) }
                    .font(.footnote.weight(.semibold))
                    .padding(.horizontal, 7).padding(.vertical, 4)
                    .background(rehearsal.rate == r ? Theme.track : Theme.chip, in: Capsule())
                    .foregroundStyle(rehearsal.rate == r ? .white : Theme.ink)
            }
            Button { session.setRehearsal(gpsOff: !rehearsal.gpsOff) } label: {
                Image(systemName: rehearsal.gpsOff ? "location.slash.fill" : "location.fill")
                    .foregroundStyle(rehearsal.gpsOff ? Theme.bad : Theme.ok)
                    .frame(width: 30, height: 26)
            }
            .accessibilityLabel(rehearsal.gpsOff ? "Rehearsal GPS is off" : "Rehearsal GPS is on")
            Button("End") { session.endRehearsal() }.font(.footnote.weight(.semibold)).foregroundStyle(Theme.bad)
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(Theme.track.opacity(0.5), lineWidth: 1))
    }
}
