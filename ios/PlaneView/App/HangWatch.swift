import Foundation

/// Notices when the main thread stops answering, and keeps a note of what it was last doing.
/// The note goes to Documents/hangs.log (Files > On My iPhone > Plane View, or readable from the Mac), because a
/// frozen app leaves no crash report: it is simply killed.
enum HangWatch {
    private static let lock = NSLock()
    private static var crumb = "launch"
    private static var crumbAt = Date()
    private static var lastPong = Date()
    private static var reported = false
    private static var timer: DispatchSourceTimer?

    /// Leave a note of what the main thread is about to do (cheap; call it at the start of anything substantial).
    static func mark(_ what: String) {
        lock.lock(); crumb = what; crumbAt = Date(); lock.unlock()
    }

    static func start() {
        guard timer == nil else { return }
        let t = DispatchSource.makeTimerSource(queue: DispatchQueue(label: "planeview.hangwatch", qos: .utility))
        t.schedule(deadline: .now() + 2, repeating: 1)
        t.setEventHandler {
            DispatchQueue.main.async { lock.lock(); lastPong = Date(); reported = false; lock.unlock() }
            lock.lock()
            let stale = Date().timeIntervalSince(lastPong), what = crumb, since = Date().timeIntervalSince(crumbAt), told = reported
            if stale > 4, !told { reported = true }
            lock.unlock()
            if stale > 4, !told {
                let line = "\(ISO8601DateFormatter().string(from: Date())) main thread silent for \(Int(stale)) s; last: \(what) (\(Int(since)) s ago)\n"
                let url = URL.documentsDirectory.appendingPathComponent("hangs.log")
                if let h = try? FileHandle(forWritingTo: url) {
                    _ = try? h.seekToEnd(); try? h.write(contentsOf: Data(line.utf8)); try? h.close()
                } else {
                    try? Data(line.utf8).write(to: url)
                }
            }
        }
        t.resume()
        timer = t
    }
}
