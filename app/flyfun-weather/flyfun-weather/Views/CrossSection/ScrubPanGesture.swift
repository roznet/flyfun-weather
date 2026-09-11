import SwiftUI
import UIKit

/// The cross-section's scrub gesture when the chart sits in a vertical scroll
/// view (#605).
///
/// The old `DragGesture(minimumDistance: 0)` claimed every touch the instant it
/// landed, so on a chart filling much of the screen a vertical drag scrubbed
/// instead of scrolling and there was no way past it. This pan decides by
/// DIRECTION at the moment it would begin:
///  - a sideways drag scrubs, and then tracks x and y freely;
///  - a finger held still for `holdToScrub` first scrubs in any direction — for
///    reading altitude straight up a column;
///  - anything else fails at once, and the scroll view, which is made to wait
///    for exactly that failure, scrolls as usual.
/// Taps are a separate spatial tap gesture, which never competes with scrolling.
struct ScrubPanGesture: UIGestureRecognizerRepresentable {
    var holdToScrub: TimeInterval = 0.25
    var onChanged: (CGPoint) -> Void

    func makeCoordinator(converter: CoordinateSpaceConverter) -> Coordinator {
        Coordinator(holdToScrub: holdToScrub)
    }

    func makeUIGestureRecognizer(context: Context) -> UIPanGestureRecognizer {
        let pan = UIPanGestureRecognizer()
        pan.maximumNumberOfTouches = 1
        pan.delegate = context.coordinator
        return pan
    }

    func handleUIGestureRecognizerAction(_ recognizer: UIPanGestureRecognizer, context: Context) {
        switch recognizer.state {
        case .began, .changed:
            onChanged(context.converter.localLocation)
        default:
            break
        }
    }

    final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        let holdToScrub: TimeInterval
        /// When the finger landed, in `systemUptime` seconds (a touch's clock).
        private var touchDownAt: TimeInterval = 0

        init(holdToScrub: TimeInterval) {
            self.holdToScrub = holdToScrub
        }

        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldReceive touch: UITouch) -> Bool {
            touchDownAt = touch.timestamp
            return true
        }

        func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
            guard let pan = gestureRecognizer as? UIPanGestureRecognizer else { return true }
            let t = pan.translation(in: pan.view)
            if abs(t.x) > abs(t.y) { return true }
            return ProcessInfo.processInfo.systemUptime - touchDownAt >= holdToScrub
        }

        /// The enclosing scroll view's pan waits for this one to fail, so a
        /// sideways scrub never drags the page along with it.
        func gestureRecognizer(
            _ gestureRecognizer: UIGestureRecognizer,
            shouldBeRequiredToFailBy otherGestureRecognizer: UIGestureRecognizer
        ) -> Bool {
            otherGestureRecognizer is UIPanGestureRecognizer && otherGestureRecognizer.view is UIScrollView
        }
    }
}
