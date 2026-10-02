#if os(macOS) || os(iOS)
import Foundation

// MARK: - Wire types (decoded from pymol_predict_<pid>.json; see appkit_predict.emit)

struct PredictorInfo: Codable, Equatable, Identifiable {
    let id: String
    let msa: Bool          // supports_msa: the method can genuinely use an alignment
}

struct PredictChain: Codable, Equatable, Identifiable {
    let id: String         // spec chain id assigned in order: A, B, C, ...
    let length: Int
    let object: String     // source object, "" for a literal sequence
    let chain: String      // source chain id, "" for a literal sequence
    var isFromObject: Bool { !object.isEmpty }
}

/// The MSA server a search would use now (appkit_predict._msa_server, #598).
struct MSAServerInfo: Codable, Equatable {
    let url: String        // "" when `error` is set
    let origin: String     // "saved" | "msa_server" | "RAYMOL_MSA_SERVER" | "default"
    let isPublic: Bool
    let error: String?     // a saved server that cannot be used; searches are refused

    enum CodingKeys: String, CodingKey { case url, origin, isPublic = "public", error }
}

struct PredictFormPayload: Codable, Equatable {
    let predictors: [PredictorInfo]
    let chains: [PredictChain]
    let error: String?
    var msaServer: MSAServerInfo? = nil   // absent from a payload older than #598

    enum CodingKeys: String, CodingKey {
        case predictors, chains, error, msaServer = "msa_server"
    }
}

enum PredictPhase: Equatable {
    case idle
    case searching(remaining: Int)
    case predicting
    case error(String)
}

struct PredictSizeWarning: Equatable {
    let estimatedBytes: Int
    let availableBytes: Int
}

// MARK: - Pure command composition (unit-tested without an engine)

extension PredictController {

    /// `from pymol import cmd as _c\n_c.predict(...)`. Optional args are omitted when
    /// nil so predict applies its own defaults (notably: no seed → a fresh seed per
    /// run). `msa` is passed only on the literal-sequence path; object inputs let
    /// predict pick up attached alignments.
    nonisolated static func predictPython(predictor: String, input: String, nModels: Int,
                              recyclingSteps: Int, diffusionSteps: Int,
                              seed: Int?, msaDepth: Int?, name: String?,
                              msa: String?) -> String {
        var args = ["\(InferenceJob.pythonLiteral(predictor)), "
                    + "\(InferenceJob.pythonLiteral(input))"]
        args.append("n_models=\(nModels)")
        args.append("recycling_steps=\(recyclingSteps)")
        args.append("diffusion_steps=\(diffusionSteps)")
        if let seed { args.append("seed=\(seed)") }
        if let msaDepth { args.append("msa_depth=\(msaDepth)") }
        if let name, !name.isEmpty { args.append("name=\(InferenceJob.pythonLiteral(name))") }
        if let msa, !msa.isEmpty { args.append("msa=\(InferenceJob.pythonLiteral(msa))") }
        return "from pymol import cmd as _c\n_c.predict(\(args.joined(separator: ", ")))"
    }

    /// `_c.msa_search(...)`. `target`/`chain` are passed only when non-empty (the
    /// object path); a literal sequence lands the alignment unattached under `name`.
    nonisolated static func msaSearchPython(sequence: String, name: String, target: String,
                                chain: String, mode: String, server: String) -> String {
        var args = ["\(InferenceJob.pythonLiteral(sequence))"]
        args.append("name=\(InferenceJob.pythonLiteral(name))")
        if !target.isEmpty { args.append("target=\(InferenceJob.pythonLiteral(target))") }
        if !chain.isEmpty { args.append("chain=\(InferenceJob.pythonLiteral(chain))") }
        args.append("mode=\(InferenceJob.pythonLiteral(mode))")
        if !server.isEmpty { args.append("server=\(InferenceJob.pythonLiteral(server))") }
        return "from pymol import cmd as _c\n_c.msa_search(\(args.joined(separator: ", ")))"
    }

    /// The public ColabFold server: the dropdown's built-in entry and Python's default.
    nonisolated static let publicServer = "https://api.colabfold.com"

    /// True for ColabFold's public deployment. Mirrors `colabfold.PUBLIC_HOSTS` (one
    /// host); the hostname is compared exactly so a look-alike domain is not "public".
    nonisolated static func isPublicServer(_ url: String) -> Bool {
        URLComponents(string: url.trimmingCharacters(in: .whitespaces))?.host?.lowercased()
            == "api.colabfold.com"
    }

    /// `url` trimmed and without a trailing '/', or nil when it is not an http(s) URL with
    /// a host -- the same test `colabfold.normalize` applies, so the add sheet refuses here
    /// what Python would refuse later.
    nonisolated static func normalizedServer(_ url: String) -> String? {
        var text = url.trimmingCharacters(in: .whitespacesAndNewlines)
        while text.hasSuffix("/") { text.removeLast() }
        guard let parts = URLComponents(string: text),
              let scheme = parts.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = parts.host, !host.isEmpty else { return nil }
        return text
    }

    /// A Python error message without the "Error:" prefix PyMOL puts on it.
    nonisolated static func plainError(_ text: String) -> String {
        var message = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if message.hasPrefix("Error:") {
            message = String(message.dropFirst("Error:".count))
                .trimmingCharacters(in: .whitespaces)
        }
        return message
    }

    /// Host and port of `url`, for the dropdown and the "sent to" line.
    nonisolated static func hostLabel(_ url: String) -> String {
        guard let parts = URLComponents(string: url), let name = parts.host, !name.isEmpty
        else { return url }
        return parts.port.map { "\(name):\($0)" } ?? name
    }

    /// `_c.msa_server(...)`: make `server` the saved default, or forget the saved default
    /// (back to ColabFold) when it is empty. quiet=0 so the console records the change.
    nonisolated static func msaServerPython(_ server: String) -> String {
        let text = server.trimmingCharacters(in: .whitespacesAndNewlines)
        let arg = InferenceJob.pythonLiteral(text.isEmpty ? "reset" : text)
        return "from pymol import cmd as _c\n_c.msa_server(\(arg), quiet=0)"
    }

    /// Where "Sequences are sent to …" says a search goes: the picked server's host, with
    /// ColabFold called out as public.
    nonisolated static func serverLabel(_ url: String?) -> String {
        guard let url, !url.isEmpty else { return "the default MSA server" }
        let host = hostLabel(url)
        return isPublicServer(url) ? "\(host), a public server" : host
    }

    /// Per-chain sequences of a literal input: split on '/', strip whitespace,
    /// upper-case — exactly what parse_chains does on the Python side.
    nonisolated static func literalChainSequences(_ input: String) -> [String] {
        input.split(separator: "/").map {
            $0.replacingOccurrences(of: " ", with: "")
                .replacingOccurrences(of: "\t", with: "")
                .uppercased()
        }
    }

    /// `msa=` slots: one '/'-joined entry per chain in order, the alignment name for
    /// a requested chain and empty otherwise (empty folds that chain single-sequence).
    nonisolated static func msaSlots(orderedChains: [PredictChain], requested: Set<String>,
                         nameFor: (PredictChain) -> String) -> String {
        orderedChains.map { requested.contains($0.id) ? nameFor($0) : "" }
            .joined(separator: "/")
    }

    /// A deterministic, collision-resistant alignment name for a requested chain, so
    /// the running search and the landed alignment share a name the state machine can
    /// match on. Object path keys on (object, source chain); literal path on an FNV
    /// hash of the chain's sequence so a re-run reuses the cached alignment.
    nonisolated static func alignmentBaseName(for chain: PredictChain,
                                  literalSequence: String?) -> String {
        if chain.isFromObject {
            let obj = sanitize(chain.object)
            let ch = chain.chain.isEmpty ? "x" : sanitize(chain.chain)
            return "predui_\(obj)_\(ch)"
        }
        let seq = literalSequence ?? ""
        return "predui_\(fnvHex(seq))_\(chain.id)"
    }

    nonisolated static func sanitize(_ s: String) -> String {
        String(s.map { $0.isLetter || $0.isNumber ? $0 : "_" })
    }

    /// FNV-1a 32-bit, hex. A fixed hash (not Swift's randomized Hasher) so the name is
    /// stable across launches and a re-run hits msa_search's on-disk cache.
    nonisolated static func fnvHex(_ s: String) -> String {
        var h: UInt32 = 0x811c9dc5
        for b in s.utf8 { h = (h ^ UInt32(b)) &* 0x0100_0193 }
        return String(format: "%08x", h)
    }
}

import Combine

@MainActor
final class PredictController: ObservableObject {
    // Inputs (bound by PredictBar)
    @Published var inputText = ""
    @Published var predictor = ""
    @Published var useMSA = false
    @Published var msaChains: Set<String> = []
    @Published var nModels = 1
    // Advanced
    @Published var recyclingSteps = 3
    @Published var diffusionSteps = 200
    @Published var seedText = ""        // empty → omit (fresh per run)
    @Published var msaDepthText = ""    // empty → omit (predictor default)
    @Published var msaMode = "env"
    @Published var resultName = ""
    /// The server this bar's searches go to (#598). Picking one in the dropdown changes
    /// only this; the saved default changes only through addServer / setDefaultServer.
    /// nil until the form payload names a server, and while the saved one is unusable.
    @Published var selectedServer: String?

    // Resolved / status (rendered by PredictBar)
    @Published var msaServer: MSAServerInfo?
    @Published var availablePredictors: [PredictorInfo] = []
    @Published var chains: [PredictChain] = []
    @Published var resolveError: String?
    @Published var phase: PredictPhase = .idle
    @Published var pendingSizeWarning: PredictSizeWarning?
    /// Run is waiting on "your sequences go to a public server" (#598).
    @Published var pendingPublicWarning = false
    /// Private servers the user has added, in the order added. Persisted in `settings`.
    @Published private(set) var savedServers: [String] = []
    /// The saved default as Python last reported it; nil before a payload, or when the
    /// saved file cannot be used.
    @Published private(set) var defaultServer: String?

    /// Where the server list and "don't warn again" live. A seam so tests use their own.
    var settings: UserDefaults = .standard {
        didSet { savedServers = settings.stringArray(forKey: Self.serversKey) ?? [] }
    }
    nonisolated static let serversKey = "msaServers"
    nonisolated static let publicWarningSuppressedKey = "msaPublicWarningSuppressed"

    // Injected seams (default no-ops; PyMOLEngine wires real ones in Task 4).
    var runPythonSeam: (String) -> Void = { _ in }
    var refreshTrigger: (String) -> Void = { _ in }
    var availableBytesProvider: () -> Int = { PredictSizeGuard.availableBytes }

    // Per-run plan: the alignment name expected for each requested spec-chain id.
    private var plannedNames: [String: String] = [:]

    // Fix A: latest snapshot from onEngineState (updated even while idle).
    private var latestAlignments: [AlignmentEntry] = []

    // #598: failures as of the latest poll, and the ids that already existed when this
    // run's searches went out. Planned names repeat across runs, so only a failure NEW
    // since then can be this run's.
    private var latestFailures: [MSAFailureEntry] = []
    private var staleFailureIDs: Set<String> = []

    // Fix B: one-tick grace so the just-fired search can register before being declared failed.
    private var failGraceTicks = 0

    init() {
        savedServers = settings.stringArray(forKey: Self.serversKey) ?? []
    }

    // MARK: entering the mode / input changes

    /// Load predictors (input-independent) and clear the form's resolved state.
    func refresh() {
        chains = []
        msaChains = []
        resolveError = nil
        phase = .idle
        pendingSizeWarning = nil
        pendingPublicWarning = false
        refreshTrigger("")          // emit('') → predictors only
    }

    /// Re-resolve the current input (called debounced by PredictBar on inputText edits).
    func inputChanged() { refreshTrigger(inputText) }

    /// Apply a decoded pymol_predict_<pid>.json payload.
    func loadFormPayload(_ payload: PredictFormPayload) {
        availablePredictors = payload.predictors
        if predictor.isEmpty || !payload.predictors.contains(where: { $0.id == predictor }) {
            predictor = payload.predictors.first?.id ?? ""
        }
        chains = payload.chains
        resolveError = payload.error
        // Drop any selected MSA chains that no longer exist in the resolved input.
        let ids = Set(payload.chains.map(\.id))
        msaChains = msaChains.intersection(ids)
        if let info = payload.msaServer { applyServerInfo(info) }
    }

    private func applyServerInfo(_ info: MSAServerInfo) {
        msaServer = info
        guard info.error == nil, !info.url.isEmpty else {
            // Python refuses to search past an unusable saved server; picking ColabFold
            // here on its behalf would publish what the user meant to keep private.
            defaultServer = nil
            if selectedServer == nil || selectedServer.map(isListed) != true {
                selectedServer = nil
            }
            return
        }
        // A server saved from the console (or the env var's) belongs in the list too.
        if !PredictController.isPublicServer(info.url) { remember(info.url) }
        let changed = info.url != defaultServer
        defaultServer = info.url
        // Follow the default when it changes, or when there is no usable pick; otherwise
        // the user's pick stands (a payload also arrives on every input edit).
        if changed || selectedServer.map(isListed) != true { selectedServer = info.url }
    }

    private func isListed(_ url: String) -> Bool {
        PredictController.isPublicServer(url) || savedServers.contains(url)
    }

    private func remember(_ url: String) {
        guard !savedServers.contains(url) else { return }
        savedServers.append(url)
        settings.set(savedServers, forKey: Self.serversKey)
    }

    /// Add a private server to the dropdown and pick it. Returns why it was refused, or
    /// nil. Only `makeDefault` touches the saved default.
    @discardableResult
    func addServer(_ url: String, makeDefault: Bool) -> String? {
        guard let server = PredictController.normalizedServer(url) else {
            return "Enter an address starting with http:// or https://"
        }
        if PredictController.isPublicServer(server) {
            return "That is the public ColabFold server, which is always in the list."
        }
        remember(server)
        selectedServer = server
        if makeDefault { setDefaultServer(server) }
        return nil
    }

    /// Make `url` the saved default (ColabFold forgets the saved one) and pick it.
    func setDefaultServer(_ url: String) {
        let isPublic = PredictController.isPublicServer(url)
        runPythonSeam(PredictController.msaServerPython(isPublic ? "" : url))
        defaultServer = isPublic ? PredictController.publicServer : url
        selectedServer = defaultServer
        refreshTrigger(inputText)   // the payload then reports what Python settled on
    }

    /// Drop a private server from the dropdown. Deleting the default falls back to
    /// ColabFold; deleting the pick falls back to the default.
    func removeServer(_ url: String) {
        guard let index = savedServers.firstIndex(of: url) else { return }
        savedServers.remove(at: index)
        settings.set(savedServers, forKey: Self.serversKey)
        if url == defaultServer {
            setDefaultServer(PredictController.publicServer)
        } else if url == selectedServer {
            selectedServer = defaultServer
        }
    }

    // MARK: run

    private var selectedSupportsMSA: Bool {
        availablePredictors.first { $0.id == predictor }?.msa ?? false
    }

    private var tokenCount: Int { chains.reduce(0) { $0 + $1.length } }

    private var effectiveMSADepth: Int {
        if let d = Int(msaDepthText), d > 0 { return d }
        return (useMSA && selectedSupportsMSA && !msaChains.isEmpty)
            ? PredictSizeGuard.maximumMSADepth : 1
    }

    func run() {
        guard !predictor.isEmpty, !chains.isEmpty else {
            phase = .error(resolveError ?? "Nothing to fold — enter a sequence, "
                           + "selection, or object.")
            return
        }
        if useMSAEffective, selectedServer == nil, let problem = msaServer?.error {
            // Python's reason says which setting is at fault -- the saved file or
            // RAYMOL_MSA_SERVER -- so it is passed on rather than guessed at here.
            phase = .error("The MSA server setting cannot be used: "
                           + PredictController.plainError(problem)
                           + " Pick a server, or set a default under Edit….")
            return
        }
        // Size guard (per predictor). A warn stops for confirmation; a refusal is fatal.
        // Protenix sizes per VARIANT (#316): base and v2 have different pair-tensor
        // widths and caps. Map the id → variant the same way ProtenixSizeGuard does —
        // explicitly base only for a "protenix-base*" pack, else the expensive v2
        // (which also covers the "protenix" alias for protenix-v2-int8).
        //
        // The Protenix branch is macOS-only because ProtenixSizeGuard is (the runtime
        // was not ported to iOS — see PyMOLBridge.mm's RAYMOL_PREDICT_RUNTIMES). On iOS
        // the branch is also unreachable rather than merely absent: the host advertises
        // "boltz" alone, so check_available refuses every protenix predictor, and
        // appkit_predict._predictors() FILTERS the form list by check_available, so none
        // reaches `availablePredictors`. Compiling it out removes dead code rather than
        // narrowing behaviour — but note the filter is what makes that true. Before it
        // existed the list was every REGISTERED predictor and Protenix ids did arrive
        // here on iOS, where this branch was compiled out and they fell through to the
        // Boltz curve: a Protenix job sized against Boltz's much smaller footprint.
        //
        // Also why boltz2-bf16 refuses on iOS (see Boltz2BF16Predictor.check_available):
        // `decide` below takes no predictor, so every Boltz pack shares one curve, and
        // that curve is fitted to measured int8 footprints.
        let decision: PredictSizeGuard.Decision
        #if os(macOS)
        if predictor.hasPrefix("protenix") {
            let variant: ProtenixSizeGuard.Variant =
                predictor.contains("protenix-base") ? .base : .v2
            decision = ProtenixSizeGuard.decide(tokens: tokenCount, variant: variant,
                                                availableBytes: availableBytesProvider())
        } else {
            decision = PredictSizeGuard.decide(tokens: tokenCount, msaDepth: effectiveMSADepth,
                                               availableBytes: availableBytesProvider())
        }
        #else
        decision = PredictSizeGuard.decide(tokens: tokenCount, msaDepth: effectiveMSADepth,
                                           availableBytes: availableBytesProvider())
        #endif
        switch decision {
        case .ok:
            proceed()
        case let .warn(estimatedBytes, availableBytes):
            pendingSizeWarning = PredictSizeWarning(estimatedBytes: estimatedBytes,
                                                    availableBytes: availableBytes)
        case let .refuse(maxFittingTokens):
            phase = .error("Too large for this machine — at most about "
                           + "\(maxFittingTokens) residues fit.")
        case let .refuseDepth(maxFittingDepth):
            phase = .error("The alignment is too deep for this machine — set "
                           + "msa_depth to at most \(maxFittingDepth).")
        }
    }

    func confirmPendingWarning() { pendingSizeWarning = nil; proceed() }
    func cancelPendingWarning() { pendingSizeWarning = nil; phase = .idle }

    private var useMSAEffective: Bool {
        useMSA && selectedSupportsMSA && !msaChains.isEmpty
    }

    func confirmPublicWarning(dontShowAgain: Bool) {
        if dontShowAgain { settings.set(true, forKey: Self.publicWarningSuppressedKey) }
        pendingPublicWarning = false
        proceed(publicServerAccepted: true)
    }

    func cancelPublicWarning() {
        pendingPublicWarning = false
        plannedNames = [:]
        phase = .idle
    }

    private func proceed(publicServerAccepted: Bool = false) {
        pendingSizeWarning = nil
        guard useMSAEffective else { submitPredict(); return }

        // Plan one alignment name per requested chain; start a search only for chains
        // not already satisfied by the current latestAlignments snapshot (Fix A).
        plannedNames = [:]
        let literalSeqs = PredictController.literalChainSequences(inputText)
        var searches: [String] = []
        for ch in chains where msaChains.contains(ch.id) {
            let literal = ch.isFromObject ? nil
                : (indexOf(ch).map { $0 < literalSeqs.count ? literalSeqs[$0] : "" } ?? "")
            let name = PredictController.alignmentBaseName(for: ch, literalSequence: literal)
            plannedNames[ch.id] = name
            // Fix A: skip search if alignment already landed.
            if isSatisfied(ch, alignments: latestAlignments) { continue }
            let sequence = ch.isFromObject
                ? "(\(inputText)) and chain \(ch.chain)"
                : (literal ?? "")
            let cmd = PredictController.msaSearchPython(
                sequence: sequence, name: name,
                target: ch.isFromObject ? ch.object : "",
                chain: ch.isFromObject ? ch.chain : "",
                mode: msaMode, server: selectedServer ?? "")
            searches.append(cmd)
        }
        // Fix A: if all chains were already satisfied, predict immediately.
        if searches.isEmpty { submitPredict(); return }
        // Asked only now, once a sequence would actually leave: an alignment already
        // attached sends nothing (#598).
        if !publicServerAccepted, PredictController.isPublicServer(selectedServer ?? ""),
           !settings.bool(forKey: Self.publicWarningSuppressedKey) {
            pendingPublicWarning = true
            return
        }
        staleFailureIDs = Set(latestFailures.map(\.id))
        searches.forEach(runPythonSeam)
        // Fix B: arm the one-tick grace so the just-fired searches can register.
        failGraceTicks = 1
        phase = .searching(remaining: searches.count)
    }

    private func indexOf(_ ch: PredictChain) -> Int? {
        chains.firstIndex(where: { $0.id == ch.id })
    }

    /// Called from the engine's 500 ms alignment/search poll. Advances or completes
    /// the search-then-predict pipeline.
    ///
    /// Called on EVERY poll (PyMOLEngine.panelPolled), changed or not: the grace tick
    /// below counts polls, and a failed search changes nothing else (#598).
    func onEngineState(alignments: [AlignmentEntry], searches: [MSASearchEntry],
                       failures: [MSAFailureEntry] = []) {
        // Fix A: always record latest state so proceed() can skip already-satisfied chains.
        latestAlignments = alignments
        latestFailures = failures
        guard case .searching = phase else { return }
        let searchNames = Set(searches.map(\.name))
        var remaining: [String] = []
        var failed: [String] = []
        var reasons: [String] = []
        for ch in chains where msaChains.contains(ch.id) {
            if isSatisfied(ch, alignments: alignments) { continue }
            let name = plannedNames[ch.id] ?? ""
            if let failure = failures.first(where: {
                $0.name == name && !staleFailureIDs.contains($0.id)
            }) {
                reasons.append("chain \(ch.id): \(failure.error)")   // reported: no grace
            } else if searchNames.contains(name) { remaining.append(ch.id) }  // running
            else { failed.append(ch.id) }                            // gone, no result
        }
        if !reasons.isEmpty {
            phase = .error("MSA search failed for " + reasons.joined(separator: "; "))
            return
        }
        if !failed.isEmpty {
            // Fix B: one-tick grace before declaring failure — the just-fired search may
            // not have appeared in msaSearches on the very first poll tick.
            if failGraceTicks > 0 {
                failGraceTicks -= 1
                phase = .searching(remaining: failed.count + remaining.count)
                return
            }
            phase = .error("MSA search did not complete for chain(s) "
                           + failed.sorted().joined(separator: ", ") + ".")
            return
        }
        if remaining.isEmpty { submitPredict() }
        else { phase = .searching(remaining: remaining.count) }
    }

    /// A requested chain is satisfied once its alignment exists. Object chains match on
    /// attachment to (object, source chain) — which also reuses an alignment the user
    /// attached earlier; literal chains match on the planned alignment name.
    private func isSatisfied(_ ch: PredictChain, alignments: [AlignmentEntry]) -> Bool {
        if ch.isFromObject {
            return alignments.contains { $0.target == ch.object && $0.chain == ch.chain }
        }
        let name = plannedNames[ch.id] ?? ""
        return alignments.contains { $0.name == name }
    }

    private func submitPredict() {
        let seed = Int(seedText)
        let depth = Int(msaDepthText)
        // msa= slots only for the literal path; object inputs auto-use attachments.
        var slots: String? = nil
        if useMSAEffective, let first = chains.first, !first.isFromObject {
            slots = PredictController.msaSlots(
                orderedChains: chains, requested: msaChains,
                nameFor: { plannedNames[$0.id] ?? "" })
        }
        let cmd = PredictController.predictPython(
            predictor: predictor, input: inputText, nModels: nModels,
            recyclingSteps: recyclingSteps, diffusionSteps: diffusionSteps,
            seed: seed, msaDepth: depth,
            name: resultName.isEmpty ? nil : resultName, msa: slots)
        runPythonSeam(cmd)
        // The job is now the engine's; the progress tray is its single source of
        // truth from here. Return the bar to ready rather than leaving a sticky
        // "submitted" status that only the mode-exit button could clear — so the
        // user can watch the tray (or queue another fold) unobstructed.
        plannedNames = [:]
        failGraceTicks = 0
        phase = .idle
    }

    func cancel() {
        for name in plannedNames.values {
            runPythonSeam("from pymol import cmd as _c\n_c.msa_cancel(\(InferenceJob.pythonLiteral(name)))")
        }
        plannedNames = [:]
        phase = .idle
        failGraceTicks = 0
        pendingPublicWarning = false
    }
}
#endif
