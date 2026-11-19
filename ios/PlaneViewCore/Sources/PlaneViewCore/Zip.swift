import Compression
import Foundation

/// Just enough of ZIP to unpack a flight pack: stored and deflated entries, no encryption, no ZIP64.
/// (Foundation has no public unzip; a pack is written by planeview/pack.py, so this need not read anything else.)
public enum Zip {
    public struct Entry {
        public let name: String
        let method: UInt16
        let compressedSize: Int
        let size: Int
        let headerOffset: Int
    }

    public enum Failure: Error, LocalizedError {
        case notAZip, unsupported(String), damaged(String)
        public var errorDescription: String? {
            switch self {
            case .notAZip: return "This is not a flight pack (no ZIP directory)."
            case .unsupported(let s): return "The flight pack uses \(s), which this app cannot read."
            case .damaged(let s): return "The flight pack is damaged (\(s))."
            }
        }
    }

    public static func entries(_ data: Data) throws -> [Entry] {
        let n = data.count
        guard n >= 22 else { throw Failure.notAZip }
        // end of central directory: search back from the end (a comment may follow it)
        var eocd = -1
        var i = n - 22
        let stop = max(0, n - 22 - 65_535)
        while i >= stop {
            if data.u32(i) == 0x0605_4b50 { eocd = i; break }
            i -= 1
        }
        guard eocd >= 0 else { throw Failure.notAZip }
        let count = Int(data.u16(eocd + 10))
        var p = Int(data.u32(eocd + 16))
        if count == 0xFFFF || p == 0xFFFF_FFFF { throw Failure.unsupported("ZIP64") }
        var out: [Entry] = []
        out.reserveCapacity(count)
        for _ in 0..<count {
            guard p + 46 <= n, data.u32(p) == 0x0201_4b50 else { throw Failure.damaged("directory") }
            let flags = data.u16(p + 8)
            if flags & 1 != 0 { throw Failure.unsupported("encryption") }
            let method = data.u16(p + 10)
            let csize = Int(data.u32(p + 20)), usize = Int(data.u32(p + 24))
            let nl = Int(data.u16(p + 28)), el = Int(data.u16(p + 30)), cl = Int(data.u16(p + 32))
            let off = Int(data.u32(p + 42))
            guard p + 46 + nl <= n else { throw Failure.damaged("names") }
            let name = String(decoding: data.subdata(in: p + 46..<p + 46 + nl), as: UTF8.self)
            out.append(Entry(name: name, method: method, compressedSize: csize, size: usize, headerOffset: off))
            p += 46 + nl + el + cl
        }
        return out
    }

    public static func read(_ e: Entry, from data: Data) throws -> Data {
        let h = e.headerOffset
        guard h + 30 <= data.count, data.u32(h) == 0x0403_4b50 else { throw Failure.damaged(e.name) }
        let start = h + 30 + Int(data.u16(h + 26)) + Int(data.u16(h + 28))
        guard start + e.compressedSize <= data.count else { throw Failure.damaged(e.name) }
        let raw = data.subdata(in: start..<start + e.compressedSize)
        switch e.method {
        case 0: return raw
        case 8:
            if e.size == 0 { return Data() }
            var out = Data(count: e.size)
            let got = out.withUnsafeMutableBytes { dst -> Int in
                raw.withUnsafeBytes { src -> Int in
                    compression_decode_buffer(dst.bindMemory(to: UInt8.self).baseAddress!, e.size,
                                              src.bindMemory(to: UInt8.self).baseAddress!, raw.count, nil, COMPRESSION_ZLIB)
                }
            }
            guard got == e.size else { throw Failure.damaged(e.name) }
            return out
        default: throw Failure.unsupported("compression method \(e.method)")
        }
    }

    /// Unpack every file under `dir` (created if need be). Names that would climb out of it are refused.
    public static func extract(_ url: URL, to dir: URL) throws {
        let data = try Data(contentsOf: url, options: .mappedIfSafe)
        let fm = FileManager.default
        try fm.createDirectory(at: dir, withIntermediateDirectories: true)
        for e in try entries(data) {
            if e.name.hasSuffix("/") { continue }
            let parts = e.name.split(separator: "/")
            if e.name.hasPrefix("/") || parts.contains("..") { throw Failure.damaged("path \(e.name)") }
            let dst = dir.appendingPathComponent(e.name)
            try fm.createDirectory(at: dst.deletingLastPathComponent(), withIntermediateDirectories: true)
            try read(e, from: data).write(to: dst, options: .atomic)
        }
    }
}

private extension Data {
    func u16(_ o: Int) -> UInt16 {
        let s = startIndex + o
        return UInt16(self[s]) | UInt16(self[s + 1]) << 8
    }
    func u32(_ o: Int) -> UInt32 {
        let s = startIndex + o
        return UInt32(self[s]) | UInt32(self[s + 1]) << 8 | UInt32(self[s + 2]) << 16 | UInt32(self[s + 3]) << 24
    }
}
