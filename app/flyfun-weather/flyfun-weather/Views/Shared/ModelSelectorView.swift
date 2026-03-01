import SwiftUI

/// Picker for weather model selection.
struct ModelSelectorView: View {
    @Binding var selectedModel: String
    let models: [String]

    var body: some View {
        Menu {
            ForEach(models, id: \.self) { model in
                Button {
                    selectedModel = model
                } label: {
                    HStack {
                        Text(model.uppercased())
                        if model == selectedModel {
                            Image(systemName: "checkmark")
                        }
                    }
                }
            }
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "cloud.sun")
                    .font(.caption)
                Text(selectedModel.uppercased())
                    .font(.caption.bold())
                Image(systemName: "chevron.down")
                    .font(.caption2)
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(Color.accentColor.opacity(0.1))
            .clipShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}
