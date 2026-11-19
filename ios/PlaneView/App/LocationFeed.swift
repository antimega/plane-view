import CoreLocation
import Foundation
import PlaneViewCore

/// GPS from the phone. Started while the app is in front, it keeps running when you switch to Camera or Maps
/// (the blue location pill shows it), so the track has no holes where the photos are.
final class LocationFeed: NSObject, CLLocationManagerDelegate {
    private let manager = CLLocationManager()
    var onFix: ((Fix) -> Void)?
    var onStatus: ((CLAuthorizationStatus) -> Void)?
    private(set) var running = false

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBest
        manager.distanceFilter = kCLDistanceFilterNone
        manager.activityType = .airborne
        manager.pausesLocationUpdatesAutomatically = false
    }

    var status: CLAuthorizationStatus { manager.authorizationStatus }

    func start() {
        if manager.authorizationStatus == .notDetermined { manager.requestWhenInUseAuthorization() }
        guard !running else { return }
        running = true
        manager.allowsBackgroundLocationUpdates = true
        manager.showsBackgroundLocationIndicator = true
        manager.startUpdatingLocation()
    }

    func stop() {
        guard running else { return }
        running = false
        manager.stopUpdatingLocation()
        manager.allowsBackgroundLocationUpdates = false
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        onStatus?(manager.authorizationStatus)
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        for l in locations where l.horizontalAccuracy >= 0 {
            onFix?(Fix(time: l.timestamp, lat: l.coordinate.latitude, lon: l.coordinate.longitude,
                       altM: l.verticalAccuracy > 0 ? l.altitude : nil, speed: l.speed >= 0 ? l.speed : nil,
                       course: l.course >= 0 ? l.course : nil, hAcc: l.horizontalAccuracy))
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        // no fix yet (common for minutes after switching flight mode on): the estimate carries on without one
    }
}
