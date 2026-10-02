#if os(macOS)
import XCTest
@testable import RayMol

final class PredictControllerTests: XCTestCase {

    // MARK: composition

    func testPredictPythonMinimal() {
        let s = PredictController.predictPython(
            predictor: "boltz2", input: "MKTAY", nModels: 1,
            recyclingSteps: 3, diffusionSteps: 200,
            seed: nil, msaDepth: nil, name: nil, msa: nil)
        XCTAssertTrue(s.contains("_c.predict('boltz2', 'MKTAY'"))
        XCTAssertTrue(s.contains("n_models=1"))
        XCTAssertTrue(s.contains("recycling_steps=3"))
        XCTAssertTrue(s.contains("diffusion_steps=200"))
        XCTAssertFalse(s.contains("seed="))       // omitted → fresh per run
        XCTAssertFalse(s.contains("msa_depth="))
        XCTAssertFalse(s.contains("name="))
        XCTAssertFalse(s.contains("msa="))
    }

    func testPredictPythonEscapesSelectionAndAddsOptions() {
        let s = PredictController.predictPython(
            predictor: "boltz2", input: "1ubq and chain A", nModels: 5,
            recyclingSteps: 3, diffusionSteps: 300,
            seed: 42, msaDepth: 256, name: "my pred", msa: "alnA//alnC")
        XCTAssertTrue(s.contains("_c.predict('boltz2', '1ubq and chain A'"))
        XCTAssertTrue(s.contains("n_models=5"))
        XCTAssertTrue(s.contains("diffusion_steps=300"))
        XCTAssertTrue(s.contains("seed=42"))
        XCTAssertTrue(s.contains("msa_depth=256"))
        XCTAssertTrue(s.contains("name='my pred'"))
        XCTAssertTrue(s.contains("msa='alnA//alnC'"))
    }

    func testMsaSearchPythonObjectPath() {
        let s = PredictController.msaSearchPython(
            sequence: "1ubq and chain A", name: "predui_x", target: "1ubq",
            chain: "A", mode: "env", server: "")
        XCTAssertTrue(s.contains("_c.msa_search('1ubq and chain A'"))
        XCTAssertTrue(s.contains("name='predui_x'"))
        XCTAssertTrue(s.contains("target='1ubq'"))
        XCTAssertTrue(s.contains("chain='A'"))
        XCTAssertTrue(s.contains("mode='env'"))
        XCTAssertFalse(s.contains("server="))     // blank → use the setting/default
    }

    func testMsaSearchPythonLiteralPathHasNoTarget() {
        let s = PredictController.msaSearchPython(
            sequence: "MKTAY", name: "predui_y", target: "", chain: "",
            mode: "all", server: "https://msa.internal")
        XCTAssertTrue(s.contains("_c.msa_search('MKTAY'"))
        XCTAssertTrue(s.contains("name='predui_y'"))
        XCTAssertFalse(s.contains("target="))
        XCTAssertFalse(s.contains("chain="))
        XCTAssertTrue(s.contains("mode='all'"))
        XCTAssertTrue(s.contains("server='https://msa.internal'"))
    }

    func testLiteralChainSequencesSplitStripUpper() {
        XCTAssertEqual(PredictController.literalChainSequences(" mkt ay / gshma "),
                       ["MKTAY", "GSHMA"])
    }

    func testMsaSlotsOrderedWithEmptyForUnselected() {
        let chains = [
            PredictChain(id: "A", length: 5, object: "", chain: ""),
            PredictChain(id: "B", length: 5, object: "", chain: ""),
            PredictChain(id: "C", length: 5, object: "", chain: ""),
        ]
        let slots = PredictController.msaSlots(
            orderedChains: chains,
            requested: ["A", "C"],
            nameFor: { "aln\($0.id)" })
        XCTAssertEqual(slots, "alnA//alnC")
    }

    func testFormPayloadDecodes() throws {
        let json = """
        {"predictors":[{"id":"boltz2","msa":true},{"id":"protenix","msa":false}],
         "chains":[{"id":"A","length":129,"object":"1ubq","chain":"A"}],
         "error":null}
        """.data(using: .utf8)!
        let payload = try JSONDecoder().decode(PredictFormPayload.self, from: json)
        XCTAssertEqual(payload.predictors.map(\.id), ["boltz2", "protenix"])
        XCTAssertFalse(payload.predictors[1].msa)
        XCTAssertEqual(payload.chains.first?.length, 129)
        XCTAssertTrue(payload.chains.first!.isFromObject)
        XCTAssertNil(payload.error)
        XCTAssertNil(payload.msaServer)   // an older payload without the key still decodes
    }

    // MARK: MSA server (#598)

    func testFormPayloadDecodesTheMSAServer() throws {
        let json = """
        {"predictors":[],"chains":[],"error":null,
         "msa_server":{"url":"https://msa.internal:8080","origin":"saved",
                       "public":false,"error":null}}
        """.data(using: .utf8)!
        let payload = try JSONDecoder().decode(PredictFormPayload.self, from: json)
        XCTAssertEqual(payload.msaServer,
                       MSAServerInfo(url: "https://msa.internal:8080", origin: "saved",
                                     isPublic: false, error: nil))
    }

    func testMsaServerPythonSaves() {
        XCTAssertEqual(PredictController.msaServerPython(" https://msa.internal "),
                       "from pymol import cmd as _c\n"
                       + "_c.msa_server('https://msa.internal', quiet=0)")
    }

    func testMsaServerPythonResetsWhenEmpty() {
        for blank in ["", "   "] {
            XCTAssertEqual(PredictController.msaServerPython(blank),
                           "from pymol import cmd as _c\n_c.msa_server('reset', quiet=0)")
        }
    }

    func testServerLabelNamesThePrivateHost() {
        XCTAssertEqual(PredictController.serverLabel("https://msa.internal:8080"),
                       "msa.internal:8080")
    }

    func testServerLabelSaysColabFoldIsPublic() {
        XCTAssertEqual(PredictController.serverLabel("https://api.colabfold.com"),
                       "api.colabfold.com, a public server")
    }

    func testServerLabelBeforeAServerIsKnown() {
        XCTAssertEqual(PredictController.serverLabel(nil), "the default MSA server")
    }

    func testOnlyColabFoldCountsAsPublic() {
        XCTAssertTrue(PredictController.isPublicServer("https://api.colabfold.com"))
        XCTAssertTrue(PredictController.isPublicServer("https://API.colabfold.com/"))
        XCTAssertFalse(PredictController.isPublicServer("https://msa.internal"))
        XCTAssertFalse(PredictController.isPublicServer("https://api.colabfold.com.evil.example"))
    }

    func testNormalizedServerAcceptsHttpAndHttps() {
        XCTAssertEqual(PredictController.normalizedServer(" https://msa.internal/ "),
                       "https://msa.internal")
        XCTAssertEqual(PredictController.normalizedServer("http://10.0.0.5:8080"),
                       "http://10.0.0.5:8080")
    }

    func testNormalizedServerRefusesWhatPythonWouldRefuse() {
        for bad in ["", "msa.internal", "ftp://msa.internal", "https://", "not a url"] {
            XCTAssertNil(PredictController.normalizedServer(bad), bad)
        }
    }

    // MARK: Task 2 deferred — direct coverage of pure statics

    func testFNVDigestIsPinned() {
        // FNV-1a 32-bit hash of "MKTAY" — pinned so any future change to the hash is caught.
        XCTAssertEqual(PredictController.fnvHex("MKTAY"), "6c788fb1")
    }

    func testAlignmentBaseNameObjectPath() {
        let ch = PredictChain(id: "A", length: 129, object: "1ubq", chain: "A")
        XCTAssertEqual(PredictController.alignmentBaseName(for: ch, literalSequence: nil),
                       "predui_1ubq_A")
    }

    func testAlignmentBaseNameLiteralPath() {
        let ch = PredictChain(id: "A", length: 5, object: "", chain: "")
        let name = PredictController.alignmentBaseName(for: ch, literalSequence: "MKTAY")
        XCTAssertEqual(name, "predui_\(PredictController.fnvHex("MKTAY"))_A")
    }
}

@MainActor
final class PredictControllerRunTests: XCTestCase {

    private let gib = 1024 * 1024 * 1024

    private func makeController(captured: NSMutableArray) -> PredictController {
        let c = PredictController()
        c.runPythonSeam = { captured.add($0) }
        c.availableBytesProvider = { 64 * (1024 * 1024 * 1024) }  // never warns
        return c
    }

    private func chain(_ id: String, _ len: Int, obj: String = "", ch: String = "")
        -> PredictChain { PredictChain(id: id, length: len, object: obj, chain: ch) }

    func testRunWithoutMSASubmitsPredictImmediately() {
        let cmds = NSMutableArray()
        let c = makeController(captured: cmds)
        c.loadFormPayload(PredictFormPayload(
            predictors: [PredictorInfo(id: "boltz2", msa: true)],
            chains: [chain("A", 30)], error: nil))
        c.inputText = "MKTAY"
        c.predictor = "boltz2"
        c.nModels = 3
        c.run()
        // Submitting hands the job to the tray and returns the bar to ready (.idle),
        // rather than dwelling on a sticky "submitted" status.
        XCTAssertEqual(c.phase, .idle)
        XCTAssertEqual(cmds.count, 1)
        let sent = cmds[0] as! String
        XCTAssertTrue(sent.contains("_c.predict('boltz2', 'MKTAY'"))
        XCTAssertTrue(sent.contains("n_models=3"))
    }

    func testRunWithMSAObjectPathStartsSearchesThenPredicts() {
        let cmds = NSMutableArray()
        let c = makeController(captured: cmds)
        c.loadFormPayload(PredictFormPayload(
            predictors: [PredictorInfo(id: "boltz2", msa: true)],
            chains: [chain("A", 60, obj: "1ubq", ch: "A")], error: nil))
        c.inputText = "1ubq"
        c.predictor = "boltz2"
        c.useMSA = true
        c.msaChains = ["A"]
        c.run()

        // A search started; not predicting yet. The search is chain-SCOPED —
        // msa_search refuses a complex, so per-chain scoping is required even for a
        // single-chain object.
        XCTAssertEqual(c.phase, .searching(remaining: 1))
        XCTAssertEqual(cmds.count, 1)
        XCTAssertTrue((cmds[0] as! String).contains("_c.msa_search('(1ubq) and chain A'"))
        XCTAssertTrue((cmds[0] as! String).contains("target='1ubq'"))
        XCTAssertTrue((cmds[0] as! String).contains("chain='A'"))

        // The alignment lands (name matches predui_1ubq_A, attached to 1ubq/A).
        let landed = AlignmentEntry(id: "aln", name: "predui_1ubq_A", depth: 8,
                                    columns: 60, residues: 60, target: "1ubq", chain: "A")
        c.onEngineState(alignments: [landed], searches: [])

        XCTAssertEqual(c.phase, .idle)   // submitted → bar back to ready; tray owns progress
        XCTAssertEqual(cmds.count, 2)
        // Object path: predict does NOT carry an msa= arg (auto-attach).
        XCTAssertFalse((cmds[1] as! String).contains("msa="))
        XCTAssertTrue((cmds[1] as! String).contains("_c.predict('boltz2', '1ubq'"))
    }

    func testMSALiteralPathPassesSlots() {
        let cmds = NSMutableArray()
        let c = makeController(captured: cmds)
        c.loadFormPayload(PredictFormPayload(
            predictors: [PredictorInfo(id: "boltz2", msa: true)],
            chains: [chain("A", 5), chain("B", 5)], error: nil))
        c.inputText = "MKTAY/GSHMA"
        c.predictor = "boltz2"
        c.useMSA = true
        c.msaChains = ["A"]              // only chain A gets an MSA
        c.run()
        XCTAssertEqual(c.phase, .searching(remaining: 1))
        let name = PredictController.alignmentBaseName(
            for: c.chains[0], literalSequence: "MKTAY")
        let landed = AlignmentEntry(id: "aln", name: name, depth: 4, columns: 5,
                                    residues: 5, target: "", chain: "")
        c.onEngineState(alignments: [landed], searches: [])
        XCTAssertEqual(c.phase, .idle)   // submitted → bar back to ready
        XCTAssertTrue((cmds.lastObject as! String).contains("msa='\(name)/'"))  // B empty
    }

    func testSearchThatVanishesWithoutLandingIsAnError() {
        let cmds = NSMutableArray()
        let c = makeController(captured: cmds)
        c.loadFormPayload(PredictFormPayload(
            predictors: [PredictorInfo(id: "boltz2", msa: true)],
            chains: [chain("A", 60, obj: "1ubq", ch: "A")], error: nil))
        c.inputText = "1ubq"; c.predictor = "boltz2"; c.useMSA = true; c.msaChains = ["A"]
        c.run()
        // Fix B: the FIRST tick with no alignment and no running search burns the grace tick
        // — the search hasn't had time to appear in engine.msaSearches yet.
        c.onEngineState(alignments: [], searches: [])
        XCTAssertEqual(c.phase, .searching(remaining: 1), "first tick must NOT error (grace)")
        XCTAssertEqual(cmds.count, 1)   // predict still not submitted
        // SECOND tick with neither alignment nor running search → genuinely failed.
        c.onEngineState(alignments: [], searches: [])
        guard case .error = c.phase else { return XCTFail("expected error phase on second tick") }
        XCTAssertEqual(cmds.count, 1)   // predict was never submitted
    }

    // #598: a failed search used to leave the bar on "Building 1 alignment…" for good.

    private func literalRunNeedingSearch(_ cmds: NSMutableArray) -> (PredictController, String) {
        let c = makeController(captured: cmds)
        c.loadFormPayload(PredictFormPayload(
            predictors: [PredictorInfo(id: "boltz2", msa: true)],
            chains: [chain("A", 24)], error: nil))
        c.inputText = "MKTAYIAKQRQISFVKSHFSRQLE"; c.predictor = "boltz2"
        c.useMSA = true; c.msaChains = ["A"]
        let planned = PredictController.alignmentBaseName(
            for: chain("A", 24), literalSequence: "MKTAYIAKQRQISFVKSHFSRQLE")
        return (c, planned)
    }

    func testAReportedFailureStopsTheBarWithTheServersReason() {
        let cmds = NSMutableArray()
        let (c, planned) = literalRunNeedingSearch(cmds)
        c.run()
        XCTAssertEqual(c.phase, .searching(remaining: 1))
        let failure = MSAFailureEntry(
            id: "msa-1", name: planned,
            error: "cannot reach the MSA server at msa.internal: Connection refused.")
        c.onEngineState(alignments: [], searches: [], failures: [failure])
        guard case let .error(message) = c.phase else {
            return XCTFail("a failed search must end the run, got \(c.phase)")
        }
        XCTAssertTrue(message.contains("cannot reach the MSA server at msa.internal"), message)
        XCTAssertEqual(cmds.count, 1)   // the search; predict was never submitted
    }

    func testAFailureFromAnEarlierRunDoesNotFailThisOne() {
        // The planned name is deterministic, so a re-run plans the SAME name as the run
        // that failed. Only a failure new since this run's searches went out counts.
        let cmds = NSMutableArray()
        let (c, planned) = literalRunNeedingSearch(cmds)
        let old = MSAFailureEntry(id: "msa-old", name: planned, error: "refused")
        c.onEngineState(alignments: [], searches: [], failures: [old])   // idle tick
        c.run()
        let running = MSASearchEntry(id: "msa-new", name: planned, phase: "search",
                                     server: "https://msa.internal", elapsed: 0)
        c.onEngineState(alignments: [], searches: [running], failures: [old])
        XCTAssertEqual(c.phase, .searching(remaining: 1))
    }

    func testAlreadySatisfiedChainSkipsSearchAndPredictsDirect() {
        // Fix A: if the alignment is already present when run() is called, no msa_search
        // is fired and predict is submitted immediately (no .searching phase).
        let cmds = NSMutableArray()
        let c = makeController(captured: cmds)
        c.loadFormPayload(PredictFormPayload(
            predictors: [PredictorInfo(id: "boltz2", msa: true)],
            chains: [chain("A", 60, obj: "1ubq", ch: "A")], error: nil))
        c.inputText = "1ubq"; c.predictor = "boltz2"; c.useMSA = true; c.msaChains = ["A"]
        // Feed the matching alignment while idle — latestAlignments is populated.
        let existing = AlignmentEntry(id: "aln", name: "predui_1ubq_A", depth: 8,
                                      columns: 60, residues: 60, target: "1ubq", chain: "A")
        c.onEngineState(alignments: [existing], searches: [])
        XCTAssertEqual(c.phase, .idle, "onEngineState while idle must not change phase")
        c.run()
        // Already satisfied → no msa_search, goes straight to predict, then back to idle.
        XCTAssertEqual(c.phase, .idle)
        XCTAssertEqual(cmds.count, 1)
        XCTAssertFalse((cmds[0] as! String).contains("msa_search"), "no search needed")
        XCTAssertTrue((cmds[0] as! String).contains("_c.predict("))
        XCTAssertFalse((cmds[0] as! String).contains("msa="), "object path: no msa= arg")
    }

    func testOversizeRaisesWarningNotSubmit() {
        let cmds = NSMutableArray()
        let c = PredictController()
        c.runPythonSeam = { cmds.add($0) }
        c.availableBytesProvider = { 4 * (1024 * 1024 * 1024) }  // small machine
        c.loadFormPayload(PredictFormPayload(
            predictors: [PredictorInfo(id: "boltz2", msa: true)],
            chains: [chain("A", 120)], error: nil))
        c.inputText = "…120 residues…"; c.predictor = "boltz2"
        c.run()
        XCTAssertNotNil(c.pendingSizeWarning)
        XCTAssertEqual(cmds.count, 0)          // nothing submitted yet
        c.confirmPendingWarning()
        XCTAssertNil(c.pendingSizeWarning)
        XCTAssertEqual(c.phase, .idle)   // confirm → submit → bar back to ready
        XCTAssertEqual(cmds.count, 1)
    }

    func testCancelDuringSearchPreventsAutoSubmit() {
        // Fix 1: cancel() during .searching must stop the state machine so that a
        // subsequently-landed alignment does NOT trigger submitPredict.
        let cmds = NSMutableArray()
        let c = makeController(captured: cmds)
        c.loadFormPayload(PredictFormPayload(
            predictors: [PredictorInfo(id: "boltz2", msa: true)],
            chains: [chain("A", 60, obj: "1ubq", ch: "A")], error: nil))
        c.inputText = "1ubq"; c.predictor = "boltz2"; c.useMSA = true; c.msaChains = ["A"]

        // Start the MSA flow — should be .searching with one msa_search command sent.
        c.run()
        XCTAssertEqual(c.phase, .searching(remaining: 1))
        XCTAssertEqual(cmds.count, 1)

        // Cancel — must send msa_cancel, clear plannedNames, and reset to idle.
        c.cancel()
        XCTAssertEqual(c.phase, .idle, "cancel must reset phase to .idle")
        XCTAssertTrue((cmds.lastObject as! String).contains("msa_cancel"),
                      "cancel must send msa_cancel command")
        let countAfterCancel = cmds.count

        // Simulate the alignment landing AFTER cancel — must not auto-submit predict.
        let landed = AlignmentEntry(id: "aln", name: "predui_1ubq_A", depth: 8,
                                    columns: 60, residues: 60, target: "1ubq", chain: "A")
        c.onEngineState(alignments: [landed], searches: [])
        XCTAssertEqual(c.phase, .idle, "landed alignment after cancel must not change phase")
        XCTAssertEqual(cmds.count, countAfterCancel, "no predict submitted after cancel")
    }

    func testPredictorSelectionDefaultsToFirst() {
        let c = PredictController()
        c.loadFormPayload(PredictFormPayload(
            predictors: [PredictorInfo(id: "boltz2", msa: true),
                         PredictorInfo(id: "protenix", msa: false)],
            chains: [], error: nil))
        XCTAssertEqual(c.predictor, "boltz2")
    }

    // MARK: MSA server dropdown (#598)

    private let colab = "https://api.colabfold.com"
    private let internalServer = "https://msa.internal"
    private let savedServer = MSAServerInfo(url: "https://msa.internal", origin: "saved",
                                            isPublic: false, error: nil)
    private let publicDefault = MSAServerInfo(url: "https://api.colabfold.com",
                                              origin: "default", isPublic: true,
                                              error: nil)
    private let unusableSaved = MSAServerInfo(url: "", origin: "", isPublic: false,
                                              error: "Error: the saved MSA server in x")

    /// A controller whose preferences live in a throwaway suite, never the app's own.
    private func makeServerController(_ cmds: NSMutableArray) -> PredictController {
        let c = makeController(captured: cmds)
        c.settings = UserDefaults(suiteName: "PredictControllerTests-\(UUID().uuidString)")!
        return c
    }

    private func payload(server: MSAServerInfo?, chains: [PredictChain]? = nil)
        -> PredictFormPayload {
        PredictFormPayload(predictors: [PredictorInfo(id: "boltz2", msa: true)],
                           chains: chains ?? [chain("A", 30)], error: nil, msaServer: server)
    }

    private func msaServerCommands(_ cmds: NSMutableArray) -> [String] {
        (cmds as? [String] ?? []).filter { $0.contains("msa_server") }
    }

    private func searchCommands(_ cmds: NSMutableArray) -> [String] {
        (cmds as? [String] ?? []).filter { $0.contains("msa_search") }
    }

    /// Ready to run an MSA search for chain A of a literal sequence.
    private func readyForSearch(_ c: PredictController, server: MSAServerInfo) {
        c.loadFormPayload(payload(server: server))
        c.inputText = "MKTAYIAKQRQISFVKSHFSRQLE"; c.predictor = "boltz2"
        c.useMSA = true; c.msaChains = ["A"]
    }

    func testTheDropdownStartsOnTheSavedDefault() {
        let c = makeServerController(NSMutableArray())
        c.loadFormPayload(payload(server: savedServer))
        XCTAssertEqual(c.selectedServer, internalServer)
        XCTAssertEqual(c.defaultServer, internalServer)
    }

    func testTheDropdownStartsOnColabFoldWhenNothingIsSaved() {
        let c = makeServerController(NSMutableArray())
        c.loadFormPayload(payload(server: publicDefault))
        XCTAssertEqual(c.selectedServer, colab)
        XCTAssertEqual(c.savedServers, [])
    }

    func testAServerSavedFromTheConsoleJoinsTheList() {
        let c = makeServerController(NSMutableArray())
        c.loadFormPayload(payload(server: savedServer))
        XCTAssertEqual(c.savedServers, [internalServer])
    }

    func testTheListOutlivesTheController() {
        let suite = UserDefaults(suiteName: "PredictControllerTests-\(UUID().uuidString)")!
        let first = makeController(captured: NSMutableArray())
        first.settings = suite
        XCTAssertNil(first.addServer("https://msa.lab:8080", makeDefault: false))
        let second = makeController(captured: NSMutableArray())
        second.settings = suite
        XCTAssertEqual(second.savedServers, ["https://msa.lab:8080"])
    }

    func testPickingAServerDoesNotChangeTheDefault() {
        let cmds = NSMutableArray()
        let c = makeServerController(cmds)
        c.loadFormPayload(payload(server: savedServer))
        c.selectedServer = colab
        XCTAssertEqual(msaServerCommands(cmds), [])
        XCTAssertEqual(c.defaultServer, internalServer)
    }

    func testAPayloadKeepsTheUsersPick() {
        // e.g. the input was edited and the form re-resolved: same default, so the pick stays.
        let c = makeServerController(NSMutableArray())
        c.loadFormPayload(payload(server: savedServer))
        c.selectedServer = colab
        c.loadFormPayload(payload(server: savedServer))
        XCTAssertEqual(c.selectedServer, colab)
    }

    func testANewDefaultIsAlsoSelected() {
        // The default changed (here: from the console), so the dropdown follows it.
        let c = makeServerController(NSMutableArray())
        c.loadFormPayload(payload(server: publicDefault))
        c.loadFormPayload(payload(server: savedServer))
        XCTAssertEqual(c.selectedServer, internalServer)
    }

    func testTheSearchGoesToThePickedServer() {
        let cmds = NSMutableArray()
        let c = makeServerController(cmds)
        readyForSearch(c, server: publicDefault)
        XCTAssertNil(c.addServer("https://msa.lab", makeDefault: false))
        c.selectedServer = "https://msa.lab"
        c.run()
        XCTAssertEqual(searchCommands(cmds).count, 1)
        XCTAssertTrue(searchCommands(cmds)[0].contains("server='https://msa.lab'"))
    }

    func testAddingAsDefaultSavesItAndSelectsIt() {
        let cmds = NSMutableArray()
        let c = makeServerController(cmds)
        var refreshed = 0
        c.refreshTrigger = { _ in refreshed += 1 }
        c.loadFormPayload(payload(server: publicDefault))
        XCTAssertNil(c.addServer("https://msa.lab/", makeDefault: true))
        XCTAssertEqual(c.savedServers, ["https://msa.lab"])
        XCTAssertEqual(c.selectedServer, "https://msa.lab")
        XCTAssertEqual(msaServerCommands(cmds),
                       ["from pymol import cmd as _c\n_c.msa_server('https://msa.lab', quiet=0)"])
        XCTAssertEqual(refreshed, 1)
    }

    func testAddingWithoutDefaultLeavesTheDefaultAlone() {
        let cmds = NSMutableArray()
        let c = makeServerController(cmds)
        c.loadFormPayload(payload(server: publicDefault))
        XCTAssertNil(c.addServer("https://msa.lab", makeDefault: false))
        XCTAssertEqual(c.savedServers, ["https://msa.lab"])
        XCTAssertEqual(c.selectedServer, "https://msa.lab")   // you added it to use it
        XCTAssertEqual(c.defaultServer, colab)
        XCTAssertEqual(msaServerCommands(cmds), [])
    }

    func testAddingTheSameServerTwiceListsItOnce() {
        let c = makeServerController(NSMutableArray())
        XCTAssertNil(c.addServer("https://msa.lab", makeDefault: false))
        XCTAssertNil(c.addServer("https://msa.lab/", makeDefault: false))
        XCTAssertEqual(c.savedServers, ["https://msa.lab"])
    }

    func testABadAddressIsRefusedAndNotListed() {
        let c = makeServerController(NSMutableArray())
        XCTAssertNotNil(c.addServer("msa.lab", makeDefault: true))
        XCTAssertEqual(c.savedServers, [])
    }

    func testColabFoldCannotBeAddedAsAPrivateServer() {
        let c = makeServerController(NSMutableArray())
        XCTAssertNotNil(c.addServer("https://api.colabfold.com", makeDefault: false))
        XCTAssertEqual(c.savedServers, [])
    }

    func testEditCanMakeColabFoldTheDefaultAgain() {
        let cmds = NSMutableArray()
        let c = makeServerController(cmds)
        c.loadFormPayload(payload(server: savedServer))
        c.setDefaultServer(colab)
        XCTAssertEqual(msaServerCommands(cmds),
                       ["from pymol import cmd as _c\n_c.msa_server('reset', quiet=0)"])
        XCTAssertEqual(c.selectedServer, colab)
    }

    func testEditCanMakeAPrivateServerTheDefault() {
        let cmds = NSMutableArray()
        let c = makeServerController(cmds)
        c.loadFormPayload(payload(server: publicDefault))
        XCTAssertNil(c.addServer("https://msa.lab", makeDefault: false))
        c.setDefaultServer("https://msa.lab")
        XCTAssertEqual(msaServerCommands(cmds),
                       ["from pymol import cmd as _c\n_c.msa_server('https://msa.lab', quiet=0)"])
    }

    func testDeletingTheDefaultFallsBackToColabFold() {
        let cmds = NSMutableArray()
        let c = makeServerController(cmds)
        c.loadFormPayload(payload(server: savedServer))
        c.removeServer(internalServer)
        XCTAssertEqual(c.savedServers, [])
        XCTAssertEqual(msaServerCommands(cmds),
                       ["from pymol import cmd as _c\n_c.msa_server('reset', quiet=0)"])
        XCTAssertEqual(c.selectedServer, colab)
    }

    func testDeletingAnotherServerLeavesTheDefaultAlone() {
        let cmds = NSMutableArray()
        let c = makeServerController(cmds)
        c.loadFormPayload(payload(server: savedServer))
        XCTAssertNil(c.addServer("https://msa.lab", makeDefault: false))
        c.removeServer("https://msa.lab")
        XCTAssertEqual(c.savedServers, [internalServer])
        XCTAssertEqual(msaServerCommands(cmds), [])
        XCTAssertEqual(c.selectedServer, internalServer)   // the pick fell back to the default
    }

    func testAnUnusableSavedServerSelectsNothingAndRefusesToSearch() {
        // Python refuses to search past a damaged saved file rather than go public; the
        // dropdown must not quietly pick ColabFold for it either.
        let cmds = NSMutableArray()
        let c = makeServerController(cmds)
        readyForSearch(c, server: unusableSaved)
        XCTAssertNil(c.selectedServer)
        c.run()
        XCTAssertEqual(cmds.count, 0)
        guard case let .error(message) = c.phase else {
            return XCTFail("expected an error, got \(c.phase)")
        }
        // Python's own reason, which says whether the saved file or RAYMOL_MSA_SERVER
        // is at fault (review on #599) -- the bar must not guess.
        XCTAssertTrue(message.contains("the saved MSA server in x"), message)
        XCTAssertFalse(message.contains("Error:"), message)
    }

    func testEditRecoversFromAnUnusableSavedServer() {
        // Review on #599: the damaged default must be fixable from the UI. Edit's
        // ColabFold row forgets it; a private row overwrites it.
        let cmds = NSMutableArray()
        let c = makeServerController(cmds)
        c.loadFormPayload(payload(server: unusableSaved))
        c.setDefaultServer(colab)
        XCTAssertEqual(msaServerCommands(cmds),
                       ["from pymol import cmd as _c\n_c.msa_server('reset', quiet=0)"])
        XCTAssertEqual(c.selectedServer, colab)
    }

    // MARK: public-server warning

    func testRunningOnColabFoldAsksBeforeSendingAnything() {
        let cmds = NSMutableArray()
        let c = makeServerController(cmds)
        readyForSearch(c, server: publicDefault)
        c.run()
        XCTAssertTrue(c.pendingPublicWarning)
        XCTAssertEqual(cmds.count, 0)
    }

    func testSendingAfterTheWarningSearchesColabFold() {
        let cmds = NSMutableArray()
        let c = makeServerController(cmds)
        readyForSearch(c, server: publicDefault)
        c.run()
        c.confirmPublicWarning(dontShowAgain: false)
        XCTAssertFalse(c.pendingPublicWarning)
        XCTAssertEqual(searchCommands(cmds).count, 1)
        XCTAssertTrue(searchCommands(cmds)[0].contains("server='https://api.colabfold.com'"))
        XCTAssertEqual(c.phase, .searching(remaining: 1))
        // Not suppressed: the next run asks again.
        cmds.removeAllObjects()
        c.cancel()
        c.run()
        XCTAssertTrue(c.pendingPublicWarning)
    }

    func testCancellingTheWarningSendsNothing() {
        let cmds = NSMutableArray()
        let c = makeServerController(cmds)
        readyForSearch(c, server: publicDefault)
        c.run()
        c.cancelPublicWarning()
        XCTAssertFalse(c.pendingPublicWarning)
        XCTAssertEqual(cmds.count, 0)
        XCTAssertEqual(c.phase, .idle)
    }

    func testDontShowAgainStopsTheWarning() {
        let cmds = NSMutableArray()
        let c = makeServerController(cmds)
        readyForSearch(c, server: publicDefault)
        c.run()
        c.confirmPublicWarning(dontShowAgain: true)
        c.cancel()
        cmds.removeAllObjects()
        c.run()
        XCTAssertFalse(c.pendingPublicWarning)
        XCTAssertEqual(searchCommands(cmds).count, 1)
    }

    func testAPrivateServerNeverWarns() {
        let cmds = NSMutableArray()
        let c = makeServerController(cmds)
        readyForSearch(c, server: savedServer)
        c.run()
        XCTAssertFalse(c.pendingPublicWarning)
        XCTAssertEqual(searchCommands(cmds).count, 1)
    }

    func testNoWarningWhenNoSequenceWouldBeSent() {
        // The chain's alignment is already attached: nothing is searched, nothing leaves.
        let cmds = NSMutableArray()
        let c = makeServerController(cmds)
        c.loadFormPayload(payload(server: publicDefault,
                                  chains: [chain("A", 60, obj: "1ubq", ch: "A")]))
        c.inputText = "1ubq"; c.predictor = "boltz2"; c.useMSA = true; c.msaChains = ["A"]
        c.onEngineState(alignments: [AlignmentEntry(id: "aln", name: "x", depth: 8,
                                                    columns: 60, residues: 60,
                                                    target: "1ubq", chain: "A")],
                        searches: [])
        c.run()
        XCTAssertFalse(c.pendingPublicWarning)
        XCTAssertEqual(searchCommands(cmds), [])
    }

    func testNoWarningWithoutMSA() {
        let cmds = NSMutableArray()
        let c = makeServerController(cmds)
        readyForSearch(c, server: publicDefault)
        c.useMSA = false
        c.run()
        XCTAssertFalse(c.pendingPublicWarning)
        XCTAssertEqual(cmds.count, 1)   // the predict itself
    }
}
#endif
