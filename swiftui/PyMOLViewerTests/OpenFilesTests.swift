import XCTest
@testable import RayMol

// Regression tests for issue #222: `open a.pdb b.pdb c.pdb` from a Terminal (and
// Finder "Open With" multi-select / Dock-icon drop) only loaded the first file.
// macOS delivers all of them via NSApplicationDelegate.application(_:open:) as one
// [URL] array; RayMolAppDelegate forwards that array through handleOpenedURLs.
// These lock in that EVERY url is forwarded — a spy stands in for the real
// loadOpenedFile so the engine/PyMOL core never has to boot.
@MainActor
final class OpenFilesTests: XCTestCase {

    func testAllOpenedURLsAreForwardedInOrder() {
        let urls = ["a.pdb", "b.pdb", "c.pdb"].map { URL(fileURLWithPath: "/tmp/\($0)") }
        var seen: [String] = []
        handleOpenedURLs(urls, into: PyMOLEngine.shared) { url, _ in
            seen.append(url.lastPathComponent)
        }
        // The bug loaded only "a.pdb"; the fix must forward all three, in order.
        XCTAssertEqual(seen, ["a.pdb", "b.pdb", "c.pdb"])
    }

    func testSingleOpenedURLStillForwarded() {
        // A plain Finder double-click delivers a one-element array; it must still
        // load (the delegate now handles single- and multi-file opens uniformly).
        let urls = [URL(fileURLWithPath: "/tmp/only.pse")]
        var seen: [String] = []
        handleOpenedURLs(urls, into: PyMOLEngine.shared) { url, _ in
            seen.append(url.lastPathComponent)
        }
        XCTAssertEqual(seen, ["only.pse"])
    }

    func testEmptyURLListIsNoOp() {
        var called = false
        handleOpenedURLs([], into: PyMOLEngine.shared) { _, _ in called = true }
        XCTAssertFalse(called)
    }

    // MARK: - Issue #272: sessions load bare — no object name, no theming

    func testSessionLoadHasNoObjectNameAndNoTheme() {
        // `load foo.pse, name` creates nothing named `name` (the session restores
        // its own objects), so the old theming call spammed `Invalid selection
        // name` on every session open — and would have clobbered the session's
        // saved colors/reps had the name resolved.
        let (command, theme) = PyMOLEngine.loadInvocation(
            path: "/tmp/open_E277B7EE.pse", name: "top6_candidates")
        XCTAssertEqual(command, "load /tmp/open_E277B7EE.pse")
        XCTAssertNil(theme)
    }

    func testSessionExtensionMatchIsCaseInsensitiveAndCoversPsw() {
        for path in ["/tmp/a.PSE", "/tmp/b.psw", "/tmp/c.Psw"] {
            let (command, theme) = PyMOLEngine.loadInvocation(path: path, name: "x")
            XCTAssertEqual(command, "load \(path)", path)
            XCTAssertNil(theme, path)
        }
    }

    func testCoordinateLoadKeepsObjectNameAndTheme() {
        let (command, theme) = PyMOLEngine.loadInvocation(
            path: "/tmp/5hbh.pdb", name: "5hbh")
        XCTAssertEqual(command, "load /tmp/5hbh.pdb, 5hbh")
        XCTAssertEqual(theme,
            "from pymol import raymol_theme as _rt; _rt.apply_to('5hbh')")
    }

    // MARK: - Issue #349: a .pse open must not silently wipe a non-empty session

    func testSessionOpenOverNonEmptySessionNeedsConfirmation() {
        let pse = URL(fileURLWithPath: "/tmp/other.pse")
        XCTAssertTrue(openWouldReplaceSession(pse, hasObjects: true))
    }

    func testSessionOpenIntoEmptySessionNeedsNoConfirmation() {
        // Cold launch from a Finder double-click: nothing to lose, no prompt.
        let pse = URL(fileURLWithPath: "/tmp/other.pse")
        XCTAssertFalse(openWouldReplaceSession(pse, hasObjects: false))
    }

    func testCoordinateOpenNeverPrompts() {
        // A .pdb/.cif ADDS an object — it doesn't replace the session.
        let pdb = URL(fileURLWithPath: "/tmp/5hbh.pdb")
        XCTAssertFalse(openWouldReplaceSession(pdb, hasObjects: true))
    }

    #if os(macOS)
    private func recentSessionFixtures() throws -> (URL, URL) {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("run_261005_open_recent/test-fixtures/\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let first = root.appendingPathComponent("first session.pse")
        let second = root.appendingPathComponent("second.PSW")
        try Data().write(to: first)
        try Data().write(to: second)
        return (first, second)
    }

    func testRecentSessionsPublishNativeHistoryAndKeepOriginalURLs() throws {
        let engine = PyMOLEngine.shared
        defer { engine.refreshRecentSessions() }
        let controller = RecentDocumentSpy()
        let (first, second) = try recentSessionFixtures()
        engine.rememberRecentSession(first, using: controller)
        engine.rememberRecentSession(second, using: controller)
        XCTAssertEqual(engine.recentSessionURLs, [second, first])
        engine.rememberRecentSession(first, using: controller)
        XCTAssertEqual(engine.recentSessionURLs, [first, second])
    }

    func testRecentSessionsIgnoreCoordinatesAndRemoteURLs() {
        let engine = PyMOLEngine.shared
        defer { engine.refreshRecentSessions() }
        let controller = RecentDocumentSpy()
        engine.rememberRecentSession(URL(fileURLWithPath: "/sessions/structure.pdb"), using: controller)
        engine.rememberRecentSession(URL(string: "https://example.com/session.pse")!, using: controller)
        engine.rememberRecentSession(URL(fileURLWithPath: "/sessions/missing.pse"), using: controller)
        XCTAssertEqual(controller.recentDocumentURLs, [])
    }

    func testRecentSessionsReadExistingHistoryAndFilterCoordinateFiles() {
        let engine = PyMOLEngine.shared
        defer { engine.refreshRecentSessions() }
        let controller = RecentDocumentSpy()
        let session = URL(fileURLWithPath: "/sessions/previous.pse")
        controller.urls = [session, URL(fileURLWithPath: "/sessions/structure.pdb")]
        engine.refreshRecentSessions(using: controller)
        XCTAssertEqual(engine.recentSessionURLs, [session])
    }

    func testClearRecentSessionsClearsOnlyHistoryNotCurrentDocument() throws {
        let engine = PyMOLEngine.shared
        defer { engine.refreshRecentSessions() }
        let original = engine.currentSessionURL
        let controller = RecentDocumentSpy()
        let (session, _) = try recentSessionFixtures()
        engine.rememberRecentSession(session, using: controller)
        engine.clearRecentSessions(using: controller)
        XCTAssertTrue(controller.cleared)
        XCTAssertEqual(engine.recentSessionURLs, [])
        XCTAssertEqual(engine.currentSessionURL, original)
    }
    #endif
}

#if os(macOS)
private final class RecentDocumentSpy: RecentSessionHistory {
    var urls: [URL] = []
    var cleared = false
    var recentDocumentURLs: [URL] { urls }
    func noteNewRecentDocumentURL(_ url: URL) {
        urls.removeAll { $0 == url }
        urls.insert(url, at: 0)
    }
    func clearRecentDocuments(_ sender: Any?) {
        cleared = true
        urls.removeAll()
    }
}
#endif
