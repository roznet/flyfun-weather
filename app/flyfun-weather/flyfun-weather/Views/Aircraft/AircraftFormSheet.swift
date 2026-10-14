import SwiftUI

/// Aircraft add / edit sheet with search-as-you-type ICAO-type suggestions.
/// Edits when the form was prepared with `prepareEdit` (PUT), adds otherwise
/// (POST). Shared by the flight form and Settings → Aircraft (#778): each
/// owner passes its own `save`, which runs the form's save and then refreshes
/// that owner's list. Labels follow the web Settings aircraft form.
struct AircraftFormSheet: View {
    @Bindable var form: AircraftFormModel
    let save: () async -> Bool
    let onDone: () -> Void

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("ICAO type, e.g. SR22, C172", text: $form.icaoType)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .accessibilityIdentifier("aircraftIcaoTypeField")

                    if form.isSearchingTypes {
                        HStack {
                            ProgressView()
                            Text("Searching aircraft types\u{2026}")
                                .foregroundStyle(.secondary)
                        }
                    }
                } header: {
                    Text("ICAO Type")
                }

                if !form.typeSuggestions.isEmpty {
                    Section {
                        ForEach(form.typeSuggestions) { type in
                            Button {
                                form.selectType(type)
                            } label: {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(type.displayName)
                                    if let category = type.category, !category.isEmpty {
                                        Text(category)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                }
                            }
                        }
                    } header: {
                        Text("Matches")
                    }
                }

                Section {
                    TextField("Tail number", text: $form.tailNumber)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                    TextField("Nickname", text: $form.nickname)
                    TextField("Cruise speed (IAS in kt)", text: $form.cruiseSpeedKt)
                        .keyboardType(.numberPad)
                    TextField("Service ceiling (ft)", text: $form.ceilingFt)
                        .keyboardType(.numberPad)
                } header: {
                    Text("Details")
                }

                Section {
                    Toggle("IFR capable", isOn: $form.isIfr)
                    Toggle("FIKI equipped", isOn: $form.isFiki)
                    Toggle("Default aircraft", isOn: $form.isDefault)
                        // Already the default: un-defaulting happens by making
                        // another aircraft the default.
                        .disabled(form.editingAircraft?.isDefault == true)
                }

                if let error = form.errorMessage {
                    Section {
                        Text(error)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle(form.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { onDone() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task {
                            if await save() {
                                onDone()
                            }
                        }
                    }
                    .disabled(!form.canSave)
                    .accessibilityIdentifier("aircraftSaveButton")
                }
            }
            // Debounce the type search. `.task(id:)` cancels the previous run on
            // each keystroke; the guard after the sleep makes that cancellation
            // actually stop the superseded search (the `try?` swallows the
            // CancellationError, so without this guard every keystroke would fire
            // a network request).
            .task(id: form.icaoType) {
                try? await Task.sleep(nanoseconds: 250_000_000)
                guard !Task.isCancelled else { return }
                await form.searchTypes()
            }
        }
    }
}
