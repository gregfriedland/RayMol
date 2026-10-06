#if os(macOS)
import Foundation
import CoreFoundation
import Darwin
import CryptoKit

struct EnergyReply: Sendable {
    let generation: UInt64
    let request: UInt64
    let kind: String
    let payload: Data

    static func decode(_ data: Data) throws -> EnergyReply {
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(object.keys) == ["version", "generation", "request", "kind", "payload"],
              integer(object["version"]) == 1,
              let generation = integer(object["generation"]),
              let request = integer(object["request"]),
              let kind = object["kind"] as? String,
              ["started", "stage", "result", "cancelled", "error"].contains(kind),
              let payload = object["payload"] as? [String: Any] else {
            throw EnergyProtocolError.invalidMessage
        }
        return EnergyReply(generation: generation, request: request, kind: kind,
                           payload: try JSONSerialization.data(withJSONObject: payload))
    }

    private static func integer(_ value: Any?) -> UInt64? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              let value = UInt64(number.stringValue), value > 0, value <= 9_007_199_254_740_991 else { return nil }
        return value
    }
}

enum EnergyProtocolError: Error { case invalidMessage, busy, unavailable, stopDeadline }

// Foundation-only owner. App/UI integration must use this same instance and await stop().
actor EnergyAnalysisController {
    static let maxMessageBytes = 262_144
    private var process: Process?
    private var input: FileHandle?
    private var reader: Task<Void, Never>?
    private var readerFinished: UInt64 = 0
    private var readerEpoch: UInt64 = 0
    private var stopping = false
    private var buffer = Data()
    private var active: UInt64?
    private var cancelling: UInt64?
    private var nextRequest: UInt64 = 0
    private(set) var generation: UInt64 = 0
    private(set) var published: EnergyReply?
    private(set) var lastStarted: UInt64?
    private(set) var failure: String?
    private(set) var stageEvents: [EnergyReply] = []
    private(set) var componentStates: [String: String] = [:]
    private var stageSequence: UInt64 = 0
    private var acceptedIdentity: [String: String]?
    private var expectedInputHash: String?
    private var expectedSceneRevision: String?
    private var expectedAnalysisID: String?
    private var queued: (operation: String, payload: Data)?
    private var launch: (executable: URL, arguments: [String], environment: [String: String])?

    func start(executable: URL, arguments: [String], environment: [String: String]) throws {
        guard !stopping, process == nil, reader == nil else { throw EnergyProtocolError.busy }
        generation += 1
        let epoch = generation
        let child = Process(), incoming = Pipe(), outgoing = Pipe()
        child.executableURL = executable
        child.arguments = arguments
        child.environment = environment
        child.environment?["PYTHONDONTWRITEBYTECODE"] = "1"
        child.standardInput = incoming
        child.standardOutput = outgoing
        child.standardError = FileHandle.standardError
        let output = outgoing.fileHandleForReading
        let descriptor = output.fileDescriptor
        guard fcntl(descriptor, F_SETFL, fcntl(descriptor, F_GETFL) | O_NONBLOCK) != -1 else {
            throw EnergyProtocolError.unavailable
        }
        try child.run()
        launch = (executable, arguments, environment)
        process = child
        input = incoming.fileHandleForWriting
        active = nil; cancelling = nil; published = nil; lastStarted = nil; failure = nil
        buffer.removeAll(keepingCapacity: true)
        stageEvents.removeAll(); componentStates.removeAll(); stageSequence = 0
        acceptedIdentity = nil; expectedInputHash = nil; expectedSceneRevision = nil; expectedAnalysisID = nil
        readerEpoch = epoch
        reader = Task.detached {
            defer { try? output.close() }
            var bytes = Data(count: 65_536)
            while !Task.isCancelled {
                let count = bytes.withUnsafeMutableBytes { Darwin.read(descriptor, $0.baseAddress, $0.count) }
                if count > 0 { await self.receive(Data(bytes.prefix(count)), epoch: epoch) }
                else if count == 0 || (errno != EAGAIN && errno != EINTR) { break }
                else { try? await Task.sleep(nanoseconds: 10_000_000) }
            }
            if !Task.isCancelled { await self.eof(epoch: epoch) }
            await self.readerEnded(epoch: epoch)
        }
    }

    func ensureRunning(executable: URL, arguments: [String], environment: [String: String]) async throws {
        let deadline = Date().addingTimeInterval(4)
        while stopping && Date() < deadline { try await Task.sleep(nanoseconds: 10_000_000) }
        guard !stopping else { throw EnergyProtocolError.stopDeadline }
        if process?.isRunning == true, failure == nil { return }
        let expected = generation + 1
        try await stop()
        try Task.checkCancellation()
        guard generation == expected else { throw EnergyProtocolError.unavailable }
        try start(executable: executable, arguments: arguments, environment: environment)
    }

    private func readerEnded(epoch: UInt64) { readerFinished = max(readerFinished, epoch) }

    @discardableResult
    func submit(operation: String, payload: Data = Data("{}".utf8)) throws -> UInt64 {
        guard !stopping, process?.isRunning == true, failure == nil else { throw EnergyProtocolError.unavailable }
        guard active == nil, cancelling == nil else { throw EnergyProtocolError.busy }
        guard ["inspect", "charge", "minimize", "tiles", "analyze", "release", "ligand_input"].contains(operation) else { throw EnergyProtocolError.invalidMessage }
        nextRequest += 1
        guard nextRequest <= 9_007_199_254_740_991 else { throw EnergyProtocolError.unavailable }
        let values = try JSONSerialization.jsonObject(with: payload)
        guard values is [String: Any] else { throw EnergyProtocolError.invalidMessage }
        if operation == "analyze" {
            guard let object = values as? [String: Any],
                  let descriptor = object["snapshot"] as? [String: Any],
                  let hash = descriptor["sha256"] as? String,
                  let directory = object["directory"] as? String,
                  let relative = descriptor["path"] as? String else { throw EnergyProtocolError.invalidMessage }
            let root = URL(fileURLWithPath: directory).resolvingSymlinksInPath()
            let path = root.appendingPathComponent(relative).resolvingSymlinksInPath()
            guard path.path.hasPrefix(root.path + "/") else { throw EnergyProtocolError.invalidMessage }
            let bytes = try Data(contentsOf: path)
            guard SHA256.hash(data: bytes).map({ String(format: "%02x", $0) }).joined() == hash,
                  (descriptor["bytes"] as? NSNumber)?.intValue == bytes.count,
                  let snapshot = try JSONSerialization.jsonObject(with: bytes) as? [String: Any],
                  let revision = snapshot["scene_revision"] as? String,
                  let identifier = snapshot["analysis_id"] as? String, identifier == root.lastPathComponent else { throw EnergyProtocolError.invalidMessage }
            expectedInputHash = hash; expectedSceneRevision = revision; expectedAnalysisID = identifier
        } else { expectedInputHash = nil; expectedSceneRevision = nil; expectedAnalysisID = nil }
        stageEvents.removeAll(); componentStates.removeAll(); stageSequence = 0; acceptedIdentity = nil
        active = nextRequest; published = nil; lastStarted = nil
        do { try send(kind: "run", request: nextRequest, operation: operation, payload: values) }
        catch { active = nil; throw error }
        return nextRequest
    }

    // Replacement is a new explicit request, not a retry of failed science.
    func submitLatest(operation: String, payload: Data) async throws {
        guard !stopping else { throw EnergyProtocolError.unavailable }
        if active != nil || cancelling != nil {
            queued = (operation, payload)
            stageEvents.removeAll(); componentStates.removeAll(); acceptedIdentity = nil
            if active != nil { try cancelActive() }
        } else { try submit(operation: operation, payload: payload) }
    }

    func invalidateScene() throws {
        expectedSceneRevision = nil; acceptedIdentity = nil
        stageEvents.removeAll(); componentStates.removeAll()
        queued = nil
        try cancel()
    }

    func cancel() throws {
        queued = nil
        try cancelActive()
    }

    private func cancelActive() throws {
        guard let request = active else { return }
        active = nil; published = nil; cancelling = request
        let epoch = generation
        try send(kind: "cancel", request: request)
        Task {
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            await self.forceCancelled(epoch: epoch, request: request)
        }
    }

    func stop() async throws {
        generation += 1
        if stopping {
            let deadline = Date().addingTimeInterval(4)
            while stopping && Date() < deadline { try await Task.sleep(nanoseconds: 10_000_000) }
            guard !stopping else { throw EnergyProtocolError.stopDeadline }
            return
        }
        stopping = true
        defer { stopping = false }
        active = nil; cancelling = nil; published = nil
        queued = nil; stageEvents.removeAll(); componentStates.removeAll(); acceptedIdentity = nil
        try? input?.close(); input = nil
        let child = process, oldReader = reader, oldReaderEpoch = readerEpoch
        oldReader?.cancel()
        if child?.isRunning == true { child?.terminate() }
        let deadline = Date().addingTimeInterval(2)
        while child?.isRunning == true && Date() < deadline { try? await Task.sleep(nanoseconds: 10_000_000) }
        if let child, child.isRunning { Darwin.kill(child.processIdentifier, SIGKILL) }
        let killDeadline = Date().addingTimeInterval(1)
        while child?.isRunning == true && Date() < killDeadline { try? await Task.sleep(nanoseconds: 10_000_000) }
        guard child?.isRunning != true else { failure = "Helper stop deadline"; throw EnergyProtocolError.stopDeadline }
        let readerDeadline = Date().addingTimeInterval(1)
        while oldReader != nil && readerFinished < oldReaderEpoch && Date() < readerDeadline {
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        guard oldReader == nil || readerFinished >= oldReaderEpoch else {
            failure = "Helper reader stop deadline"; throw EnergyProtocolError.stopDeadline
        }
        reader = nil; process = nil; buffer.removeAll()
    }

    private func send(kind: String, request: UInt64, operation: String? = nil, payload: Any = [:]) throws {
        var message: [String: Any] = ["version": 1, "generation": generation, "request": request, "kind": kind, "payload": payload]
        if let operation { message["operation"] = operation }
        var bytes = try JSONSerialization.data(withJSONObject: message)
        bytes.append(10)
        guard bytes.count <= Self.maxMessageBytes, let input else { throw EnergyProtocolError.invalidMessage }
        try input.write(contentsOf: bytes)
    }

    private func receive(_ data: Data, epoch: UInt64) async {
        guard !stopping, epoch == generation, failure == nil else { return }
        buffer.append(data)
        do {
            while let newline = buffer.firstIndex(of: 10) {
                let line = buffer.prefix(upTo: newline)
                guard line.count + 1 <= Self.maxMessageBytes else { throw EnergyProtocolError.invalidMessage }
                let reply = try EnergyReply.decode(Data(line))
                buffer.removeSubrange(...newline)
                guard reply.generation == generation else { continue }
                if reply.request == cancelling {
                    if ["result", "cancelled", "error"].contains(reply.kind) {
                        cancelling = nil
                        try await drainQueue()
                        guard !stopping, epoch == generation else { return }
                    }
                    continue
                }
                guard reply.request == active else { continue }
                if reply.kind == "started" { lastStarted = reply.request; continue }
                if reply.kind == "stage" { try acceptStage(reply); continue }
                if reply.kind == "result", expectedInputHash != nil {
                    guard let payload = try JSONSerialization.jsonObject(with: reply.payload) as? [String: Any],
                          let identity = acceptedIdentity,
                          identity.allSatisfy({ payload[$0.key] as? String == $0.value }),
                          let states = payload["statuses"] as? [String: String], states == componentStates,
                          Set(states.keys) == ["prepared", "direct", "global_solvation", "local_solvation", "strain"],
                          states.values.allSatisfy({ ["ready", "failed", "unavailable"].contains($0) }),
                          payload["complete"] as? Bool == states.values.allSatisfy({ $0 == "ready" }) else {
                        throw EnergyProtocolError.invalidMessage
                    }
                }
                active = nil
                if reply.kind == "result" { published = reply }
                if reply.kind == "error" {
                    guard let values = try JSONSerialization.jsonObject(with: reply.payload) as? [String: Any],
                          let message = values["message"] as? String, !message.isEmpty else { throw EnergyProtocolError.invalidMessage }
                    failure = message
                }
                try await drainQueue()
                guard !stopping, epoch == generation else { return }
            }
            guard buffer.count < Self.maxMessageBytes else { throw EnergyProtocolError.invalidMessage }
        } catch { await protocolFailure("Malformed or oversized reply", epoch: epoch) }
    }

    private func eof(epoch: UInt64) async {
        guard !stopping, epoch == generation else { return }
        if queued != nil {
            do { try await restartQueued(); return }
            catch { failure = "Cannot start replacement helper: \(error)" }
        }
        await protocolFailure(buffer.isEmpty ? "Helper exited" : "Truncated reply", epoch: epoch)
    }

    private func acceptStage(_ reply: EnergyReply) throws {
        let order = ["prepared", "direct", "global_solvation", "local_solvation", "strain"]
        guard let payload = try JSONSerialization.jsonObject(with: reply.payload) as? [String: Any],
              let sequence = payload["sequence"] as? NSNumber,
              CFGetTypeID(sequence) != CFBooleanGetTypeID(),
              let number = UInt64(sequence.stringValue), number == stageSequence + 1,
              let stage = payload["stage"] as? String, let index = order.firstIndex(of: stage),
              let status = payload["status"] as? String,
              ["pending", "ready", "failed", "unavailable"].contains(status),
              let input = expectedInputHash, let revision = expectedSceneRevision,
              payload["input_hash"] as? String == input,
              payload["scene_revision"] as? String == revision,
              payload["analysis_id"] as? String == expectedAnalysisID else { throw EnergyProtocolError.invalidMessage }
        let keys = ["analysis_id", "input_hash", "scene_revision", "model_hash", "endpoint_hash"]
        var identity: [String: String] = [:]
        for key in keys {
            guard let value = payload[key] as? String, !value.isEmpty else { throw EnergyProtocolError.invalidMessage }
            identity[key] = value
        }
        if stage == "prepared" {
            guard number == 1, status == "ready", acceptedIdentity == nil else { throw EnergyProtocolError.invalidMessage }
            acceptedIdentity = identity
        } else {
            guard identity == acceptedIdentity,
                  order.prefix(index).allSatisfy({ ["ready", "failed", "unavailable"].contains(componentStates[$0] ?? "") }),
                  (status == "pending" && componentStates[stage] == nil) ||
                  (status != "pending" && componentStates[stage] == "pending") else { throw EnergyProtocolError.invalidMessage }
        }
        stageSequence = number; componentStates[stage] = status; stageEvents.append(reply)
    }

    private func drainQueue() async throws {
        guard let next = queued, active == nil, cancelling == nil else { return }
        queued = nil
        try submit(operation: next.operation, payload: next.payload)
    }

    private func restartQueued() async throws {
        guard let next = queued, let launch else { return }
        queued = nil
        // EOF is called by reader: never await that task from itself.
        reader = nil
        let expected = generation + 1
        try await stop()
        guard generation == expected else { return }
        try start(executable: launch.executable, arguments: launch.arguments, environment: launch.environment)
        try submit(operation: next.operation, payload: next.payload)
    }

    private func protocolFailure(_ message: String, epoch: UInt64) async {
        guard !stopping, epoch == generation else { return }
        active = nil; published = nil; failure = message
        guard let child = process, child.isRunning else { return }
        child.terminate()
        Task {
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            if child.isRunning { Darwin.kill(child.processIdentifier, SIGKILL) }
        }
    }

    private func forceCancelled(epoch: UInt64, request: UInt64) async {
        guard epoch == generation, cancelling == request, let child = process else { return }
        if child.isRunning { child.terminate() }
        // Leave time to observe exit inside the one-second forced-stop window.
        try? await Task.sleep(nanoseconds: 100_000_000)
        if child.isRunning { Darwin.kill(child.processIdentifier, SIGKILL) }
    }
}

#if DEBUG
// Opt-in acceptance hook, not a second transport or user-facing analysis UI.
enum EnergyControllerPilot {
    @MainActor static func run(output: String, sandbox: String) async {
        let controller = EnergyAnalysisController()
        var result: [String: Any] = ["status": "failed"]
        do {
            guard let resources = Bundle.main.resourceURL else { throw EnergyProtocolError.unavailable }
            let helper = resources.appendingPathComponent("energy-helper/bin/python")
            var environment = ProcessInfo.processInfo.environment
            environment.removeValue(forKey: "PYTHONPATH")
            environment.removeValue(forKey: "PYTHONHOME")
            environment["PYTHONNOUSERSITE"] = "1"
            environment["OMP_NUM_THREADS"] = "4"
            environment["OPENBLAS_NUM_THREADS"] = "1"
            try await controller.start(executable: URL(fileURLWithPath: "/usr/bin/sandbox-exec"),
                                       arguments: ["-f", sandbox, helper.path, "-m", "raymol_energy.worker"],
                                       environment: environment)
            try await controller.submit(operation: "inspect")
            let deadline = Date().addingTimeInterval(60)
            while await controller.published == nil {
                if let failure = await controller.failure {
                    result["helper_failure"] = failure
                    throw EnergyProtocolError.unavailable
                }
                if Date() >= deadline {
                    result["deadline"] = "inspect: 60 seconds"
                    throw EnergyProtocolError.unavailable
                }
                try await Task.sleep(nanoseconds: 10_000_000)
            }
            let reply = await controller.published!
            let inspection = try JSONSerialization.jsonObject(with: reply.payload) as! [String: Any]
            let request = try await controller.submit(operation: "tiles", payload: Data("{\"count\":100000}".utf8))
            let startedDeadline = Date().addingTimeInterval(5)
            while await controller.lastStarted != request {
                if Date() >= startedDeadline { throw EnergyProtocolError.unavailable }
                try await Task.sleep(nanoseconds: 1_000_000)
            }
            let start = Date()
            try await controller.cancel()
            let acknowledgement = Date().timeIntervalSince(start)
            guard acknowledgement < 0.1 else { throw EnergyProtocolError.stopDeadline }
            try await Task.sleep(nanoseconds: 200_000_000)
            guard await controller.published == nil else { throw EnergyProtocolError.invalidMessage }
            try await controller.stop()
            result = ["status": "passed", "bundle": Bundle.main.bundlePath,
                      "inspection": inspection, "main_actor_cancel_ack_seconds": acknowledgement]
        } catch {
            try? await controller.stop()
            result["error"] = String(describing: error)
        }
        do {
            try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
                .write(to: URL(fileURLWithPath: output), options: .atomic)
        } catch { FileHandle.standardError.write(Data("Pilot report error: \(error)\n".utf8)) }
    }
}
#endif
#endif
