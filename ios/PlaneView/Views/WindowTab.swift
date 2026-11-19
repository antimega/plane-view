import PlaneViewCore
import SwiftUI

struct DetailItem: Identifiable {
    var sighting: Sighting
    var plan: PlanSighting?
    var also: [Sighting] = []
    var remaining: Double? = nil      // seconds it should stay in view, when it is in view
    var best: Sighting? = nil         // the sighting at its best moment: what its pictures are drawn for
    var id: String { sighting.id }
}

/// The flight's sightings as a vertical carousel, in the order they come. The one to look at now is the large
/// card; its neighbours either side keep a couple of lines; further off, earlier and later, they shrink to a title.
/// The list holds itself with one earlier sighting showing above the main card; everything flown over is a scroll
/// up, everything to come a scroll down.
struct WindowTab: View {
    var session: FlightSession
    var board: Board
    @Binding var detail: DetailItem?
    @Binding var zoomed: ZoomedPicture?
    @State private var following = true
    @State private var showAll = false

    private var pack: Pack { session.pack }

    private enum Entry: Identifiable {
        case sight(Int, TimelineRow)
        case context(ContextRow)
        var key: Double {
            switch self { case .sight(_, let r): return r.best; case .context(let c): return c.eta }
        }
        var id: String {
            switch self { case .sight(_, let r): return "s" + r.id; case .context(let c): return "c" + c.id }
        }
    }

    private var entries: [Entry] {
        let sights = board.timeline.enumerated().map { Entry.sight($0.offset, $0.element) }
        return (sights + board.context.map { Entry.context($0) }).sorted { $0.key < $1.key }
    }

    /// The row the list is held at: the sighting before the one in focus, so that it stays in sight above it.
    private var anchorID: String? {
        guard let f = board.focus else { return nil }
        return "s" + board.timeline[max(0, f - 1)].id
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 6) {
                    if board.timeline.isEmpty {
                        Text(board.scheduleReady ? "Nothing named on this side of the flight." : "Working out what is in view…")
                            .font(.subheadline).foregroundStyle(Theme.muted).padding(.vertical, 20)
                    }
                    ForEach(entries) { e in
                        switch e {
                        case .sight(let i, let r):
                            let far = abs(i - (board.focus ?? i))
                            if far == 0 {
                                // not one button: its two pictures open full width, the rest of it opens the sighting
                                MainCard(session: session, pack: pack, row: r, board: board, marked: marked(r), showAll: { showAll = true },
                                         open: { open(r) }, zoom: { z in withAnimation(.easeOut(duration: 0.18)) { zoomed = z } })
                                    .id(e.id)
                            } else {
                            Button { open(r) } label: {
                                if far <= 2 {
                                    NearRow(pack: pack, row: r, side: board.side, before: beforeTakeoff, marked: marked(r))
                                } else {
                                    FarRow(pack: pack, row: r, before: beforeTakeoff, marked: marked(r))
                                }
                            }
                            .buttonStyle(.plain)
                            .id(e.id)
                            }
                        case .context(let c):
                            HStack(spacing: 8) {
                                Text(Fmt.until(c.eta)).font(.caption.monospacedDigit()).foregroundStyle(Theme.muted)
                                    .frame(width: 76, alignment: .trailing)
                                Image(systemName: contextIcon(c.item.type)).font(.caption2).foregroundStyle(Theme.muted)
                                Text(c.item.text).font(.caption).foregroundStyle(Theme.muted).lineLimit(1)
                                Spacer(minLength: 0)
                            }
                            .padding(.vertical, 1)
                        }
                    }
                    Text("A clear view is assumed. Directions count from the nose: 3 o'clock is straight out of the right-hand window, 9 o'clock the left.")
                        .font(.caption2).foregroundStyle(Theme.muted).padding(.top, 10)
                    // room to bring the last sightings up to the top
                    Color.clear.frame(height: 260)
                }
                .padding(.horizontal, 12).padding(.top, 4)
            }
            .onScrollPhaseChange { _, phase in
                if phase == .interacting { following = false }
            }
            .onChange(of: anchorID) { _, id in
                if following, let id { withAnimation(.snappy(duration: 0.35)) { proxy.scrollTo(id, anchor: .top) } }
            }
            .onChange(of: session.viewT) { _, _ in
                // the bar along the bottom moves the moment shown: the list goes with it
                following = true
                if let id = anchorID { proxy.scrollTo(id, anchor: .top) }
            }
            .onAppear {
                if let id = anchorID { proxy.scrollTo(id, anchor: .top) }
                warmPictures()
            }
            .onChange(of: board.focus) { _, _ in warmPictures() }
            .onChange(of: board.side) { _, _ in warmPictures() }
            .onChange(of: session.scheduleVersion) { _, _ in warmPictures() }
            .overlay(alignment: .bottom) {
                if !following, anchorID != nil {
                    Button {
                        following = true
                        if let id = anchorID { withAnimation(.snappy(duration: 0.35)) { proxy.scrollTo(id, anchor: .top) } }
                    } label: {
                        Label(board.live ? "Back to what's in view" : "Back to that moment", systemImage: "scope")
                            .font(.footnote.weight(.semibold))
                            .padding(.horizontal, 14).padding(.vertical, 9)
                            .background(Theme.ink, in: Capsule())
                            .foregroundStyle(Theme.paper)
                    }
                    .padding(.bottom, 10)
                    .transition(.opacity)
                }
            }
        }
        .sheet(isPresented: $showAll) {
            AllInViewSheet(session: session)
        }
    }

    /// Have the pictures of the main card and of the sightings either side of it decoded before they are shown:
    /// the window views of the next few to come (each will be the main card in turn), and both pictures of the
    /// nearest ones, which a tap opens in full.
    private func warmPictures() {
        guard let f = board.focus else { return }
        HangWatch.mark("window: warm pictures")
        defer { HangWatch.mark("window: warm pictures done") }
        var urls: [URL] = []
        for i in max(0, f - 2)...min(board.timeline.count - 1, f + 5) {
            guard let p = board.timeline[i].plan else { continue }
            if let v = p.view?.file { urls.append(pack.file(v)) }
            if abs(i - f) <= 2, let a = p.aerial?.file { urls.append(pack.file(a)) }
        }
        PictureCache.warm(urls)
        // and have the app's own pictures drawn for the next few that the forecast has none for
        for i in max(0, f - 1)...min(board.timeline.count - 1, f + 3) {
            let r = board.timeline[i]
            _ = session.windowArt(r.headline.main, plan: r.plan)
            _ = session.mapArt(r.headline.main, plan: r.plan, also: r.headline.also)
        }
    }

    private var beforeTakeoff: Bool { board.live && board.estimate.phase == .beforeDeparture }

    private func marked(_ r: TimelineRow) -> Bool {
        session.marked.contains(pack.features[r.headline.main.feature].id)
    }

    private func open(_ r: TimelineRow) {
        detail = DetailItem(sighting: r.look, plan: r.plan, also: r.headline.also,
                            remaining: r.state == .inView && r.live != nil ? max(0, r.end) : nil, best: r.headline.main)
    }

    private func contextIcon(_ type: String) -> String {
        switch type {
        case "toc": return "arrow.up.right"
        case "tod": return "arrow.down.right"
        case "coast_in", "coast_out", "sea": return "water.waves"
        case "country": return "flag"
        case "sunrise": return "sunrise"
        case "sunset": return "sunset"
        default: return "circle.dotted"
        }
    }
}

/// How a sighting stands against the moment shown, in a few words.
private func timing(_ r: TimelineRow, before: Bool) -> (text: String, urgent: Bool) {
    if before { return ("\(Int((r.headline.main.t0 / 60).rounded())) min after takeoff", false) }
    switch r.state {
    case .passed: return (Fmt.ago(-r.end), false)
    case .coming: return (Fmt.until(r.start), r.start < 120)
    case .inView:
        if r.maybe { return ("about now", true) }
        return (r.end < 45 ? "in view, going" : "in view, \(Fmt.span(r.end)) more", true)
    }
}

/// The one to look at: a big window view, where to look, and what else is there.
struct MainCard: View {
    var session: FlightSession
    var pack: Pack
    var row: TimelineRow
    var board: Board
    var marked: Bool
    var showAll: () -> Void
    var open: () -> Void
    var zoom: (ZoomedPicture) -> Void

    var body: some View {
        let side = board.side
        let s = row.look
        let f = pack.features[s.feature]
        let w = whereWords(side: side, fa: s.fa, dep: s.dep)
        let before = board.live && board.estimate.phase == .beforeDeparture
        let t = timing(row, before: before)
        // every sighting has both pictures: the forecast's where it made them, the app's own otherwise (drawn in
        // the background - reading the version brings the card back when one arrives)
        let _ = session.art.version
        let _ = HangWatch.mark("window: main card body")
        let best = row.headline.main
        let windowArt = session.windowArt(best, plan: row.plan)
        let mapArt = session.mapArt(best, plan: row.plan, also: row.headline.also)
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(t.text.uppercased())
                    .font(.caption.weight(.bold)).tracking(0.6)
                    .foregroundStyle(row.state == .inView ? Theme.side(side) : Theme.muted)
                if row.maybe { Text("position uncertain").font(.caption2).foregroundStyle(Theme.warn) }
                Spacer(minLength: 0)
                if marked { Image(systemName: "checkmark.circle.fill").font(.footnote).foregroundStyle(Theme.ok) }
            }
            HStack(alignment: .top, spacing: 12) {
                Button { zoom(ZoomedPicture(kind: .window(windowArt), side: side, name: f.name)) } label: {
                    WindowPicture(art: windowArt, side: side, name: f.name)
                }
                .buttonStyle(.plain)
                .containerRelativeFrame(.horizontal) { width, _ in width * 0.56 }
                .accessibilityHint("Shows it full width")
                VStack(alignment: .leading, spacing: 4) {
                    Text(f.name).font(Theme.display(25)).foregroundStyle(Theme.ink)
                        .lineLimit(2).minimumScaleFactor(0.7).fixedSize(horizontal: false, vertical: true)
                    if let what = f.what { Text(what).font(.footnote).foregroundStyle(Theme.muted).lineLimit(1) }
                    HStack(spacing: 6) {
                        DirectionGlyph(side: side, fa: s.fa, dep: s.dep, size: 34)
                        VStack(alignment: .leading, spacing: 0) {
                            Text(w.clock).font(.headline).foregroundStyle(Theme.side(side))
                            Text(Fmt.km(s.dKm)).font(.caption).foregroundStyle(Theme.muted)
                        }
                    }
                    Text(w.words).font(.caption).foregroundStyle(Theme.ink).lineLimit(2).fixedSize(horizontal: false, vertical: true)
                    if row.state != .inView || row.live == nil {
                        Text(row.state == .passed ? "as it was at its best" : "as it will be at its best")
                            .font(.caption2).foregroundStyle(Theme.muted)
                    }
                    Spacer(minLength: 2)
                    Button { zoom(ZoomedPicture(kind: .map(mapArt), side: side, name: f.name)) } label: {
                        AerialPicture(art: mapArt, name: f.name, compact: true)
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Shows it full width")
                }
            }
            .fixedSize(horizontal: false, vertical: true)
            let others = board.now.filter { !$0.maybe && $0.sighting.feature != s.feature }
            if !others.isEmpty && (board.live ? board.estimate.phase == .airborne : true) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("Also in view: " + others.prefix(4).map { pack.features[$0.sighting.feature].name }.joined(separator: ", "))
                        .font(.footnote).foregroundStyle(Theme.ink).lineLimit(2)
                    Spacer(minLength: 0)
                    Button(action: showAll) {
                        Text("all \(others.count + (row.live != nil ? 1 : 0))")
                            .font(.footnote.weight(.semibold)).foregroundStyle(Theme.track)
                            .padding(.horizontal, 9).padding(.vertical, 4)
                            .background(Theme.chip, in: Capsule())
                    }
                    .accessibilityLabel("Everything in view")
                }
            } else if !row.headline.also.isEmpty {
                Text("With it: " + row.headline.also.prefix(5).map { pack.features[$0.feature].name }.joined(separator: ", "))
                    .font(.footnote).foregroundStyle(Theme.muted).lineLimit(2)
            }
        }
        .padding(12)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous)
            .stroke(Theme.side(side).opacity(row.state == .inView ? 0.75 : 0.3), lineWidth: 1.5))
        .padding(.vertical, 4)
        .contentShape(Rectangle())
        .onTapGesture(perform: open)
    }
}

/// A neighbour of the main card: when, what, and where to look.
struct NearRow: View {
    var pack: Pack
    var row: TimelineRow
    var side: Side
    var before: Bool
    var marked: Bool

    var body: some View {
        let s = row.look
        let f = pack.features[s.feature]
        let w = whereWords(side: side, fa: s.fa, dep: s.dep)
        let t = timing(row, before: before)
        HStack(alignment: .center, spacing: 10) {
            DirectionGlyph(side: side, fa: s.fa, dep: s.dep, size: 38)
                .opacity(row.state == .passed ? 0.45 : 1)
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 6) {
                    Text(f.name).font(.body.weight(.semibold)).foregroundStyle(row.state == .passed ? Theme.muted : Theme.ink).lineLimit(1)
                    if row.plan?.view?.file != nil { Image(systemName: "photo").font(.caption2).foregroundStyle(Theme.muted) }
                    if marked { Image(systemName: "checkmark.circle.fill").font(.caption).foregroundStyle(Theme.ok) }
                }
                Text([f.what, "\(w.clock), \(w.words)", Fmt.km(s.dKm)].compactMap { $0 }.joined(separator: " · "))
                    .font(.footnote).foregroundStyle(Theme.muted).lineLimit(2).fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 4)
            Text(t.text)
                .font(.caption.weight(t.urgent ? .semibold : .regular).monospacedDigit())
                .foregroundStyle(t.urgent ? Theme.side(side) : Theme.muted)
                .multilineTextAlignment(.trailing)
                .frame(maxWidth: 78, alignment: .trailing)
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .background(Theme.card.opacity(row.state == .passed ? 0.6 : 1), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

/// Further off, earlier or later: just the name and when.
struct FarRow: View {
    var pack: Pack
    var row: TimelineRow
    var before: Bool
    var marked: Bool

    var body: some View {
        let f = pack.features[row.headline.main.feature]
        let t = timing(row, before: before)
        HStack(spacing: 8) {
            Text(before ? "+\(Int((row.headline.main.t0 / 60).rounded())) min" : t.text)
                .font(.caption.monospacedDigit()).foregroundStyle(t.urgent ? Theme.ink : Theme.muted)
                .lineLimit(1).frame(width: 76, alignment: .trailing)
            Text(f.name).font(.subheadline).foregroundStyle(row.state == .passed ? Theme.muted : Theme.ink).lineLimit(1)
            if marked { Image(systemName: "checkmark.circle.fill").font(.caption2).foregroundStyle(Theme.ok) }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 3)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}

/// Everything in view at the moment shown, best first - the long list behind the main card's "all".
struct AllInViewSheet: View {
    var session: FlightSession
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: 6) {
                    if let b = session.board {
                        ForEach(b.now) { r in
                            NowLine(pack: session.pack, row: r, side: b.side,
                                    marked: session.marked.contains(session.pack.features[r.sighting.feature].id))
                        }
                    }
                }
                .padding(12)
            }
            .background(Theme.paper)
            .navigationTitle("In view")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
    }
}

struct NowLine: View {
    var pack: Pack
    var row: NowRow
    var side: Side
    var marked: Bool

    var body: some View {
        let f = pack.features[row.sighting.feature]
        let w = whereWords(side: side, fa: row.sighting.fa, dep: row.sighting.dep)
        HStack(spacing: 10) {
            DirectionGlyph(side: side, fa: row.sighting.fa, dep: row.sighting.dep, size: 40)
                .opacity(row.maybe ? 0.45 : 1)
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 6) {
                    Text(f.name).font(.body.weight(.semibold)).foregroundStyle(Theme.ink).lineLimit(1)
                    if marked { Image(systemName: "checkmark.circle.fill").font(.caption).foregroundStyle(Theme.ok) }
                }
                if row.maybe {
                    Text("about now: the position is uncertain").font(.footnote).foregroundStyle(Theme.warn)
                } else {
                    Text("\(w.clock) · \(w.words)").font(.footnote).foregroundStyle(Theme.ink).lineLimit(1)
                }
                Text([f.what, Fmt.km(row.sighting.dKm)].compactMap { $0 }.joined(separator: " · "))
                    .font(.caption).foregroundStyle(Theme.muted).lineLimit(1)
            }
            Spacer(minLength: 0)
            if let r = row.remaining, !row.maybe {
                Text(r < 45 ? "going" : Fmt.span(r) + " more")
                    .font(.caption.monospacedDigit()).foregroundStyle(r < 45 ? Theme.warn : Theme.muted)
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}
