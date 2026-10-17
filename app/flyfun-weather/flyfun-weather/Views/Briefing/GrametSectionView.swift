import PDFKit
import SwiftUI
import UIKit

/// GRAMET cross-section on the Discussion tab (#799). Shown as the PDF itself
/// (PDFKit: vector, so zoom stays sharp) — the PNG the web shows is a raster
/// of the same page. Packs stored before the PDF switch serve a PNG instead,
/// shown as an image. Tap for full screen. Cached in the pack's directory by
/// the repository, so a downloaded pack shows it offline.
struct GrametSectionView: View {
    let viewModel: BriefingViewModel
    let hasGramet: Bool

    @State private var state: LoadState = .loading
    @State private var showFullScreen = false

    enum LoadState {
        case loading
        case pdf(PDFDocument)
        case image(UIImage)
        case failed
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.spacingS) {
            Text("GRAMET")
                .font(.headline)
                .foregroundStyle(Theme.text)

            if !hasGramet {
                Text("GRAMET not available for this briefing.")
                    .font(.callout)
                    .foregroundStyle(Theme.textMuted)
            } else {
                content
                Text("GRAMET cross-section from Autorouter")
                    .font(.caption)
                    .foregroundStyle(Theme.textMuted)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Theme.cardPadding)
        .task(id: viewModel.pack?.fetchTimestamp) {
            guard hasGramet else { return }
            await load()
        }
        .fullScreenCover(isPresented: $showFullScreen) {
            fullScreen
        }
    }

    @ViewBuilder
    private var content: some View {
        switch state {
        case .loading:
            ProgressView()
                .frame(maxWidth: .infinity, minHeight: 160)
        case .pdf(let document):
            PDFDocumentView(document: document, interactive: false)
                .aspectRatio(Self.aspectRatio(of: document), contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .contentShape(Rectangle())
                .onTapGesture { showFullScreen = true }
                .accessibilityLabel("GRAMET cross-section")
                .accessibilityHint("Opens the GRAMET full screen")
                .accessibilityAddTraits(.isButton)
        case .image(let image):
            Image(uiImage: image)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .contentShape(Rectangle())
                .onTapGesture { showFullScreen = true }
                .accessibilityLabel("GRAMET cross-section")
                .accessibilityAddTraits(.isButton)
        case .failed:
            Label("GRAMET couldn't be loaded — it isn't saved on this device. Connect to load it.",
                  systemImage: "wifi.slash")
                .font(.callout)
                .foregroundStyle(Theme.textMuted)
                .frame(maxWidth: .infinity, minHeight: 100)
                .background(Theme.surface, in: RoundedRectangle(cornerRadius: 6))
        }
    }

    @ViewBuilder
    private var fullScreen: some View {
        switch state {
        case .pdf(let document):
            NavigationStack {
                PDFDocumentView(document: document, interactive: true)
                    .ignoresSafeArea(edges: .bottom)
                    .navigationTitle("GRAMET")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .topBarTrailing) {
                            Button("Done") { showFullScreen = false }
                        }
                    }
            }
        case .image(let image):
            ZoomViewerScreen(title: "GRAMET", caption: "GRAMET cross-section from Autorouter",
                             contentSize: image.size) {
                Image(uiImage: image).resizable()
            }
        default:
            EmptyView()
        }
    }

    private func load() async {
        state = .loading
        do {
            let data = try await viewModel.fetchGrametDocument()
            if data.starts(with: Array("%PDF".utf8)), let document = PDFDocument(data: data) {
                state = .pdf(document)
            } else if let image = UIImage(data: data) {
                state = .image(image)
            } else {
                state = .failed
            }
        } catch {
            guard !Task.isCancelled else { return }
            state = .failed
        }
    }

    /// Width / height of the first page (GRAMET is a single landscape page).
    private static func aspectRatio(of document: PDFDocument) -> CGFloat {
        guard let bounds = document.page(at: 0)?.bounds(for: .mediaBox),
              bounds.width > 0, bounds.height > 0 else { return 1.6 }
        return bounds.width / bounds.height
    }
}

/// PDFKit's `PDFView` in SwiftUI. Non-interactive inline (the tab's scroll
/// view keeps the gestures), pinch-zoomable full screen.
struct PDFDocumentView: UIViewRepresentable {
    let document: PDFDocument
    let interactive: Bool

    func makeUIView(context: Context) -> PDFView {
        let view = PDFView()
        view.autoScales = true
        view.displayMode = .singlePage
        view.displaysPageBreaks = false
        view.backgroundColor = .clear
        view.isUserInteractionEnabled = interactive
        view.document = document
        return view
    }

    func updateUIView(_ view: PDFView, context: Context) {
        if view.document !== document {
            view.document = document
        }
        view.isUserInteractionEnabled = interactive
    }
}
