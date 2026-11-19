import PlaneViewCore
import SwiftUI

/// Which flight is open. Reading a pack takes a moment (15,000 places), so it is done off the main thread; and
/// the open flight is remembered, so that if iOS throws the app out while you are in Camera, coming back lands
/// in the flight, not in the list.
@MainActor @Observable
final class AppModel {
    var open: FlightSession?
    var opening: String?
    var failed: String?

    func openPack(dir: URL, title: String) async -> FlightSession? {
        opening = title
        defer { opening = nil }
        let loaded = await Task.detached(priority: .userInitiated) { () -> Result<Pack, Error> in
            Result { try Pack(dir: dir) }
        }.value
        switch loaded {
        case .success(let pack):
            open?.stop()
            let s = FlightSession(pack: pack)
            open = s
            UserDefaults.standard.set(pack.id, forKey: "lastOpen")
            return s
        case .failure(let e):
            failed = "\(title): \(e.localizedDescription)"
            return nil
        }
    }

    func close() {
        open?.stop()
        open = nil
        UserDefaults.standard.removeObject(forKey: "lastOpen")
    }
}

@main
struct PlaneViewApp: App {
    @State private var library = PackLibrary()
    @State private var app = AppModel()
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            LibraryView(library: library, app: app)
                .task {
                    HangWatch.start()
                    await library.importInbox()
                    await launchArguments()
                }
                .onOpenURL { url in
                    Task {
                        if let id = await library.importPack(from: url, removeSource: url.path.contains("/Inbox/")) {
                            await openPack(id)
                        }
                    }
                }
                .onChange(of: phase) { _, p in
                    if p == .active {
                        app.open?.cameToFront()
                        Task { await library.importInbox() }
                    } else if p == .background {
                        app.open?.wentToBack()
                    }
                }
        }
    }

    private func openPack(_ id: String) async {
        guard let p = library.packs.first(where: { $0.id == id }) else { return }
        _ = await app.openPack(dir: p.dir, title: p.title)
    }

    /// For testing from the Mac:  simctl launch <device> <bundle id> -open <pack id> -rehearse V1 -rate 30
    ///   -from <plan seconds> -shift <km> -side L|R -gpsoff <after plan seconds> -tab map
    private func launchArguments() async {
        let d = UserDefaults.standard
        guard let id = d.string(forKey: "open") ?? (d.string(forKey: "rehearse") != nil ? library.packs.first?.id : nil)
            ?? d.string(forKey: "lastOpen") else { return }
        await openPack(id)
        guard let s = app.open else { return }
        if let sd = d.string(forKey: "side").flatMap({ Side(rawValue: $0) }) { s.side = sd }
        if let path = d.string(forKey: "rehearse") {
            let rate = d.double(forKey: "rate")
            s.startRehearsal(pathID: path, rate: rate > 0 ? rate : 30, fromT: d.double(forKey: "from"), shiftKm: d.double(forKey: "shift"))
            if let pin = d.object(forKey: "pinat") as? String, let t = Double(pin) { s.model.navigator.setPin(Pin(at: s.now, tau: t)) }
            let off = d.double(forKey: "gpsoff")
            if off > 0 {
                Task {
                    while let r = s.rehearsal, r.t(at: Date()) < off { try? await Task.sleep(for: .milliseconds(300)) }
                    s.setRehearsal(gpsOff: true)
                }
            }
        }
    }
}
