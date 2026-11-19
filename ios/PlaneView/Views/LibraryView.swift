import PlaneViewCore
import SwiftUI

/// The flights on this phone, and the way in for new ones.
struct LibraryView: View {
    var library: PackLibrary
    var app: AppModel
    @State private var picking = false
    @State private var failed: String?

    var body: some View {
        if let s = app.open {
            FlightView(session: s) { app.close() }
            .id(s.pack.id)
        } else {
            NavigationStack {
                Group {
                    if library.packs.isEmpty {
                        empty
                    } else {
                        List {
                            ForEach(library.packs) { p in
                                Button { openPack(p) } label: { row(p) }
                                    .listRowBackground(Theme.card)
                            }
                            .onDelete { idx in idx.map { library.packs[$0] }.forEach(library.delete) }
                        }
                        .scrollContentBackground(.hidden)
                    }
                }
                .background(Theme.paper)
                .navigationTitle("Plane View")
                .toolbar {
                    ToolbarItem(placement: .primaryAction) {
                        Button { picking = true } label: { Label("Add a flight", systemImage: "plus") }
                    }
                }
                .overlay(alignment: .bottom) {
                    if library.importing || app.opening != nil {
                        Label(app.opening.map { "Opening \($0)…" } ?? "Unpacking the flight…", systemImage: "shippingbox")
                            .font(.subheadline.weight(.medium))
                            .padding(.horizontal, 14).padding(.vertical, 10)
                            .background(.thinMaterial, in: Capsule())
                            .padding(.bottom, 24)
                    }
                }
                .fileImporter(isPresented: $picking, allowedContentTypes: [.flightPack, .data], allowsMultipleSelection: true) { result in
                    guard case .success(let urls) = result else { return }
                    Task {
                        for u in urls { await library.importPack(from: u) }
                        failed = library.lastError
                    }
                }
                .alert("That file could not be added", isPresented: Binding(get: { failed != nil }, set: { if !$0 { failed = nil } })) {
                    Button("OK", role: .cancel) {}
                } message: {
                    Text(failed ?? "")
                }
            }
        }
    }

    private func openPack(_ p: PackSummary) {
        Task {
            _ = await app.openPack(dir: p.dir, title: p.title)
            if let f = app.failed { failed = f; app.failed = nil }
        }
    }

    private func row(_ p: PackSummary) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(p.title).font(Theme.display(26)).foregroundStyle(Theme.ink)
                    if !p.route.isEmpty { Text(p.route).font(.subheadline.weight(.medium)).foregroundStyle(Theme.muted) }
                    if p.highDetail {
                        Text("HD")
                            .font(.caption2.weight(.bold)).foregroundStyle(.white)
                            .padding(.horizontal, 5).padding(.vertical, 1.5)
                            .background(Theme.track, in: RoundedRectangle(cornerRadius: 4, style: .continuous))
                            .accessibilityLabel("High-detail pack")
                    }
                }
                Text(p.cities).font(.subheadline).foregroundStyle(Theme.ink)
                Text("\(day(p.takeoff, tz: p.tz)) · \(Fmt.clock(p.takeoff, tz: p.tz)) · \(Fmt.span(p.durationMin * 60))")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(Theme.muted)
                Text("\(Int(p.megabytes.rounded())) MB").font(.caption2).foregroundStyle(Theme.muted)
            }
        }
        .padding(.vertical, 4)
    }

    private func day(_ d: Date, tz: String?) -> String {
        var style = Date.FormatStyle.dateTime.weekday(.abbreviated).day().month(.abbreviated).year()
        if let tz, let z = TimeZone(identifier: tz) { style.timeZone = z }
        return d.formatted(style)
    }

    private var empty: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("No flights yet").font(Theme.display(30)).foregroundStyle(Theme.ink)
                Text("A flight arrives as one file, made on the Mac before the trip. It holds the routes, everything that might be seen, the pictures and an offline map, so nothing here needs a network in the air.")
                    .foregroundStyle(Theme.ink)
                step("1", "On the Mac", "uv run planeview pack out/<flight> --icloud")
                step("2", "On the phone", "Tap + and pick the .planeview file from iCloud Drive › PlaneView. Or AirDrop the file to this phone and choose Plane View.")
                step("3", "Before you board", "Open the flight once and allow location. GPS works in flight mode by a window; the app carries on without it when it does not.")
                Button { picking = true } label: {
                    Label("Add a flight", systemImage: "plus").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent).tint(Theme.track).controlSize(.large)
            }
            .padding(20)
        }
    }

    private func step(_ n: String, _ title: String, _ body: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text(n).font(Theme.display(20)).foregroundStyle(.white)
                .frame(width: 28, height: 28).background(Theme.track, in: Circle())
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.ink)
                Text(body).font(.subheadline).foregroundStyle(Theme.muted).textSelection(.enabled)
            }
        }
    }
}
