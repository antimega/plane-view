import Foundation
import Observation
import PlaneViewCore
import UniformTypeIdentifiers

extension UTType {
    static let flightPack = UTType(exportedAs: "app.planeview.pack")
}

struct PackSummary: Identifiable, Hashable {
    var id: String
    var dir: URL
    var title: String
    var route: String
    var cities: String
    var date: String
    var takeoff: Date
    var tz: String?             // the departure airport's time zone: the list shows its local time
    var durationMin: Double
    var megabytes: Double
    var features: Int
    var highDetail = false
}

/// The flight packs on this phone. A pack arrives as one .planeview file - by AirDrop, from Files or iCloud Drive,
/// or dropped into the app's folder - and is unpacked into Application Support, where nothing needs a network.
@MainActor @Observable
final class PackLibrary {
    var packs: [PackSummary] = []
    var importing = false
    var lastError: String?

    private let fm = FileManager.default
    private var inboxBusy = false
    let root: URL

    init() {
        root = URL.applicationSupportDirectory.appendingPathComponent("Packs", isDirectory: true)
        try? fm.createDirectory(at: root, withIntermediateDirectories: true)
        scan()
    }

    func scan() {
        var out: [PackSummary] = []
        for dir in (try? fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)) ?? [] {
            guard let m = Pack.manifest(in: dir), m.schema == "plane-view/pack/1" else { continue }
            let f = m.flight
            out.append(PackSummary(id: m.id, dir: dir, title: f.title, route: f.number != nil ? f.route : "",
                                   cities: "\(f.orig.city ?? f.orig.code) to \(f.dest.city ?? f.dest.code)", date: f.date,
                                   takeoff: Date(timeIntervalSince1970: f.takeoff_unix), tz: f.orig.tz, durationMin: f.duration_min,
                                   megabytes: Double(folderSize(dir)) / 1e6, features: m.counts?["features"] ?? 0,
                                   highDetail: m.detail == "high"))
        }
        packs = out.sorted { $0.takeoff < $1.takeoff }
    }

    private func folderSize(_ dir: URL) -> Int {
        let marker = dir.appendingPathComponent(".bytes")
        if let s = try? String(contentsOf: marker, encoding: .utf8), let n = Int(s) { return n }
        var n = 0
        if let e = fm.enumerator(at: dir, includingPropertiesForKeys: [.fileSizeKey]) {
            for case let u as URL in e { n += (try? u.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0 }
        }
        try? String(n).write(to: marker, atomically: true, encoding: .utf8)
        return n
    }

    /// Unpack a .planeview file (from the file picker, AirDrop's "open in", or the app's own folder).
    @discardableResult
    func importPack(from url: URL, removeSource: Bool = false) async -> String? {
        importing = true
        defer { importing = false }
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let root = self.root
        do {
            let id = try await Task.detached(priority: .userInitiated) { () throws -> String in
                let fm = FileManager.default
                let tmp = root.appendingPathComponent(".incoming-\(UUID().uuidString)", isDirectory: true)
                defer { try? fm.removeItem(at: tmp) }
                // iCloud Drive files may still be in the cloud: a coordinated read brings them down first
                var coordError: NSError?
                var failure: Error?
                NSFileCoordinator().coordinate(readingItemAt: url, options: [], error: &coordError) { u in
                    do { try Zip.extract(u, to: tmp) } catch { failure = error }
                }
                if let e = coordError ?? failure as NSError? { throw e }
                guard let m = Pack.manifest(in: tmp) else { throw Zip.Failure.damaged("no pack.json") }
                guard m.schema == "plane-view/pack/1" else { throw Pack.Failure.schema(m.schema) }
                let dst = root.appendingPathComponent(m.id, isDirectory: true)
                try? fm.removeItem(at: dst)
                try fm.moveItem(at: tmp, to: dst)
                return m.id
            }.value
            if removeSource { try? fm.removeItem(at: url) }
            lastError = nil
            scan()
            return id
        } catch {
            lastError = "\(url.lastPathComponent): \(error.localizedDescription)"
            return nil
        }
    }

    /// Packs dropped into the app's folder (Files > On My iPhone > Plane View, or Finder) or left by "open in".
    func importInbox() async {
        // launch and coming to the front both ask: one pass at a time, or a big pack is unpacked twice
        guard !inboxBusy else { return }
        inboxBusy = true
        defer { inboxBusy = false }
        let docs = URL.documentsDirectory
        var found: [URL] = []
        for dir in [docs, docs.appendingPathComponent("Inbox")] {
            for u in (try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)) ?? [] where u.pathExtension == "planeview" {
                found.append(u)
            }
        }
        for u in found { await importPack(from: u, removeSource: true) }
    }

    func delete(_ p: PackSummary) {
        try? fm.removeItem(at: p.dir)
        scan()
    }
}
