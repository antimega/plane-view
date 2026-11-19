import PlaneViewCore
import SwiftUI
import UIKit

/// The forecast page's colours, for day and for a dark cabin.
enum Theme {
    static func dyn(_ light: UInt32, _ dark: UInt32) -> Color {
        Color(UIColor { $0.userInterfaceStyle == .dark ? UIColor(hex: dark) : UIColor(hex: light) })
    }
    static let paper = dyn(0xF1F2EE, 0x0D1217)
    static let card = dyn(0xFFFFFF, 0x141B22)
    static let ink = dyn(0x17202A, 0xE3E8ED)
    static let muted = dyn(0x59646F, 0x94A1AD)
    static let line = dyn(0xD8DCD8, 0x25303A)
    static let chip = dyn(0xE7EAE4, 0x1C252E)
    static let track = dyn(0xB5126B, 0xFF5AAE)
    static let port = dyn(0xC2271F, 0xFF6B60)
    static let starboard = dyn(0x13804A, 0x4FD08A)
    static let ok = dyn(0x2D7A4C, 0x62C48F)
    static let warn = dyn(0x9A6A12, 0xE2B04E)
    static let bad = dyn(0xB42318, 0xF2766B)
    static let water = dyn(0x2B689B, 0x7DB9E8)

    static func side(_ s: Side) -> Color { s == .L ? port : starboard }

    static func display(_ size: CGFloat, _ weight: Font.Weight = .semibold) -> Font {
        .system(size: size, weight: weight).width(.condensed)
    }
}

extension UIColor {
    convenience init(hex: UInt32) {
        self.init(red: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255, blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
    }
}

enum Fmt {
    /// "in 40 s", "in 4 min", "in 1 h 20"; "now" when it is upon us.
    static func until(_ s: Double) -> String {
        if s < 20 { return "now" }
        if s < 90 { return "in \(Int((s / 10).rounded()) * 10) s" }
        if s < 90 * 60 { return "in \(Int((s / 60).rounded())) min" }
        if s > 36 * 3600 { return "in \(Int((s / 86400).rounded())) days" }
        return "in \(span(s))"
    }

    static func ago(_ s: Double) -> String {
        if s < 60 { return "just now" }
        if s < 90 * 60 { return "\(Int((s / 60).rounded())) min ago" }
        return "\(span(s)) ago"
    }

    /// "45 s", "12 min", "1 h 20"
    static func span(_ s: Double) -> String {
        let s = abs(s)
        if s < 60 { return "\(Int(s.rounded())) s" }
        let m = Int((s / 60).rounded())
        if m < 90 { return "\(m) min" }
        return "\(m / 60) h \(String(format: "%02d", m % 60))"
    }

    static func feet(_ ft: Double) -> String {
        let v = ft >= 10000 ? (ft / 1000).rounded() * 1000 : (ft / 100).rounded() * 100
        return v.formatted(.number.grouping(.automatic)) + " ft"
    }

    static func km(_ d: Double) -> String { d < 10 ? String(format: "%.1f km", d) : "\(Int(d.rounded())) km" }

    static func clock(_ date: Date, tz: String?) -> String {
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        if let tz, let z = TimeZone(identifier: tz) { f.timeZone = z }
        return f.string(from: date)
    }

    static func kindIcon(_ kind: String) -> String {
        switch kind {
        case "settlement": return "building.2"
        case "peak", "range": return "mountain.2"
        case "lake": return "drop"
        case "river": return "water.waves"
        case "island": return "leaf"
        case "coast": return "beach.umbrella"
        case "sea": return "water.waves"
        case "landmark": return "building.columns"
        default: return "mappin"
        }
    }
}
