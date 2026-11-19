import Photos
import PlaneViewCore
import SwiftUI
import UIKit

/// One sighting in full: the window view, the place from above, where and when to look, and what else is there.
struct SightingDetail: View {
    var session: FlightSession
    var item: DetailItem
    @State private var zoomed: ZoomedPicture?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let pack = session.pack
        let s = item.sighting
        let f = pack.features[s.feature]
        let w = whereWords(side: s.side, fa: s.fa, dep: s.dep)
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(f.name).font(Theme.display(34)).foregroundStyle(Theme.ink)
                        if let l = f.local, l != f.name { Text(l).font(.subheadline).foregroundStyle(Theme.muted) }
                        if let what = f.what { Text(what).font(.body).foregroundStyle(Theme.muted) }
                    }
                    let _ = session.art.version
                    let best = item.best ?? s
                    let windowArt = session.windowArt(best, plan: item.plan)
                    let mapArt = session.mapArt(best, plan: item.plan, also: item.also)
                    HStack(alignment: .top, spacing: 10) {
                        Button { withAnimation(.easeOut(duration: 0.18)) { zoomed = ZoomedPicture(kind: .window(windowArt), side: s.side, name: f.name) } } label: {
                            WindowPicture(art: windowArt, side: s.side, name: f.name)
                        }
                        .buttonStyle(.plain)
                        VStack(alignment: .leading, spacing: 4) {
                            Button { withAnimation(.easeOut(duration: 0.18)) { zoomed = ZoomedPicture(kind: .map(mapArt), side: s.side, name: f.name) } } label: {
                                AerialPicture(art: mapArt, name: f.name)
                            }
                            .buttonStyle(.plain)
                            Text("from above, as you face it" + ((mapArt?.km ?? 0) > 0 ? " · \(Int(mapArt!.km)) km across" : ""))
                                .font(.caption2).foregroundStyle(Theme.muted)
                        }
                    }
                    Text(pictureNote(windowArt, mapArt))
                        .font(.caption2).foregroundStyle(Theme.muted)
                    HStack(spacing: 12) {
                        DirectionGlyph(side: s.side, fa: s.fa, dep: s.dep, size: 64)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(s.side == .L ? "Left" : "Right") window, \(w.clock)").font(.title3.weight(.semibold)).foregroundStyle(Theme.side(s.side))
                            Text(w.words).font(.body).foregroundStyle(Theme.ink)
                            Text("\(Fmt.km(s.dKm)) away · \(Int(s.dep.rounded()))° below the horizontal").font(.footnote).foregroundStyle(Theme.muted)
                        }
                    }
                    facts(f, s)
                    if !item.also.isEmpty {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Also there").font(Theme.display(19)).foregroundStyle(Theme.ink)
                            ForEach(item.also) { a in
                                let af = pack.features[a.feature]
                                let aw = whereWords(side: a.side, fa: a.fa, dep: a.dep)
                                HStack {
                                    Text(af.name).font(.subheadline.weight(.medium)).foregroundStyle(Theme.ink)
                                    Spacer()
                                    Text("\(aw.clock) · \(Fmt.km(a.dKm))").font(.caption).foregroundStyle(Theme.muted)
                                }
                            }
                        }
                    }
                    Button {
                        session.toggleMark(f.id)
                    } label: {
                        let on = session.marked.contains(f.id)
                        Label(on ? "Seen it" : "Mark as seen", systemImage: on ? "checkmark.circle.fill" : "circle")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered).tint(session.marked.contains(f.id) ? Theme.ok : Theme.ink).controlSize(.large)
                }
                .padding(18)
            }
            .background(Theme.paper)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .navigationBarTitleDisplayMode(.inline)
        }
        .pictureZoom($zoomed)
    }

    /// Where the two pictures came from, so they are not taken for more than they are.
    private func pictureNote(_ w: WindowArt?, _ m: MapArt?) -> String {
        let alt = item.plan?.alt_ft.map { ", at \(Fmt.feet($0))" } ?? ""
        let mine = "drawn on the phone from the pack's satellite map and terrain, for where the aircraft is taken to be at the best moment"
        switch (w?.drawnHere ?? true, m?.drawnHere ?? true) {
        case (false, false):
            return "The pictures are from the forecast's likely route\(alt); today's view may be from a little further along or to one side."
        case (true, true):
            return "Both pictures are \(mine). They are coarser than the forecast's own."
        case (false, true):
            return "The window view is from the forecast's likely route\(alt). The map is \(mine)."
        case (true, false):
            return "The map is from the forecast. The window view is \(mine)."
        }
    }

    @ViewBuilder private func facts(_ f: Feature, _ s: Sighting) -> some View {
        let b = session.board
        let pace = max(b?.estimate.pace ?? 1, 0.4)
        let T = b?.viewT ?? 0
        VStack(alignment: .leading, spacing: 5) {
            if let r = item.remaining {
                fact("clock", r < 45 ? "Going out of view" : "In view for another \(Fmt.span(r))")
            } else if s.t1 > s.t0 {
                let start = (s.t0 - T) / pace, end = (s.t1 - T) / pace
                if end < 0 {
                    fact("clock", "Went out of view \(Fmt.ago(-end))")
                } else if start > 0 {
                    fact("clock", "Comes into view \(Fmt.until(start)), for about \(Fmt.span(end - start)); best \(Fmt.until((s.tBest - T) / pace))")
                } else {
                    fact("clock", "In view for another \(Fmt.span(end))")
                }
            }
            fact("eye", "View \(s.confidence): \(Int((s.chance * 100).rounded()))% on a clear day from this path" + (s.light == .night ? " (night: lights)" : ""))
            if let p = f.prior(s.side) {
                fact("chart.bar", "Seen from this side on \(Int((p * 100).rounded()))% of past flights")
            } else {
                fact("chart.bar", "Not in view from this side on any past flight: you are off their paths")
            }
            if f.pop > 0 { fact("person.2", "Population \(Int(f.pop).formatted())") }
            if f.elev > 0, f.kind == "peak" { fact("mountain.2", "\(Int(f.elev).formatted()) m high") }
        }
    }

    private func fact(_ icon: String, _ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: icon).font(.footnote).foregroundStyle(Theme.muted).frame(width: 20)
            Text(text).font(.footnote).foregroundStyle(Theme.ink)
        }
    }
}

/// "We took off at…" for when there was no GPS at the time.
struct TakeoffSheet: View {
    var session: FlightSession
    @State private var when = Date()
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                DatePicker("Wheels left the ground", selection: $when, in: ...session.now, displayedComponents: [.hourAndMinute])
                Text("The flight is then followed by the clock from that moment. A GPS fix, whenever one comes, takes over.")
                    .font(.footnote).foregroundStyle(Theme.muted)
            }
            .navigationTitle("Takeoff time")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) { Button("Set") { session.tookOff(at: when); dismiss() } }
            }
            .onAppear { when = session.now }
        }
        .presentationDetents([.medium])
    }
}

/// Fly one of the pack's tracks as if it were today, to look through the flight or to try the app out.
struct RehearseSheet: View {
    var session: FlightSession
    @State private var pathID = "V1"
    @State private var rate = 30.0
    @State private var fromMin = 0.0
    @State private var shift = 0.0
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let pack = session.pack
        let choices = pack.paths.filter { $0.kind != "history" } + pack.paths.filter { $0.kind == "history" }.sorted { $0.weight < $1.weight }.prefix(4)
        NavigationStack {
            Form {
                Section("Track to fly") {
                    Picker("Track", selection: $pathID) {
                        ForEach(Array(choices), id: \.id) { p in
                            Text(label(p)).tag(p.id)
                        }
                    }
                    .pickerStyle(.inline).labelsHidden()
                }
                Section("How") {
                    Picker("Speed", selection: $rate) {
                        Text("Real time").tag(1.0); Text("10×").tag(10.0); Text("30×").tag(30.0); Text("60×").tag(60.0)
                    }
                    Stepper("Start \(Int(fromMin)) min after takeoff", value: $fromMin, in: 0...max(0, pack.flight.duration_min - 5), step: 5)
                    Stepper(shift == 0 ? "On the track" : "\(Int(shift)) km to its right in the cruise", value: $shift, in: 0...120, step: 20)
                }
                Section {
                    Text("The rehearsal has its own positions and leaves this flight's real ones alone. While it runs, the location button in its bar switches its GPS off and on, to see how the app carries on without.")
                        .font(.footnote).foregroundStyle(Theme.muted)
                }
            }
            .navigationTitle("Rehearse")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Start") {
                        session.startRehearsal(pathID: pathID, rate: rate, fromT: fromMin * 60, shiftKm: shift)
                        dismiss()
                    }
                }
            }
            .onAppear { pathID = pack.mainPath.id }
        }
    }

    private func label(_ p: FlightPath) -> String {
        switch p.kind {
        case "variant": return "\(p.id) · \(Int((p.weight * 100).rounded()))% of past flights" + (p.label.map { " · \($0)" } ?? "")
        default: return "Rare past flight · \(p.label ?? p.id)"
        }
    }
}

/// How belief in each likely route stands, given the GPS so far.
struct RoutesSheet: View {
    var session: FlightSession
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let nav = session.model.navigator
        let e = session.board?.estimate
        NavigationStack {
            List {
                Section {
                    ForEach(nav.routeShares(), id: \.id) { r in
                        HStack {
                            VStack(alignment: .leading) {
                                Text(r.id).font(.body.weight(.semibold))
                                if let l = r.label { Text(l).font(.caption).foregroundStyle(Theme.muted) }
                            }
                            Spacer()
                            Text("\(Int((r.share * 100).rounded()))%").font(.body.monospacedDigit())
                        }
                    }
                } header: {
                    Text(nav.lastFix == nil ? "Likely routes (from past flights)" : "Likely routes, given where you have been")
                }
                if let e, nav.lastFix != nil {
                    Section("Now") {
                        Text("Following \(e.pathID): \(abs(e.crossKm) < 1 ? "on it" : "\(Int(abs(e.crossKm).rounded())) km to its \(e.crossKm > 0 ? "right" : "left")").")
                        Text("Nearest of all \(session.pack.manifest.counts?["legs"] ?? 0) past flights: \(Int(e.nearestKm.rounded())) km away.")
                        Text(String(format: "Covering the plan at %.0f%% of the usual speed.", e.pace * 100))
                    }
                    .font(.subheadline)
                }
                Section {
                    Text("The pack knows the ground for \(Int(session.pack.manifest.corridor.reach_km)) km beyond every past flight, so a routing nobody flew before still gets a list worked out from where you actually are.")
                        .font(.footnote).foregroundStyle(Theme.muted)
                }
            }
            .navigationTitle("Routes")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .presentationDetents([.medium, .large])
    }
}

/// Photos taken during the flight with the phone's own camera, each with where it was taken and what was in view.
struct PhotosSheet: View {
    var session: FlightSession
    @State private var photos: [MatchedPhoto] = []
    @State private var state = "Looking…"
    @State private var allowed = false
    @State private var wrote: Int?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Group {
                if photos.isEmpty {
                    VStack(spacing: 10) {
                        Image(systemName: "photo.on.rectangle.angled").font(.largeTitle).foregroundStyle(Theme.muted)
                        Text(state).font(.subheadline).foregroundStyle(Theme.muted).multilineTextAlignment(.center)
                    }
                    .padding(30)
                } else {
                    List {
                        Section {
                            ForEach(photos) { p in PhotoRow(photo: p, tz: session.pack.flight.orig.tz) }
                        } header: {
                            Text("\(photos.count) photo\(photos.count == 1 ? "" : "s") during the flight, \(session.side.word) window")
                        }
                        Section {
                            let missing = photos.filter { !$0.hasLocation && $0.exact }.count
                            if missing > 0 {
                                Button(missing == 1 ? "Add the position to 1 photo that has none" : "Add the position to \(missing) photos that have none") {
                                    Task {
                                        wrote = await PhotoMatcher.addLocations(photos)
                                        photos = PhotoMatcher.match(session: session)
                                    }
                                }
                            }
                            if let w = wrote { Text("Position written to \(w) photo\(w == 1 ? "" : "s").").font(.footnote).foregroundStyle(Theme.muted) }
                            if let url = PhotoMatcher.export(photos, flight: session.pack.id) {
                                ShareLink(item: url) { Label("Export this list", systemImage: "square.and.arrow.up") }
                            }
                        } footer: {
                            Text("Positions come from this app's GPS track at the moment each photo was taken; where there was no fix within three minutes they are carried along the route and marked estimated.")
                        }
                    }
                }
            }
            .navigationTitle("Photos")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .task {
                if session.trackLog.rows.isEmpty {
                    state = "No GPS track for this flight yet. Photos are placed against the track the app logs while it is running, including while you are in Camera."
                    return
                }
                allowed = await PhotoMatcher.authorize()
                guard allowed else {
                    state = "Plane View needs access to your photos to read when each was taken. Allow it in Settings."
                    return
                }
                photos = PhotoMatcher.match(session: session)
                if photos.isEmpty { state = "No photos were taken while the GPS track was being logged." }
            }
        }
    }
}

struct PhotoRow: View {
    var photo: MatchedPhoto
    var tz: String?
    @State private var image: UIImage?

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Group {
                if let image { Image(uiImage: image).resizable().scaledToFill() } else { Rectangle().fill(Theme.chip) }
            }
            .frame(width: 72, height: 72).clipShape(RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 3) {
                Text(photo.names.isEmpty ? "Nothing named in view" : photo.names.joined(separator: ", "))
                    .font(.subheadline.weight(.medium)).foregroundStyle(Theme.ink).lineLimit(3)
                Text("\(Fmt.clock(photo.taken, tz: nil)) · \(String(format: "%.3f, %.3f", photo.state.lat, photo.state.lon)) · \(Fmt.feet(photo.state.altFt))")
                    .font(.caption).foregroundStyle(Theme.muted)
                if !photo.exact { Text("position estimated (no GPS then)").font(.caption).foregroundStyle(Theme.warn) }
            }
        }
        .task { image = await PhotoMatcher.thumbnail(photo.asset, side: 160) }
    }
}

struct AboutSheet: View {
    var pack: Pack
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let m = pack.manifest
        NavigationStack {
            List {
                Section("This flight's pack") {
                    row("Made", m.generated ?? "")
                    row("Detail", m.detail == "high" ? "High: sharper map, pictures at twice the size, fine terrain" : "Standard")
                    row("Places and features", "\(m.counts?["features"] ?? pack.features.count)")
                    row("Past flights", "\(m.counts?["legs"] ?? 0)")
                    row("Covers", "\(Int(m.corridor.reach_km)) km beyond every past flight")
                    if let t = m.map?.tiles { row("Map tiles", "\(t), zoom \(m.map?.min_z ?? 0)-\(m.map?.max_z ?? 0)") }
                    if let d = m.dem { row("Terrain tiles", "\(d.tiles.count)") }
                    if let s = m.flight.takeoff_source { row("Takeoff time", s) }
                }
                Section("Sources") {
                    Text("Past routes: adsb.lol archives (ODbL).")
                    Text("Places: GeoNames (CC BY 4.0), Natural Earth (public domain), Wikidata (CC0), OpenStreetMap (ODbL).")
                    Text("Satellite imagery: \(m.map?.attribution ?? "EOxCloudless https://cloudless.eox.at by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2024)"), CC BY-NC-SA 4.0.")
                    Text("Terrain: Copernicus DEM GLO-90.")
                }
                .font(.footnote)
                Section {
                    Text("Everything stays on this phone. The app uses no network: positions, the track and the photo list go nowhere unless you export them.")
                        .font(.footnote)
                }
            }
            .navigationTitle("About the data")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
    }

    private func row(_ a: String, _ b: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(a).foregroundStyle(Theme.muted)
            Spacer()
            Text(b).multilineTextAlignment(.trailing)
        }
        .font(.subheadline)
    }
}
