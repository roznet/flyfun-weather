import SwiftUI
import UIKit

/// Pinch / double-tap zoom over arbitrary SwiftUI content (#799), backed by a
/// `UIScrollView` — SwiftUI's own gestures don't give scroll-view-grade
/// zooming with momentum and edge bounce.
///
/// The content is laid out once at `contentSize` (points) and *scaled*, so
/// anything drawn on top of an image — the chart route overlay — zooms with
/// it. Lay content out at the image's full pixel size: zoomed out it fits the
/// screen, and up to 1x it stays sharp.
struct ZoomableScrollView<Content: View>: UIViewRepresentable {
    let contentSize: CGSize
    /// Upper zoom bound, relative to the content's laid-out size.
    var maximumZoom: CGFloat = 3
    @ViewBuilder let content: () -> Content

    func makeCoordinator() -> Coordinator {
        let hosting = UIHostingController(rootView: content())
        // Scaled content must not pick up the screen's safe-area insets, or
        // the image would be laid out offset inside its own frame.
        hosting.safeAreaRegions = []
        return Coordinator(hosting: hosting)
    }

    func makeUIView(context: Context) -> FittingScrollView {
        let hosted = context.coordinator.hosting.view!
        hosted.backgroundColor = .clear
        hosted.frame = CGRect(origin: .zero, size: contentSize)
        return FittingScrollView(zoomView: hosted, maximumZoom: maximumZoom)
    }

    func updateUIView(_ scrollView: FittingScrollView, context: Context) {
        context.coordinator.hosting.rootView = content()
    }

    /// Holds the hosting controller for the scroll view's lifetime. Kept free of
    /// UIKit delegate duty: the (non-generic) `FittingScrollView` is its own
    /// delegate, so no Objective-C exposure is needed on this generic type.
    final class Coordinator {
        let hosting: UIHostingController<Content>

        init(hosting: UIHostingController<Content>) {
            self.hosting = hosting
        }
    }
}

/// Scroll view whose minimum zoom fits the content to its bounds, re-fitting
/// on rotation / resize (iPad split view) while the user hasn't zoomed in.
/// Its own delegate; double-tap toggles between fit and 3x around the tap.
final class FittingScrollView: UIScrollView, UIScrollViewDelegate {
    private let zoomView: UIView
    private let maximumZoom: CGFloat
    private var lastBoundsSize: CGSize = .zero

    init(zoomView: UIView, maximumZoom: CGFloat) {
        self.zoomView = zoomView
        self.maximumZoom = maximumZoom
        super.init(frame: .zero)
        delegate = self
        bouncesZoom = true
        showsVerticalScrollIndicator = false
        showsHorizontalScrollIndicator = false
        contentInsetAdjustmentBehavior = .never
        backgroundColor = .clear
        addSubview(zoomView)
        contentSize = zoomView.bounds.size

        let doubleTap = UITapGestureRecognizer(target: self, action: #selector(doubleTapped(_:)))
        doubleTap.numberOfTapsRequired = 2
        addGestureRecognizer(doubleTap)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) is not supported")
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        guard bounds.size != lastBoundsSize, bounds.width > 0, bounds.height > 0 else { return }
        let atMinimum = lastBoundsSize == .zero || zoomScale <= minimumZoomScale + 0.0001
        lastBoundsSize = bounds.size

        let natural = zoomView.bounds.size
        guard natural.width > 0, natural.height > 0 else { return }
        let fit = min(bounds.width / natural.width, bounds.height / natural.height)
        minimumZoomScale = fit
        maximumZoomScale = max(fit, maximumZoom)
        if atMinimum { zoomScale = fit }
        centerContent()
    }

    func viewForZooming(in scrollView: UIScrollView) -> UIView? {
        zoomView
    }

    func scrollViewDidZoom(_ scrollView: UIScrollView) {
        centerContent()
    }

    /// Keep content centred while it is smaller than the viewport.
    private func centerContent() {
        let dx = max(0, (bounds.width - contentSize.width) / 2)
        let dy = max(0, (bounds.height - contentSize.height) / 2)
        contentInset = UIEdgeInsets(top: dy, left: dx, bottom: dy, right: dx)
    }

    @objc private func doubleTapped(_ gesture: UITapGestureRecognizer) {
        if zoomScale > minimumZoomScale * 1.05 {
            setZoomScale(minimumZoomScale, animated: true)
        } else {
            let target = min(maximumZoomScale, minimumZoomScale * 3)
            let point = gesture.location(in: zoomView)
            let size = CGSize(width: bounds.width / target, height: bounds.height / target)
            zoom(to: CGRect(x: point.x - size.width / 2, y: point.y - size.height / 2,
                            width: size.width, height: size.height), animated: true)
        }
    }
}

/// Full-screen viewer chrome around `ZoomableScrollView`: black backdrop, a
/// close button and an optional caption.
struct ZoomViewerScreen<Content: View>: View {
    let title: String
    let caption: String?
    let contentSize: CGSize
    @ViewBuilder let content: () -> Content
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ZoomableScrollView(contentSize: contentSize, content: content)
                .ignoresSafeArea(edges: .bottom)
                .background(Color.black)
                .safeAreaInset(edge: .bottom) {
                    if let caption, !caption.isEmpty {
                        Text(caption)
                            .font(.caption)
                            .foregroundStyle(.white.opacity(0.85))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(Theme.spacingS)
                            .background(.black.opacity(0.6))
                    }
                }
                .navigationTitle(title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("Done") { dismiss() }
                    }
                }
        }
    }
}
