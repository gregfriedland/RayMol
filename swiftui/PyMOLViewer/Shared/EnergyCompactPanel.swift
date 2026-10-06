#if os(macOS)
import SwiftUI
import AppKit

@MainActor
final class EnergyViewportController: ObservableObject {
    @Published var expanded = false
    @Published var receptor = "" { didSet { if receptor != oldValue { invalidateReview(clearLigand: false) } } }
    @Published var ligand = "" { didSet { if ligand != oldValue { invalidateReview(); loadEmbeddedChemistry() } } }
    @Published var moleculeNames: [String] = []
    @Published var selectionNames: [String] = []
    @Published var sdf = "" { didSet { if sdf != oldValue { if !restoringInputs { embeddedSDF = nil; inputError = nil }; invalidateReview() } } }
    @Published var ligandFormat = "SMILES" { didSet { if ligandFormat != oldValue { invalidateReview() } } }
    @Published var smiles = "" { didSet { if smiles != oldValue { if !restoringInputs { embeddedSDF = nil; inputError = nil }; invalidateReview() } } }
    @Published private(set) var embeddedSDF: String?
    @Published private(set) var inputError: String?
    @Published var reviewing = false
    @Published var state = 1 { didSet { if state != oldValue { invalidateReview(); loadEmbeddedChemistry() } } }
    @Published var ph = 7.0 { didSet { if ph != oldValue { invalidateReview(clearLigand: false) } } }
    @Published var proteinOverrides: [String: String] = [:] { didSet { if proteinOverrides != oldValue { invalidateReview(clearLigand: false) } } }
    @Published var residueLabels: [String: String] = [:]
    @Published var protonationRows: [String] = []
    @Published var templates = "{}" { didSet { if templates != oldValue { confirmed = false } } }
    @Published var mapping = "" { didSet { if mapping != oldValue { invalidateReview() } } }
    @Published var confirmed = false
    @Published var reviewed = false
    @Published var exclusions: [String] = []
    @Published var message = "No analysis"
    @Published var running = false
    @Published var mode = "Interactions"
    @Published var channel = "total"
    @Published var threshold = 0.1
    @Published var scale = 10.0
    @Published var pocket = 8.0
    @Published var components: [String: String] = [:]
    @Published var contactCounts: [String: [String: Int]] = [:]
    @Published var states: [String: String] = [:]
    @Published var summaries: [String: [String: Any]] = [:]
    @Published var readout: String?
    @Published var pinned = false
    @Published var visible = true
    @Published var groupSelection = "sele"
    @Published var queryReadout = ""
    private let worker = EnergyAnalysisController()
    private weak var engine: PyMOLEngine?
    private var poll: Task<Void, Never>?
    private var scenePoll: Task<Void, Never>?
    private var runIdentity = UUID()
    private var analysisID: String?
    private var sceneEpoch: String?
    private var reviewedHash: String?
    private var preparedSDF: String?
    private var preparedMapping: [Int]?
    private var inputRevision = 0
    private var inputMessage = false
    private var inputEpoch: String?
    private var loadedLigand: String?
    private var restoringInputs = false
    private var analyzeAfterReview = false
    private var activeAnalysisID: String?
    private let directory: URL

    init(engine: PyMOLEngine) {
        self.engine = engine
        directory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/RayMol Energy Local/analyses", isDirectory: true)
        scenePoll = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 750_000_000)
                guard let self else { return }
                self.refreshScene()
            }
        }
    }

    private var display: [String: Any] {
        ["threshold": threshold, "scale": scale, "pocket": pocket, "channel": channel]
    }

    private func invalidateReview(clearLigand: Bool = true) {
        reviewed = false; confirmed = false; reviewedHash = nil
        if clearLigand { preparedSDF = nil; preparedMapping = nil }
        inputRevision += 1
    }

    var reviewIssue: String? {
        if running { return "Analysis is running" }
        if reviewing { return "Preparing SMILES chemistry" }
        if engine?.isReady != true { return "Molecular engine is not ready" }
        if moleculeNames.isEmpty && selectionNames.isEmpty { return "No molecules loaded" }
        let names = moleculeNames + selectionNames
        if receptor.isEmpty { return "Select a receptor" }
        if !names.contains(receptor) { return "Receptor is no longer available" }
        if ligand.isEmpty { return "Select a ligand" }
        if !names.contains(ligand) { return "Ligand is no longer available" }
        if receptor == ligand { return "Receptor and ligand must differ" }
        if let inputError { return inputError }
        if !ph.isFinite || !(0...14).contains(ph) { return "Preparation pH must be between 0 and 14" }
        if ligandFormat == "SMILES" {
            if smiles.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return "Ligand SMILES required" }
        } else {
            if embeddedSDF == nil {
                if sdf.isEmpty { return "Ligand SDF required" }
                if !FileManager.default.isReadableFile(atPath: sdf) { return "Ligand SDF is no longer available" }
            }
        }
        return nil
    }

    var analyzeIssue: String? {
        if let issue = reviewIssue { return issue }
        return nil
    }

    func refreshInputs() {
        guard engine?.isReady == true else { return }
        do {
            guard let values = try scene("input_options", [:]) as? [String: [String]],
                  let molecules = values["molecules"], let selections = values["selections"] else { throw EnergyProtocolError.invalidMessage }
            if moleculeNames != molecules { moleculeNames = molecules }
            if selectionNames != selections { selectionNames = selections }
            if let epoch = values["session_epoch"]?.first, inputEpoch != epoch {
                let initial = inputEpoch == nil
                inputEpoch = epoch; loadedLigand = nil
                if !initial {
                    if running || reviewing { cancel() }
                    clearAnalysis(); message = "No analysis"
                }
                restoringInputs = true
                defer { restoringInputs = false }
                if let stored = try scene("session_inputs", [:]) as? [String: Any],
                   let savedReceptor = stored["receptor"] as? String, let savedLigand = stored["ligand"] as? String,
                   let savedState = stored["state"] as? Int, let savedPH = stored["pH"] as? Double {
                    receptor = savedReceptor; ligand = savedLigand; state = savedState; ph = savedPH
                    proteinOverrides = stored["overrides"] as? [String: String] ?? [:]
                    applyProtonation(stored["protonation"] as? [String: Any])
                } else if !initial {
                    receptor = ""; ligand = ""; state = 1; ph = 7; proteinOverrides = [:]
                    residueLabels = [:]; protonationRows = []
                }
                loadedLigand = nil; loadEmbeddedChemistry()
            }
            let names = molecules + selections
            if (!receptor.isEmpty && !names.contains(receptor)) || (!ligand.isEmpty && !names.contains(ligand)) { invalidateReview() }
            loadEmbeddedChemistry()
        } catch { inputError = error.localizedDescription; message = error.localizedDescription; invalidateReview(clearLigand: false) }
    }

    private func loadEmbeddedChemistry() {
        guard !restoringInputs, engine?.isReady == true, !ligand.isEmpty else { return }
        let key = "\(inputEpoch ?? "")/\(ligand)/\(state)"
        guard loadedLigand != key else { return }
        loadedLigand = key
        restoringInputs = true
        defer { restoringInputs = false }
        smiles = ""; sdf = ""; mapping = ""; embeddedSDF = nil; inputError = nil
        do {
            guard let chemistry = try scene("ligand_chemistry", ["ligand": ligand, "state": state]) as? [String: Any] else { return }
            smiles = chemistry["smiles"] as? String ?? ""
            embeddedSDF = chemistry["sdf"] as? String
            if let indices = chemistry["mapping"] as? [Int] {
                mapping = String(data: try JSONSerialization.data(withJSONObject: indices), encoding: .utf8)!
            }
            ligandFormat = embeddedSDF == nil ? "SMILES" : "SDF"
            message = "Ligand chemistry loaded from PSE"
        } catch { inputError = error.localizedDescription; message = error.localizedDescription }
    }

    private func applyProtonation(_ values: [String: Any]?) {
        guard let rows = values?["assignments"] as? [[String: Any]] else { return }
        protonationRows = rows.compactMap { row in
            guard let label = row["label"] as? String, let template = row["template"] as? String, let origin = row["origin"] as? String else { return nil }
            return "\(label): \(template) (\(origin))"
        }
    }

    func overrideChoices(_ identifier: String) -> [String] {
        let label = residueLabels[identifier] ?? ""
        if label.contains("/HIS") || label.contains("/HID") || label.contains("/HIE") || label.contains("/HIP") { return ["HID", "HIE", "HIP"] }
        if label.contains("/ASP") || label.contains("/ASH") { return ["ASP", "ASH"] }
        if label.contains("/GLU") || label.contains("/GLH") { return ["GLU", "GLH"] }
        if label.contains("/LYS") || label.contains("/LYN") { return ["LYS", "LYN"] }
        if label.contains("/CYS") || label.contains("/CYX") || label.contains("/CYM") { return ["CYS", "CYX", "CYM"] }
        return []
    }

    func recordAcceptanceStatus() {
        #if DEBUG
        if let report = ProcessInfo.processInfo.environment["RAYMOL_ENERGY_ACCEPTANCE_STATUS"],
           let data = try? JSONSerialization.data(withJSONObject: ["message": message, "reviewed": reviewed,
                                                                  "running": running, "confirmed": confirmed, "states": states]) {
            try? data.write(to: URL(fileURLWithPath: report), options: .atomic)
        }
        #endif
    }

    private func scene(_ operation: String, _ arguments: [String: Any]) throws -> Any? {
        guard let engine, engine.isReady else { throw EnergyProtocolError.unavailable }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let data = try JSONSerialization.data(withJSONObject: ["operation": operation, "arguments": arguments])
        let report = directory.appendingPathComponent("ui-\(operation)-result.json")
        if FileManager.default.fileExists(atPath: report.path) { try FileManager.default.removeItem(at: report) }
        let path = try JSONSerialization.data(withJSONObject: [report.path])
        let encodedPath = path.base64EncodedString()
        engine.runPython("import base64 as _eb, json as _ej\nfrom pymol.energy_view import EnergyView as _ev\n"
                         + "_ep=_ej.loads(_eb.b64decode('\(data.base64EncodedString())'))\n"
                         + "_ev.dispatch(_ep['operation'],_ep['arguments'],_ej.loads(_eb.b64decode('\(encodedPath)'))[0])")
        guard let result = try JSONSerialization.jsonObject(with: Data(contentsOf: report)) as? [String: Any],
              result["status"] as? String == "ready" else {
            let result = try? JSONSerialization.jsonObject(with: Data(contentsOf: report)) as? [String: Any]
            throw NSError(domain: "RayMolEnergy", code: 1, userInfo: [NSLocalizedDescriptionKey: result?["message"] as? String ?? "Energy scene operation failed"])
        }
        return result["value"]
    }

    private func inputArguments(commit: Bool) throws -> [String: Any] {
        var values: [String: Any] = ["receptor": receptor, "ligand": ligand, "sdf_path": sdf, "state": state,
                                    "output": directory.path, "confirmed": commit,
                                    "preparation_ph": ph, "protein_overrides": proteinOverrides]
        if !smiles.isEmpty { values["smiles"] = smiles }
        if ligandFormat == "SMILES" {
            guard let preparedSDF, let preparedMapping else { throw EnergyProtocolError.invalidMessage }
            values["sdf_text"] = preparedSDF
            values["mapping"] = preparedMapping
        } else if let embeddedSDF {
            values["sdf_text"] = embeddedSDF
        }
        if commit {
            guard confirmed, reviewed else { throw EnergyProtocolError.invalidMessage }
            values["templates"] = try JSONSerialization.jsonObject(with: Data(templates.utf8))
            guard let reviewedHash else { throw EnergyProtocolError.invalidMessage }
            values["reviewed_hash"] = reviewedHash
        }
        if ligandFormat == "SDF", !mapping.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            values["mapping"] = try JSONSerialization.jsonObject(with: Data(mapping.utf8))
        }
        return values
    }

    func chooseSDF() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.allowedFileTypes = ["sdf", "mol"]
        if panel.runModal() == .OK, let url = panel.url { sdf = url.path; reviewed = false; confirmed = false }
    }

    func review() {
        inputMessage = true
        refreshInputs()
        if let issue = reviewIssue { message = issue; invalidateReview(clearLigand: false); analyzeAfterReview = false; return }
        invalidateReview(clearLigand: false)
        if ligandFormat == "SDF" { finishReview(); return }
        do {
            guard var input = try scene("ligand_input", ["ligand": ligand, "state": state]) as? [String: Any] else { throw EnergyProtocolError.invalidMessage }
            input["smiles"] = smiles
            let payload = try JSONSerialization.data(withJSONObject: input, options: [.sortedKeys])
            let revision = inputRevision
            let identity = UUID()
            runIdentity = identity; reviewing = true; message = "Matching SMILES to ligand pose"
            poll = Task {
                defer { if runIdentity == identity { reviewing = false; recordAcceptanceStatus() } }
                do {
                    try await startWorker()
                    guard runIdentity == identity, !Task.isCancelled else { return }
                    try await worker.submitLatest(operation: "ligand_input", payload: payload)
                    let deadline = Date().addingTimeInterval(45)
                    while !Task.isCancelled {
                        guard runIdentity == identity else { return }
                        if revision != inputRevision { throw NSError(domain: "RayMolEnergy", code: 3, userInfo: [NSLocalizedDescriptionKey: "Inputs changed; review chemistry again"]) }
                        if let failure = await worker.failure { throw NSError(domain: "RayMolEnergy", code: 2, userInfo: [NSLocalizedDescriptionKey: failure]) }
                        if let terminal = await worker.published {
                            guard runIdentity == identity, !Task.isCancelled, revision == inputRevision,
                                  let value = try JSONSerialization.jsonObject(with: terminal.payload) as? [String: Any],
                                  let sdf = value["sdf"] as? String, let mapping = value["mapping"] as? [Int],
                                  var current = try scene("ligand_input", ["ligand": ligand, "state": state]) as? [String: Any] else { throw EnergyProtocolError.invalidMessage }
                            current["smiles"] = smiles
                            guard try JSONSerialization.data(withJSONObject: current, options: [.sortedKeys]) == payload else {
                                throw NSError(domain: "RayMolEnergy", code: 3, userInfo: [NSLocalizedDescriptionKey: "Ligand pose changed; review chemistry again"])
                            }
                            preparedSDF = sdf; preparedMapping = mapping
                            finishReview()
                            return
                        }
                        if Date() >= deadline { throw NSError(domain: "RayMolEnergy", code: 4, userInfo: [NSLocalizedDescriptionKey: "SMILES preparation exceeded 45 seconds"]) }
                        try await Task.sleep(nanoseconds: 100_000_000)
                    }
                } catch {
                    if runIdentity == identity { message = error.localizedDescription; invalidateReview(clearLigand: false); analyzeAfterReview = false }
                    if runIdentity == identity { try? await worker.cancel() }
                }
            }
        } catch { message = error.localizedDescription; invalidateReview(clearLigand: false); analyzeAfterReview = false }
    }

    private func finishReview() {
        do {
            guard let value = try scene("input", inputArguments(commit: false)) as? [String: Any],
                  let choices = value["templates"] as? [String: String],
                  let hash = value["reviewed_hash"] as? String else { throw EnergyProtocolError.invalidMessage }
            templates = String(data: try JSONSerialization.data(withJSONObject: choices, options: [.sortedKeys, .prettyPrinted]), encoding: .utf8)!
            residueLabels = value["labels"] as? [String: String] ?? [:]
            exclusions = value["exclusions"] as? [String] ?? []
            reviewedHash = hash; reviewed = true
            confirmed = exclusions.isEmpty
            message = "Review \(choices.count) residue states; \(exclusions.count) excluded groups"
            if analyzeAfterReview {
                analyzeAfterReview = false
                reviewing = false
                if confirmed { startAnalysis() }
                else { message = "Confirm the listed excluded groups, then Analyze" }
            }
        } catch { message = error.localizedDescription; invalidateReview(clearLigand: false); analyzeAfterReview = false }
    }

    private func startWorker() async throws {
        guard let resources = Bundle.main.resourceURL else { throw EnergyProtocolError.unavailable }
        var environment = ProcessInfo.processInfo.environment
        environment.removeValue(forKey: "PYTHONPATH"); environment.removeValue(forKey: "PYTHONHOME")
        environment["PYTHONNOUSERSITE"] = "1"; environment["OMP_NUM_THREADS"] = "4"
        environment["OPENBLAS_NUM_THREADS"] = "1"; environment["MKL_NUM_THREADS"] = "1"
        environment["VECLIB_MAXIMUM_THREADS"] = "1"
        environment["OPENMM_CPU_THREADS"] = String(ProcessInfo.processInfo.activeProcessorCount)
        var arguments = ["-B", "-m", "raymol_energy.worker"]
        #if DEBUG
        if let script = environment["RAYMOL_ENERGY_TEST_WORKER_SCRIPT"] { arguments = ["-B", script] }
        #endif
        try await worker.ensureRunning(executable: resources.appendingPathComponent("energy-helper/bin/python"),
                                       arguments: arguments, environment: environment)
    }

    func analyze() {
        if let issue = analyzeIssue { message = issue; return }
        if reviewed && confirmed { startAnalysis(); return }
        analyzeAfterReview = true
        review()
    }

    private func startAnalysis() {
        do {
            guard let value = try scene("input", inputArguments(commit: true)) as? [String: Any],
                  let id = value["analysis_id"] as? String,
                  let root = value["directory"] as? String,
                  let snapshot = value["snapshot"] as? [String: Any] else { throw EnergyProtocolError.invalidMessage }
            let payload = try JSONSerialization.data(withJSONObject: ["directory": root, "snapshot": snapshot])
            activeAnalysisID = id; sceneEpoch = value["epoch"] as? String
            inputMessage = false
            running = true
            poll?.cancel()
            let identity = UUID()
            runIdentity = identity
            poll = Task {
                var delivered = 0
                defer { if runIdentity == identity { running = false; recordAcceptanceStatus() } }
                do {
                    guard runIdentity == identity, !Task.isCancelled else { return }
                    try await startWorker()
                    guard runIdentity == identity, !Task.isCancelled else { return }
                    try await worker.submitLatest(operation: "analyze", payload: payload)
                    guard runIdentity == identity, !Task.isCancelled else { return }
                    message = "Preparing hydrogens"
                    while !Task.isCancelled {
                        let events = await worker.stageEvents
                        guard runIdentity == identity, !Task.isCancelled else { return }
                        for event in events.dropFirst(delivered) {
                            guard let data = try JSONSerialization.jsonObject(with: event.payload) as? [String: Any],
                                  let stage = data["stage"] as? String, let status = data["status"] as? String else { throw EnergyProtocolError.invalidMessage }
                            if activeAnalysisID == nil || analysisID == nil {
                                states[stage] = status
                                if status == "ready" { summaries[stage] = data["summary"] as? [String: Any] }
                            }
                            if let result = try scene("stage", ["event": data, "display": display]) as? [String: Any],
                               let names = result["components"] as? [String: String] { components = names }
                            if stage == "prepared", status == "ready" {
                                if let previous = analysisID, previous != id { _ = try scene("remove", ["analysis_id": previous]) }
                                analysisID = id; activeAnalysisID = nil
                                states = [stage: status]; summaries = [stage: data["summary"] as? [String: Any] ?? [:]]
                                if let preparation = try scene("session_inputs", [:]) as? [String: Any] { applyProtonation(preparation["protonation"] as? [String: Any]) }
                            }
                            if let reason = data["message"] as? String { message = "\(stage): \(reason)" }
                            else { message = "\(stage.replacingOccurrences(of: "_", with: " ")): \(status)" }
                            delivered += 1
                        }
                        let failure = await worker.failure
                        let terminal = await worker.published
                        guard runIdentity == identity, !Task.isCancelled else { return }
                        if let failure { throw NSError(domain: "RayMolEnergy", code: 2, userInfo: [NSLocalizedDescriptionKey: failure]) }
                        if let terminal {
                            // Scoring can finish while the main actor draws an earlier event.
                            let count = await worker.stageEvents.count
                            guard runIdentity == identity, !Task.isCancelled else { return }
                            if count != delivered { continue }
                            let data = try JSONSerialization.jsonObject(with: terminal.payload) as? [String: Any]
                            message = data?["complete"] as? Bool == true ? "Ready | ff14SB/Sage | OBC2+ACE" : "Incomplete: inspect component states"
                            break
                        }
                        try await Task.sleep(nanoseconds: 100_000_000)
                    }
                } catch { if runIdentity == identity { interrupt(error.localizedDescription) } }
            }
        } catch { inputMessage = true; message = error.localizedDescription; analyzeAfterReview = false; invalidateReview(clearLigand: false) }
    }

    func cancel() {
        let wasReviewing = reviewing
        analyzeAfterReview = false
        runIdentity = UUID()
        let identity = runIdentity
        poll?.cancel(); poll = nil; running = false; reviewing = false
        if wasReviewing { invalidateReview(clearLigand: false); message = "Chemistry preparation cancelled; inputs retained" }
        else { interrupt("Cancelled; accepted results are incomplete") }
        recordAcceptanceStatus()
        Task {
            guard runIdentity == identity else { return }
            do { try await worker.cancel() } catch { if runIdentity == identity { message = error.localizedDescription } }
        }
    }

    private func interrupt(_ reason: String) {
        message = reason
        if let analysisID = activeAnalysisID ?? analysisID, let sceneEpoch {
            do {
                if let values = try scene("interrupt", ["analysis_id": analysisID, "epoch": sceneEpoch, "reason": reason]) as? [String: String] {
                    if activeAnalysisID == nil {
                        states = values
                        summaries = summaries.filter { values[$0.key] == "ready" }
                    }
                }
            } catch { message = "\(reason); scene status update failed: \(error.localizedDescription)" }
        }
        if let pending = activeAnalysisID {
            _ = try? scene("remove", ["analysis_id": pending])
            activeAnalysisID = nil
        }
    }

    func redraw() {
        guard let analysisID else { return }
        do { _ = try scene("display", ["analysis_id": analysisID].merging(display, uniquingKeysWith: { _, new in new })) }
        catch { message = error.localizedDescription }
    }

    private func refreshScene() {
        guard engine?.isReady == true else { return }
        refreshInputs()
        do {
            guard let entries = try scene("health", [:]) as? [String: [String: Any]] else { return }
            if let old = analysisID, entries[old] == nil {
                clearAnalysis()
                message = "No analysis in this session"
            }
            if analysisID == nil, let restored = entries.keys.sorted().last(where: { entries[$0]?["restored"] as? Bool == true && !(entries[$0]?["components"] as? [String: String] ?? [:]).isEmpty }) { analysisID = restored }
            guard let analysisID, let entry = entries[analysisID] else { return }
            if let epoch = entry["session_epoch"] as? String, epoch != sceneEpoch {
                if running { cancel() }
                sceneEpoch = epoch
            }
            components = entry["components"] as? [String: String] ?? [:]
            contactCounts = entry["contact_counts"] as? [String: [String: Int]] ?? [:]
            visible = entry["visible"] as? Bool ?? false
            if entry["needs_redraw"] as? Bool == true { redraw() }
            if entry["removed"] as? Bool == true { clearAnalysis(); message = "Analysis removed"; return }
            if entry["stale"] as? Bool == true {
                if running { cancel() }
                if !inputMessage { message = entry["error"] as? String ?? "Stale: source coordinates or chemistry changed. Analyze to refresh." }
                states = [:]; summaries = [:]; readout = nil; return
            }
            if !running, entry["restored"] as? Bool == true {
                states = entry["states"] as? [String: String] ?? [:]
                summaries = entry["summaries"] as? [String: [String: Any]] ?? [:]
                if !inputMessage { message = entry["interruption"] as? String ?? (states.values.allSatisfy({ $0 == "ready" }) ? "Restored | ff14SB/Sage | OBC2+ACE" : "Restored incomplete") }
            }
        } catch { message = error.localizedDescription }
    }

    func master() {
        guard let analysisID else { return }
        do { _ = try scene("master", ["analysis_id": analysisID, "visible": !visible]); visible.toggle() }
        catch { message = error.localizedDescription }
    }

    func remove() {
        guard let analysisID else { return }
        do {
            _ = try scene("remove", ["analysis_id": analysisID])
            clearAnalysis(); message = "Analysis removed"
        } catch { message = error.localizedDescription }
    }

    private func clearAnalysis() {
        runIdentity = UUID()
        let identity = runIdentity
        poll?.cancel(); poll = nil; running = false; reviewing = false; inputMessage = false
        analysisID = nil; activeAnalysisID = nil; components = [:]; contactCounts = [:]; states = [:]; summaries = [:]; readout = nil; pinned = false
        invalidateReview(); queryReadout = ""; visible = false
        Task {
            guard runIdentity == identity else { return }
            do { try await worker.stop() } catch { if runIdentity == identity { message = error.localizedDescription } }
        }
    }

    func query() {
        guard let analysisID else { return }
        do {
            guard let values = try scene("query", ["analysis_id": analysisID, "selection": groupSelection]) as? [String: Any],
                  let selected = values["selected"] as? [Double] else { throw EnergyProtocolError.invalidMessage }
            queryReadout = zip(["Coulomb", "Attraction", "Repulsion"], selected).map { "\($0.0): \(String(format: "%.3f", $0.1)) kcal/mol" }.joined(separator: "\n")
        } catch { queryReadout = error.localizedDescription }
    }

    func hover(_ x: Float, _ y: Float) -> Bool {
        guard analysisID != nil else { return false }
        do {
            guard let value = try scene("pick", ["ndc_x": x, "ndc_y": y]) as? [String: Any],
                  let record = value["record"] as? [String: Any] else { if !pinned { readout = nil }; return false }
            if !pinned {
                var lines = [(record["kind"] as? String ?? "Interaction").replacingOccurrences(of: "_", with: " "),
                             (record["atom_labels"] as? [String] ?? []).joined(separator: " | ")]
                for field in ["coulomb_kcal", "attraction_kcal", "repulsion_kcal", "net_lj_kcal", "residue_coulomb_kcal", "residue_net_lj_kcal", "delta_kcal", "value_kcal", "polar_kcal", "ace_kcal"] {
                    if let number = record[field] as? NSNumber { lines.append("\(field.replacingOccurrences(of: "_kcal", with: "")): \(String(format: "%.3f", number.doubleValue)) kcal/mol") }
                }
                for field in ["bound_kj", "reference_kj"] {
                    if let number = record[field] as? NSNumber { lines.append("\(field.replacingOccurrences(of: "_kj", with: "")): \(String(format: "%.3f", number.doubleValue/4.184)) kcal/mol") }
                }
                if let distance = record["distance_angstrom"] as? NSNumber { lines.append("Distance: \(String(format: "%.2f", distance.doubleValue)) A") }
                if let angle = record["D_H_A_degrees"] as? NSNumber { lines.append("D-H-A: \(String(format: "%.1f", angle.doubleValue)) deg") }
                if let distance = record["hbond_distance_angstrom"] as? NSNumber { lines.append("H-bond D-A: \(String(format: "%.2f", distance.doubleValue)) A") }
                if let endpoint = value["endpoint_hash"] as? String { lines.append("Prepared: \(endpoint.prefix(12))") }
                for (field, label) in [("bound_endpoint_hash", "Bound"), ("reference_endpoint_hash", "Reference")] {
                    if let endpoint = record[field] as? String { lines.append("\(label): \(endpoint.prefix(12))") }
                }
                if record["nonunique_allocation"] as? Bool == true { lines.append("Atom allocation is model-dependent") }
                readout = lines.joined(separator: "\n")
            }
            return true
        } catch { if !pinned { readout = nil }; return false }
    }

    func select(_ x: Float, _ y: Float) -> Bool {
        pinned = false
        if hover(x, y) { pinned = true; expanded = true; return true }
        return false
    }

    var hasAnalysis: Bool { analysisID != nil }

    var numericReadout: String {
        let key = ["Interactions": "direct", "Electrostatics": "direct", "Packing": "direct", "Strain": "strain", "Solvation": "global_solvation"][mode]!
        guard states[key] == "ready" else { return states[key] ?? "unavailable" }
        guard let values = summaries[key] else { return states[key] ?? "unavailable" }
        let names = mode == "Interactions" ? ["coulomb", "net_lj"] : mode == "Electrostatics" ? ["coulomb"] : mode == "Packing" ? ["attraction", "repulsion", "net_lj"] : mode == "Strain" ? ["local_strain_kj"] : ["polar", "ace", "total"]
        let labels = ["coulomb": "Coulomb", "attraction": "Attraction", "repulsion": "Repulsion", "net_lj": "Net LJ", "local_strain_kj": "Local strain", "polar": "Polar", "ace": "ACE", "total": "Total"]
        return names.compactMap { name in (values[name] as? NSNumber).map { "\(labels[name]!): \(String(format: "%.3f", $0.doubleValue/4.184)) kcal/mol" } }.joined(separator: "\n")
    }
}

struct EnergyCompactPanel: View {
    @ObservedObject var controller: EnergyViewportController
    @ObservedObject var engine: PyMOLEngine
    var availableHeight: CGFloat = 500
    var availableWidth: CGFloat = 400
    private var leaves: [String] {
        switch controller.mode {
        case "Interactions": return ["hbonds", "salt_bridges", "clashes", "strong_contacts"]
        case "Electrostatics": return ["electrostatics", "hbonds"]
        case "Packing": return ["packing", "clashes"]
        case "Strain": return ["bonds", "angles", "proper_torsions", "improper_torsions", "nonbonded"]
        default: return ["favorable", "unfavorable"]
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Button { controller.expanded.toggle() } label: { Label("Energy", systemImage: "atom") }.help("Molecular energy analysis")
                if controller.running || controller.reviewing { ProgressView().controlSize(.small); Button { controller.cancel() } label: { Image(systemName: "stop.fill") }.help("Cancel calculation") }
                Spacer()
                if controller.expanded { Button { controller.expanded = false } label: { Image(systemName: "chevron.down") }.help("Collapse energy controls") }
            }
            if controller.expanded {
                ScrollView {
                    VStack(alignment: .leading, spacing: 8) {
                        inputPicker("Receptor", selection: $controller.receptor)
                        inputPicker("Ligand", selection: $controller.ligand)
                        Picker("Ligand chemistry", selection: $controller.ligandFormat) { Text("SMILES").tag("SMILES"); Text("SDF").tag("SDF") }.pickerStyle(.segmented)
                            .disabled(controller.running || controller.reviewing)
                        HStack {
                            if controller.ligandFormat == "SDF" {
                                Text(controller.embeddedSDF != nil ? "Embedded SDF" : controller.sdf.isEmpty ? "Ligand SDF (required)" : URL(fileURLWithPath: controller.sdf).lastPathComponent).lineLimit(1).truncationMode(.middle)
                                Button { controller.chooseSDF() } label: { Image(systemName: "folder") }.help("Attach ligand SDF")
                            }
                            Stepper("State \(controller.state)", value: $controller.state, in: 1...999)
                        }.disabled(controller.running || controller.reviewing)
                        HStack { Text("Preparation pH"); TextField("pH", value: $controller.ph, format: .number).frame(width: 60) }
                            .font(.caption).disabled(controller.running || controller.reviewing)
                            .help("OpenMM protonation estimate; preserves supplied explicit states and polar hydrogens")
                        if controller.ligandFormat == "SMILES" {
                            TextField("Ligand isomeric SMILES", text: $controller.smiles, axis: .vertical).lineLimit(2...5)
                                .disabled(controller.running || controller.reviewing)
                        } else { TextField("Explicit SDF index list (optional)", text: $controller.mapping).disabled(controller.running || controller.reviewing) }
                        if !controller.residueLabels.isEmpty {
                            DisclosureGroup("Residue states") {
                                ForEach(controller.residueLabels.keys.sorted(by: { (controller.residueLabels[$0] ?? "") < (controller.residueLabels[$1] ?? "") }), id: \.self) { identifier in
                                    let choices = controller.overrideChoices(identifier)
                                    if !choices.isEmpty {
                                        Picker(controller.residueLabels[identifier] ?? identifier, selection: Binding(get: { controller.proteinOverrides[identifier] ?? "" }, set: { value in
                                            if value.isEmpty { controller.proteinOverrides.removeValue(forKey: identifier) }
                                            else { controller.proteinOverrides[identifier] = value }
                                        })) {
                                            Text("Automatic").tag("")
                                            ForEach(choices, id: \.self) { Text($0).tag($0) }
                                        }.pickerStyle(.menu)
                                    }
                                }
                                ForEach(controller.protonationRows, id: \.self) { Text($0).font(.caption).textSelection(.enabled) }
                            }.font(.caption).disabled(controller.running || controller.reviewing)
                        }
                        if controller.reviewed && !controller.exclusions.isEmpty {
                            ForEach(controller.exclusions, id: \.self) { Text($0).font(.caption).foregroundStyle(.secondary) }
                            Toggle("Listed excluded groups acknowledged", isOn: $controller.confirmed).font(.caption)
                        }
                        Button { controller.analyze() } label: { Label("Analyze", systemImage: "play.fill") }.disabled(controller.analyzeIssue != nil)
                            .help(controller.analyzeIssue ?? "Prepare receptor and score ligand")
                        if let issue = controller.analyzeIssue { Text(issue).font(.caption).foregroundStyle(.secondary) }
                        Picker("Term", selection: $controller.mode) {
                            ForEach(["Interactions", "Electrostatics", "Packing", "Strain", "Solvation"], id: \.self) { Text($0) }
                        }.pickerStyle(.menu)
                        Text(controller.numericReadout).font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                        if controller.mode == "Interactions" || controller.mode == "Electrostatics" { Text("Solvation: \(controller.states["global_solvation"] ?? "pending")").font(.caption) }
                        if controller.mode == "Solvation" {
                            Picker("Channel", selection: $controller.channel) { Text("Total").tag("total"); Text("Polar").tag("polar"); Text("ACE").tag("ace") }.pickerStyle(.segmented)
                            Text("Dots: \(controller.states["local_solvation"] ?? "unavailable")").font(.caption)
                        }
                        ForEach(leaves, id: \.self) { leaf in
                            if let name = controller.components[leaf] {
                                Toggle(contactLabel(leaf), isOn: Binding(get: { engine.objects.first(where: { $0.name == name })?.isEnabled ?? false }, set: { engine.setObjectEnabled(name, $0) })).font(.caption)
                                    .disabled(controller.states[controller.mode == "Solvation" ? "local_solvation" : controller.mode == "Strain" ? "strain" : "direct"] != "ready")
                                    .help(leaf == "strong_contacts" ? "Up to 8 close residues with net van der Waals energy <= -1 kcal/mol. Not a binding affinity." : leaf == "electrostatics" ? "Detailed all-atom Coulomb pairs; not screened by solvation." : leaf == "salt_bridges" ? "Oppositely charged groups within 4 A, from confirmed chemical states." : leaf == "clashes" ? "Net LJ penalty >= 1 kcal/mol; normal H-bonds excluded." : "")
                            }
                        }
                        HStack { Text("Threshold kcal/mol"); TextField("", value: $controller.threshold, format: .number).frame(width: 60) }.font(.caption)
                        HStack { Text("Color scale +/- kcal/mol"); TextField("", value: $controller.scale, format: .number).frame(width: 60) }.font(.caption)
                        HStack { Text("Visible pocket A"); TextField("", value: $controller.pocket, format: .number).frame(width: 60) }.font(.caption)
                        if let readout = controller.readout { Text(readout).font(.system(size: 10, design: .monospaced)).textSelection(.enabled); Toggle("Pinned", isOn: $controller.pinned) }
                        HStack {
                            Button { controller.master() } label: { Image(systemName: controller.visible ? "eye" : "eye.slash") }.help("Show or hide analysis; preserve mixed components")
                            Button { controller.remove() } label: { Image(systemName: "trash") }.help("Remove owned analysis objects")
                        }.disabled(!controller.hasAnalysis)
                        HStack {
                            TextField("Atom-group selection", text: $controller.groupSelection)
                            Button { controller.query() } label: { Image(systemName: "sum") }.help("Sum selected atom groups without scoring")
                        }
                        if !controller.queryReadout.isEmpty { Text(controller.queryReadout).font(.system(size: 10, design: .monospaced)).textSelection(.enabled) }
                    }.textFieldStyle(.roundedBorder)
                }.frame(maxHeight: min(390, max(80, availableHeight-110)))
                Text(controller.message).font(.caption).lineLimit(3).fixedSize(horizontal: false, vertical: true)
            }
        }.padding(10).frame(width: controller.expanded ? min(370, max(0, availableWidth-24)) : controller.running ? 180 : 110)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 6))
            .onAppear { controller.refreshInputs() }
            .onChange(of: engine.objects) { _ in controller.refreshInputs() }
            .onChange(of: controller.channel) { _ in controller.redraw() }
            .onChange(of: controller.threshold) { _ in controller.redraw() }
            .onChange(of: controller.scale) { _ in controller.redraw() }
            .onChange(of: controller.pocket) { _ in controller.redraw() }
    }

    private func inputPicker(_ label: String, selection: Binding<String>) -> some View {
        Picker(label, selection: selection) {
            Text("Select \(label.lowercased())").tag("")
            if !selection.wrappedValue.isEmpty && !(controller.moleculeNames + controller.selectionNames).contains(selection.wrappedValue) {
                Text("\(selection.wrappedValue) (unavailable)").tag(selection.wrappedValue)
            }
            ForEach(controller.moleculeNames, id: \.self) { Text($0).tag($0) }
            if !controller.selectionNames.isEmpty {
                Divider()
                ForEach(controller.selectionNames, id: \.self) { Text("\($0) (selection)").tag($0) }
            }
        }.pickerStyle(.menu).disabled(controller.running || controller.reviewing)
    }

    private func contactLabel(_ leaf: String) -> String {
        let labels = ["hbonds": "H-bonds", "salt_bridges": "Salt bridges", "clashes": "Clashes", "strong_contacts": "Strong packing", "electrostatics": "All Coulomb pairs", "packing": "All packing contacts"]
        let label = labels[leaf] ?? leaf.replacingOccurrences(of: "_", with: " ")
        guard let counts = controller.contactCounts[leaf] else { return label }
        return "\(label) (\(counts["shown"] ?? 0)/\(counts["eligible"] ?? 0))"
    }
}
#endif
