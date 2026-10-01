#if os(macOS) || os(iOS)
import SwiftUI

// The Predict form's MSA server controls (#598), shared by PredictBar (macOS) and
// PredictCompactPanel (iOS). All state lives in PredictController; these only present it.
//
// Two settings, deliberately separate:
//   * the PICK (`selectedServer`) -- where this session's searches go. The dropdown sets it.
//   * the DEFAULT -- Python's saved server: what new sessions start on and what
//     `msa_search` uses from the console. Only the add sheet's checkbox and Edit set it.

/// A dropdown of ColabFold plus the private servers the user added, ending in "Edit…",
/// and a ＋ that adds a server.
struct MSAServerPicker: View {
    @ObservedObject var controller: PredictController
    @State private var showAdd = false
    @State private var showEdit = false

    var body: some View {
        HStack(spacing: 4) {
            Menu {
                Picker("MSA server", selection: pick) {
                    ForEach(options, id: \.self) { url in
                        Text(MSAServerPicker.title(url, isDefault: url == controller.defaultServer))
                            .tag(url)
                    }
                }
                .pickerStyle(.inline)
                .labelsHidden()
                Divider()
                Button("Edit…") { showEdit = true }
            } label: {
                Text(controller.selectedServer.map { MSAServerPicker.title($0, isDefault: false) }
                     ?? "Choose a server")
                    .lineLimit(1)
            }
            .fixedSize()
            .help("Where MSA searches send your sequences. Picking one here applies to "
                  + "this session; set the default under Edit….")
            Button { showAdd = true } label: { Image(systemName: "plus") }
                .help("Add an MSA server")
        }
        .sheet(isPresented: $showAdd) { AddMSAServerSheet(controller: controller) }
        .sheet(isPresented: $showEdit) { EditMSAServersSheet(controller: controller) }
    }

    private var options: [String] { [PredictController.publicServer] + controller.savedServers }

    private var pick: Binding<String> {
        Binding(get: { controller.selectedServer ?? "" },
                set: { controller.selectedServer = $0 })
    }

    static func title(_ url: String, isDefault: Bool) -> String {
        let name = PredictController.isPublicServer(url)
            ? "ColabFold (public)" : PredictController.hostLabel(url)
        return isDefault ? "\(name) — default" : name
    }
}

/// ＋: an address, and whether it becomes the default.
struct AddMSAServerSheet: View {
    @ObservedObject var controller: PredictController
    @Environment(\.dismiss) private var dismiss
    @State private var address = ""
    @State private var makeDefault = false
    @State private var problem: String?
    @State private var added = false

    private var isBlank: Bool { address.trimmingCharacters(in: .whitespaces).isEmpty }

    var body: some View {
        #if os(macOS)
        VStack(alignment: .leading, spacing: 12) {
            Text("Add an MSA server").font(.headline)
            Text("A ColabFold MSA server you run or trust. Sequences you search are sent "
                 + "to it.")
                .font(.callout).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            TextField("https://msa.example.org", text: $address)
                .textFieldStyle(.roundedBorder)
                .onSubmit(add)
            Toggle("Use as the default server", isOn: $makeDefault)
                .toggleStyle(.checkbox)
                .help("New sessions, and msa_search in the console, use the default.")
            if let problem {
                Text(problem).font(.callout).foregroundStyle(.red)
            }
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Add", action: add)
                    .keyboardShortcut(.defaultAction)
                    .disabled(isBlank)
            }
        }
        .padding(20)
        .frame(width: 380)
        #else
        NavigationStack {
            Form {
                Section {
                    TextField("https://msa.example.org", text: $address)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled(true)
                        .onSubmit(add)
                    Toggle("Use as the default server", isOn: $makeDefault)
                } footer: {
                    Text(problem ?? "A ColabFold MSA server you run or trust. Sequences "
                         + "you search are sent to it.")
                        .foregroundStyle(problem == nil ? Color.secondary : Color.red)
                }
            }
            .navigationTitle("Add MSA Server")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add", action: add).disabled(isBlank)
                }
            }
        }
        .presentationDetents([.medium])
        #endif
    }

    private func add() {
        // Return can reach both onSubmit and the default button; add once.
        guard !added, !isBlank else { return }
        if let why = controller.addServer(address, makeDefault: makeDefault) {
            problem = why
        } else {
            added = true
            dismiss()
        }
    }
}

/// "Edit…": choose the default, delete private servers. ColabFold cannot be deleted.
struct EditMSAServersSheet: View {
    @ObservedObject var controller: PredictController
    @Environment(\.dismiss) private var dismiss

    private var options: [String] { [PredictController.publicServer] + controller.savedServers }

    var body: some View {
        #if os(macOS)
        VStack(alignment: .leading, spacing: 12) {
            Text("MSA servers").font(.headline)
            explanation.font(.callout).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            List { rows }
                .frame(minHeight: 160)
            HStack {
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 440, height: 340)
        #else
        NavigationStack {
            List {
                Section { rows } footer: { explanation }
            }
            .navigationTitle("MSA Servers")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        #endif
    }

    private var explanation: Text {
        Text("The default is what new sessions and msa_search in the console use. "
             + "Picking a server in the Predict form applies to this session only.")
    }

    @ViewBuilder private var rows: some View {
        ForEach(options, id: \.self) { url in
            let isDefault = url == controller.defaultServer
            HStack(spacing: 10) {
                Button { controller.setDefaultServer(url) } label: {
                    Image(systemName: isDefault ? "largecircle.fill.circle" : "circle")
                        .foregroundStyle(isDefault ? Color.accentColor : Color.secondary)
                }
                .buttonStyle(.borderless)
                .help(isDefault ? "The default server" : "Make this the default")
                VStack(alignment: .leading, spacing: 1) {
                    Text(MSAServerPicker.title(url, isDefault: false))
                    Text(url).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if isDefault {
                    Text("Default").font(.caption).foregroundStyle(.secondary)
                }
                if !PredictController.isPublicServer(url) {
                    Button(role: .destructive) { controller.removeServer(url) } label: {
                        Image(systemName: "trash")
                    }
                    .buttonStyle(.borderless)
                    .help("Delete this server")
                }
            }
            .padding(.vertical, 2)
        }
    }
}

/// Run on ColabFold asks first: the sequences leave for a public server (#598).
struct PublicMSAWarning: ViewModifier {
    @ObservedObject var controller: PredictController
    @State private var dontShowAgain = false

    func body(content: Content) -> some View {
        content
            .confirmationDialog("Send your sequences to a public server?",
                                isPresented: presented, titleVisibility: .visible) {
                Button("Send") {
                    controller.confirmPublicWarning(dontShowAgain: dontShowAgain)
                }
                #if os(iOS)
                Button("Send, Don't Ask Again") {
                    controller.confirmPublicWarning(dontShowAgain: true)
                }
                #endif
                Button("Cancel", role: .cancel) { controller.cancelPublicWarning() }
            } message: {
                Text("The MSA search sends your sequences to api.colabfold.com, a public "
                     + "server run by a third party. Don't send unpublished or "
                     + "confidential sequences there; add your own server with ＋ "
                     + "instead.")
            }
            #if os(macOS)
            .dialogSuppressionToggle("Don't show this again", isSuppressed: $dontShowAgain)
            #endif
    }

    /// Dismissing the dialog any other way (Esc, clicking outside) is a Cancel.
    private var presented: Binding<Bool> {
        Binding(get: { controller.pendingPublicWarning },
                set: { shown in
                    if !shown, controller.pendingPublicWarning {
                        controller.cancelPublicWarning()
                    }
                })
    }
}
#endif
