import Foundation

/// A `.env` file that can't be trusted to import (issue #226): the whole file is rejected
/// rather than salvaging the lines that do parse, so an import either fully previews and
/// applies or leaves the launch environment untouched -- never a partial, surprising
/// subset. Deliberately carries only a line number, never the line's own text (contents
/// are never logged, and an error message is exactly the kind of place that could leak).
public enum DotEnvParseError: Error, Equatable, Sendable {
    case malformedLine(number: Int)
    /// The file could not be read at all -- missing, a directory, unreadable permissions,
    /// or not valid UTF-8. Deliberately collapses every read failure into one case,
    /// carrying no filesystem detail that might embed a path fragment worth not logging.
    case unreadable
}

/// Pure `.env` line parsing (issue #226, ADR-0044's one-shot import): turns file contents
/// into a flat key/value dictionary. Deliberately minimal -- Blindfold's own `.env` files
/// are `KEY=VALUE` lines, not a general shell-sourcing target, so this does not attempt to
/// replicate every `dotenv` convention.
public enum DotEnvParser {
    public static func parse(_ contents: String) throws -> [String: String] {
        var result: [String: String] = [:]
        for (index, line) in contents.components(separatedBy: .newlines).enumerated() {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard !trimmed.isEmpty, !trimmed.hasPrefix("#") else { continue }
            var working = trimmed
            if working.hasPrefix("export ") {
                working = String(working.dropFirst("export ".count)).trimmingCharacters(in: .whitespaces)
            }
            guard let eqIndex = working.firstIndex(of: "=") else {
                throw DotEnvParseError.malformedLine(number: index + 1)
            }
            let key = String(working[working.startIndex..<eqIndex])
            let rawValue = String(working[working.index(after: eqIndex)...])
            result[key] = stripMatchingSurroundingQuotes(rawValue)
        }
        return result
    }

    private static func stripMatchingSurroundingQuotes(_ value: String) -> String {
        guard value.count >= 2, let first = value.first, let last = value.last, first == last else {
            return value
        }
        guard first == "\"" || first == "'" else { return value }
        return String(value.dropFirst().dropLast())
    }
}

/// The preview of a `.env` one-shot import (issue #226, ADR-0044): "show what will change
/// before applying it" made into data, so the settings surface can render it without
/// re-deriving any classification rule. Never applied on its own -- `DotEnvImport.apply`
/// is the only thing that writes to a store, and only after this preview has been shown.
public struct DotEnvImportPlan: Equatable, Sendable {
    /// Where an imported key's value will be written.
    public enum Destination: Equatable, Sendable {
        case settings
        case secret
    }

    /// One key the file held that Blindfold recognizes and will write on `apply`.
    public struct Entry: Equatable, Sendable {
        public let key: String
        public let newValue: String
        /// The value currently held for this key, if any -- so the preview can show an
        /// overwrite, not just an addition.
        public let previousValue: String?
        public let destination: Destination

        public init(key: String, newValue: String, previousValue: String?, destination: Destination) {
            self.key = key
            self.newValue = newValue
            self.previousValue = previousValue
            self.destination = destination
        }
    }

    public var entries: [Entry]
    /// `BLINDFOLD_*` keys the file held that Blindfold doesn't recognize -- surfaced so an
    /// import never silently swallows a typo or a future key this version doesn't know
    /// about yet, and never written on `apply`.
    public var unknownKeys: [String]
    /// Legacy `BLINDFOLD_OLLAMA_*` keys the file held (ADR-0031, ADR-0044): flagged for
    /// the operator's attention but never written on `apply` -- importing one would
    /// reintroduce the legacy-variable hard refusal through the one door #220 left open.
    public var legacyKeys: [String]
    /// `BLINDFOLD_DATABASE_URL`'s value, if the file held one -- deliberately not an
    /// ordinary `Entry` (issue #226: "must be a visible, deliberate choice rather than a
    /// side effect of importing L3 settings"). `apply` only writes it when the caller
    /// passes a distinct, explicit confirmation.
    public var databaseURLValue: String?

    public init(
        entries: [Entry] = [],
        unknownKeys: [String] = [],
        legacyKeys: [String] = [],
        databaseURLValue: String? = nil
    ) {
        self.entries = entries
        self.unknownKeys = unknownKeys
        self.legacyKeys = legacyKeys
        self.databaseURLValue = databaseURLValue
    }
}

/// The one-shot `.env` import's classification step (issue #226, ADR-0044): decides, for
/// each `BLINDFOLD_*` key a file held, where it belongs -- never writes anything itself.
public enum DotEnvImport {
    /// The one field that moves an install off ADR-0043's SQLite default (issue #226):
    /// never bundled into an ordinary `Entry`, always its own distinct confirmation.
    public static let databaseURLKey = "BLINDFOLD_DATABASE_URL"

    /// Reads and parses a chosen `.env` file (issue #226's "malformed or unreadable file
    /// fails cleanly" AC): any read failure -- missing file, unreadable permissions,
    /// invalid encoding -- becomes `.unreadable` rather than propagating a raw Foundation
    /// error that might carry a path or the attempted contents into a log.
    public static func readFileValues(contentsOf url: URL) throws -> [String: String] {
        let contents: String
        do {
            contents = try String(contentsOf: url, encoding: .utf8)
        } catch {
            throw DotEnvParseError.unreadable
        }
        return try DotEnvParser.parse(contents)
    }

    /// Classifies `fileValues` (already parsed, `BLINDFOLD_*` keys only expected) against
    /// `currentValues` (the launch environment's held values plus the two known secrets,
    /// merged -- their key sets never collide) to build the preview.
    public static func plan(
        fileValues: [String: String],
        currentValues: [String: String]
    ) -> DotEnvImportPlan {
        var entries: [DotEnvImportPlan.Entry] = []
        var unknownKeys: [String] = []
        var legacyKeys: [String] = []
        var databaseURLValue: String?
        for (key, value) in fileValues {
            guard key.hasPrefix(LaunchEnvironment.blindfoldPrefix) else { continue }
            let destination: DotEnvImportPlan.Destination
            if key == databaseURLKey {
                databaseURLValue = value
                continue
            } else if SupervisorSettingsValidation.legacyOllamaEnvVarNames.contains(key) {
                legacyKeys.append(key)
                continue
            } else if SupervisorSettings.isKnownKey(key) {
                destination = .settings
            } else if SupervisorSecrets.isKnownKey(key) {
                destination = .secret
            } else {
                unknownKeys.append(key)
                continue
            }
            entries.append(
                DotEnvImportPlan.Entry(
                    key: key,
                    newValue: value,
                    previousValue: currentValues[key],
                    destination: destination
                )
            )
        }
        return DotEnvImportPlan(
            entries: entries,
            unknownKeys: unknownKeys,
            legacyKeys: legacyKeys,
            databaseURLValue: databaseURLValue
        )
    }

    /// Applies a previewed plan (issue #226): every ordinary entry is written to its
    /// destination store, and `BLINDFOLD_DATABASE_URL` only when `importDatabaseURL` is
    /// `true` -- the distinct, explicit confirmation the issue requires. Never called on
    /// a plan the caller hasn't shown to the operator first.
    public static func apply(
        _ plan: DotEnvImportPlan,
        importDatabaseURL: Bool,
        into store: LaunchEnvironmentStore,
        secretsStore: SecretsStoring
    ) {
        for entry in plan.entries {
            switch entry.destination {
            case .settings:
                store.setValue(entry.newValue, for: entry.key)
            case .secret:
                secretsStore.setValue(entry.newValue, for: entry.key)
            }
        }
        if importDatabaseURL, let databaseURLValue = plan.databaseURLValue {
            store.setValue(databaseURLValue, for: databaseURLKey)
        }
    }
}
