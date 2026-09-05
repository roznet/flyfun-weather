import MapKit
import UIKit

struct ObservedMotionOverlaySnapshot {
    let overlays: [MKOverlay]
    let lightning: [ObservedMotionLightningAnnotation]
    let signature: String

    static let empty = ObservedMotionOverlaySnapshot(overlays: [], lightning: [], signature: "empty")
}

enum ObservedMotionOverlay {
    static func build(
        envelope: ObservedMotionEnvelope?,
        selectedProjectionTime: String?,
        enabledFamilies: Set<ObservedMotionFamily>,
        selectedFeatureID: String?,
        selectedAssociation: ObservedMotionAssociation? = nil,
        allowProjectedGeometry: Bool = true,
        storedOnly: Bool = false
    ) -> ObservedMotionOverlaySnapshot {
        guard let envelope, envelope.status == "available" else { return .empty }
        let selectedTime = selectedProjectionTime
        let highlighted = Set([
            selectedAssociation?.radarFeatureID,
            selectedAssociation?.cloudFeatureID,
            selectedFeatureID,
        ].compactMap { $0 })
        var overlays: [MKOverlay] = []
        for feature in envelope.features where enabledFamilies.contains(feature.family) {
            let projected = selectedTime != nil
            let geometry: ObservedMotionGeometry?
            if let selectedTime, allowProjectedGeometry {
                geometry = feature.projections.first(where: { $0.at == selectedTime && $0.status == "available" })?.displayGeometry
            } else if selectedTime == nil {
                geometry = feature.displayGeometry
            } else {
                geometry = nil
            }
            if let geometry, geometry.status == "available", let multi = geometry.geometry, multi.isValid {
                overlays.append(contentsOf: polygons(
                    multi, family: feature.family, featureID: feature.featureID,
                    projected: projected, selected: highlighted.contains(feature.featureID), storedOnly: storedOnly))
            }
            if selectedTime == nil, feature.trail.count >= 2 {
                let points = feature.trail.compactMap { coordinate($0.center) }
                if points.count >= 2 {
                    let trail = ObservedMotionPolyline(coordinates: points, count: points.count)
                    trail.family = feature.family
                    trail.featureID = feature.featureID
                    trail.isSelected = highlighted.contains(feature.featureID)
                    trail.isStoredAnalysis = storedOnly
                    overlays.append(trail)
                }
            }
        }
        let visibleIDs = Set(envelope.features.filter { enabledFamilies.contains($0.family) }.map(\.featureID))
        let lightning = envelope.lightning.compactMap { record -> ObservedMotionLightningAnnotation? in
            guard let coordinate = coordinate(record.position) else { return nil }
            if let ids = record.associatedFeatureIDs, !ids.isEmpty, visibleIDs.isDisjoint(with: ids) { return nil }
            return ObservedMotionLightningAnnotation(record: record, coordinate: coordinate)
        }
        let signature = [
            String(envelope.revision), selectedTime ?? "observed",
            enabledFamilies.map(\.rawValue).sorted().joined(separator: ","),
            selectedFeatureID ?? "none", selectedAssociation?.associationID ?? "none",
            allowProjectedGeometry ? "projected" : "hidden",
            storedOnly ? "stored" : "live",
        ].joined(separator: "|")
        return ObservedMotionOverlaySnapshot(overlays: overlays, lightning: lightning, signature: signature)
    }

    private static func polygons(
        _ multi: ObservedMotionMultiPolygon,
        family: ObservedMotionFamily,
        featureID: String,
        projected: Bool,
        selected: Bool,
        storedOnly: Bool
    ) -> [ObservedMotionPolygon] {
        multi.coordinates.compactMap { rings in
            guard let exterior = rings.first else { return nil }
            let outer = exterior.compactMap(coordinate)
            guard outer.count >= 4 else { return nil }
            let holes = rings.dropFirst().compactMap { ring -> MKPolygon? in
                let coordinates = ring.compactMap(coordinate)
                guard coordinates.count >= 4 else { return nil }
                return MKPolygon(coordinates: coordinates, count: coordinates.count)
            }
            let polygon = ObservedMotionPolygon(
                coordinates: outer, count: outer.count, interiorPolygons: holes)
            polygon.family = family
            polygon.featureID = featureID
            polygon.isProjected = projected
            polygon.isSelected = selected
            polygon.isStoredAnalysis = storedOnly
            return polygon
        }
    }

    private static func coordinate(_ point: [Double]) -> CLLocationCoordinate2D? {
        guard point.count == 2, point[0].isFinite, point[1].isFinite,
              (-180...180).contains(point[0]), (-90...90).contains(point[1]) else { return nil }
        return CLLocationCoordinate2D(latitude: point[1], longitude: point[0])
    }
}

final class ObservedMotionPolygon: MKPolygon {
    var family: ObservedMotionFamily = .radarEcho
    var featureID = ""
    var isProjected = false
    var isSelected = false
    var isStoredAnalysis = false
}

final class ObservedMotionPolyline: MKPolyline {
    var family: ObservedMotionFamily = .radarEcho
    var featureID = ""
    var isSelected = false
    var isStoredAnalysis = false
}

final class ObservedMotionLightningAnnotation: NSObject, MKAnnotation {
    let record: ObservedMotionLightning
    @objc dynamic var coordinate: CLLocationCoordinate2D
    var title: String? { record.timePrecision == "individual_time" ? "Reported lightning" : "Lightning time window" }

    init(record: ObservedMotionLightning, coordinate: CLLocationCoordinate2D) {
        self.record = record
        self.coordinate = coordinate
    }
}

final class ObservedMotionLightningView: MKAnnotationView {
    static let reuseID = "observedMotionLightning"

    override init(annotation: MKAnnotation?, reuseIdentifier: String?) {
        super.init(annotation: annotation, reuseIdentifier: reuseIdentifier)
        canShowCallout = true
        displayPriority = .required
        collisionMode = .circle
        bounds = CGRect(x: 0, y: 0, width: 16, height: 16)
        layer.cornerRadius = 8
        layer.borderWidth = 2
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func configure(precision: String) {
        backgroundColor = precision == "individual_time" ? .systemYellow : .clear
        layer.borderColor = UIColor.systemYellow.cgColor
        layer.borderWidth = precision == "individual_time" ? 1 : 3
    }
}

enum ObservedMotionOverlayRenderer {
    static func renderer(for overlay: MKOverlay) -> MKOverlayRenderer? {
        if let polygon = overlay as? ObservedMotionPolygon {
            let renderer = MKPolygonRenderer(polygon: polygon)
            let color = polygon.isStoredAnalysis ? UIColor.secondaryLabel : color(for: polygon.family)
            renderer.strokeColor = color
            renderer.fillColor = color.withAlphaComponent(polygon.isSelected ? 0.20 : 0.06)
            renderer.lineWidth = polygon.isSelected ? 4 : 2.5
            renderer.lineDashPattern = polygon.isProjected ? [8, 6] : nil
            renderer.lineJoin = .round
            return renderer
        }
        if let trail = overlay as? ObservedMotionPolyline {
            let renderer = MKPolylineRenderer(polyline: trail)
            let color = trail.isStoredAnalysis ? UIColor.secondaryLabel : color(for: trail.family)
            renderer.strokeColor = color.withAlphaComponent(0.8)
            renderer.lineWidth = trail.isSelected ? 4 : 2
            renderer.lineDashPattern = [2, 4]
            renderer.lineCap = .round
            return renderer
        }
        return nil
    }

    private static func color(for family: ObservedMotionFamily) -> UIColor {
        switch family {
        case .radarEcho: .systemTeal
        case .highCloudTop: .systemPurple
        }
    }
}
