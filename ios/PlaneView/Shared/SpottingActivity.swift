import ActivityKit
import Foundation
import SwiftUI

/// What the Lock Screen and the Dynamic Island show while you are in Camera or Maps: the next thing to look for,
/// with a countdown the system keeps running by itself, and what is in view now.
struct SpottingAttributes: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        var nextName: String?
        var nextWhat: String?
        var nextWhere: String?        // "2 o'clock · a little below the horizon"
        var nextAt: Date?             // when it should come into view
        var inView: [String]          // the best few things in view now
        var position: String          // "GPS", "Estimated ±3 min", "Timetable"
        var uncertain: Bool
    }
    var flight: String                // "SN3741", or "BRU-AGP" when there is no flight number
    var route: String                 // "BRU → AGP"
    var side: String                  // "L" or "R"
}

enum Palette {
    static let port = Color(red: 0.86, green: 0.27, blue: 0.23)       // left window
    static let starboard = Color(red: 0.16, green: 0.62, blue: 0.38)  // right window
    static let track = Color(red: 0.86, green: 0.16, blue: 0.52)
    static func side(_ s: String) -> Color { s == "L" ? port : starboard }
}
